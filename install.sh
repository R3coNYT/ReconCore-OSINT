#!/usr/bin/env sh
# =============================================================================
# ReconCore OSINT - one-command installer.
#
#   curl -fsSL https://raw.githubusercontent.com/<owner>/<repo>/main/install.sh | sh
#
# or, from an existing clone:
#
#   ./install.sh
#
# It clones the repository if needed, generates the secrets, builds and starts
# the stack, creates the schema and the administrator account, and enables the
# plugins you choose. Running it twice is safe: nothing already in place is
# overwritten.
#
# Options (all optional, useful for unattended installs):
#   --email <address>       administrator email
#   --password <password>   administrator password (12+ chars, mixed case,
#                           digit, symbol). Prefer the interactive prompt.
#   --port <port>           HTTP port to publish (default 8080)
#   --plugins <list>        comma-separated, or "none" (default:
#                           sherlock,holehe,phoneinfoga,websearch)
#   --dir <path>            clone target directory (default ./reconcore-osint)
#   --repo <url>            repository to clone
#   --project-name <name>   compose project name (default: directory name)
#   --reset-data            wipe an orphaned database volume before install
#   --no-build              skip `docker compose build`
#   --yes                   never prompt; requires --email and --password
# =============================================================================
set -eu

REPO_URL="${RECONCORE_REPO:-https://github.com/R3coNYT/ReconCore-OSINT.git}"
TARGET_DIR=""
ADMIN_EMAIL=""
ADMIN_PASSWORD=""
HTTP_PORT=""
PLUGINS=""
ASSUME_YES=0
DO_BUILD=1
PROJECT_NAME=""
RESET_DATA=0
ADOPT_VOLUME=0
DEFAULT_PLUGINS="sherlock,holehe,phoneinfoga,websearch"

RED=''; GREEN=''; YELLOW=''; BLUE=''; BOLD=''; OFF=''
if [ -t 1 ]; then
  RED=$(printf '\033[31m'); GREEN=$(printf '\033[32m'); YELLOW=$(printf '\033[33m')
  BLUE=$(printf '\033[36m'); BOLD=$(printf '\033[1m'); OFF=$(printf '\033[0m')
fi

say()  { printf '%s\n' "$*"; }
step() { printf '\n%s==>%s %s%s%s\n' "$BLUE" "$OFF" "$BOLD" "$*" "$OFF"; }
ok()   { printf '  %sok%s   %s\n' "$GREEN" "$OFF" "$*"; }
warn() { printf '  %swarn%s %s\n' "$YELLOW" "$OFF" "$*"; }
die()  { printf '\n  %serror%s %s\n\n' "$RED" "$OFF" "$*" >&2; exit 1; }

# ------------------------------------------------------------------ arguments

while [ $# -gt 0 ]; do
  case "$1" in
    --email)    ADMIN_EMAIL="${2:-}"; shift 2 ;;
    --password) ADMIN_PASSWORD="${2:-}"; shift 2 ;;
    --port)     HTTP_PORT="${2:-}"; shift 2 ;;
    --plugins)  PLUGINS="${2:-}"; shift 2 ;;
    --dir)      TARGET_DIR="${2:-}"; shift 2 ;;
    --repo)     REPO_URL="${2:-}"; shift 2 ;;
    --project-name) PROJECT_NAME="${2:-}"; shift 2 ;;
    --reset-data)   RESET_DATA=1; shift ;;
    --no-build) DO_BUILD=0; shift ;;
    --yes|-y)   ASSUME_YES=1; shift ;;
    -h|--help)  sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)          die "unknown option: $1 (try --help)" ;;
  esac
done

# Piping the script into a shell leaves stdin consumed; prompts then need the
# terminal directly.
INTERACTIVE=0
if [ "$ASSUME_YES" -eq 0 ] && [ -r /dev/tty ]; then
  INTERACTIVE=1
fi

ask() { # ask <prompt> <default>
  _p="$1"; _d="${2:-}"; _a=""
  if [ "$INTERACTIVE" -eq 0 ]; then printf '%s' "$_d"; return; fi
  if [ -n "$_d" ]; then printf '%s [%s]: ' "$_p" "$_d" > /dev/tty
  else printf '%s: ' "$_p" > /dev/tty; fi
  IFS= read -r _a < /dev/tty || _a=""
  [ -n "$_a" ] || _a="$_d"
  printf '%s' "$_a"
}

ask_secret() { # ask_secret <prompt>
  _p="$1"; _a=""
  printf '%s: ' "$_p" > /dev/tty
  stty -echo < /dev/tty 2>/dev/null || true
  IFS= read -r _a < /dev/tty || _a=""
  stty echo < /dev/tty 2>/dev/null || true
  printf '\n' > /dev/tty
  printf '%s' "$_a"
}

# ----------------------------------------------------------------- preflight

step "Checking prerequisites"

command -v docker >/dev/null 2>&1 || die "docker is not installed - https://docs.docker.com/get-docker/"
docker compose version >/dev/null 2>&1 || die "docker compose v2 is missing (try: docker compose version)"
docker info >/dev/null 2>&1 || die "the Docker daemon is not running - start Docker Desktop or dockerd"
ok "docker $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo present)"

if command -v openssl >/dev/null 2>&1; then
  RANDOM_SOURCE="openssl"
elif [ -r /dev/urandom ]; then
  RANDOM_SOURCE="urandom"
else
  die "no secure random source found (openssl or /dev/urandom required)"
fi
ok "secure random source: $RANDOM_SOURCE"

# -------------------------------------------------------------------- sources

if [ -f "docker-compose.yml" ] && [ -d "backend/app" ]; then
  ok "running from an existing checkout: $(pwd)"
  # Prefer the clone's own remote over the built-in default.
  _origin="$(git config --get remote.origin.url 2>/dev/null || true)"
  [ -z "$_origin" ] || REPO_URL="$_origin"
else
  command -v git >/dev/null 2>&1 || die "git is required to clone the repository"
  [ -n "$TARGET_DIR" ] || TARGET_DIR="reconcore-osint"
  if [ -d "$TARGET_DIR/.git" ]; then
    step "Updating existing clone in $TARGET_DIR"
    git -C "$TARGET_DIR" pull --ff-only || warn "could not fast-forward, keeping the local state"
  else
    step "Cloning $REPO_URL into $TARGET_DIR"
    git clone --depth 1 "$REPO_URL" "$TARGET_DIR" || die "clone failed"
  fi
  cd "$TARGET_DIR"
  ok "sources ready in $(pwd)"
fi

[ -f ".env.example" ] || die ".env.example not found - is this the right repository?"

# ----------------------------------------------------------- compose project

# Compose derives the project name from the directory name, so two checkouts
# with the same folder name would share containers and volumes. Pinning it
# makes the installation self-contained.
if [ -z "$PROJECT_NAME" ]; then
  PROJECT_NAME="$(basename "$(pwd)" | tr 'A-Z' 'a-z' | tr -c 'a-z0-9_-' '-' | sed 's/-*$//')"
fi
ok "compose project: $PROJECT_NAME"

dc() { docker compose -p "$PROJECT_NAME" "$@"; }

_owner="$(docker compose ls --all --format json 2>/dev/null \
  | tr ',' '\n' | grep -A1 "\"$PROJECT_NAME\"" | grep -i 'ConfigFiles' | head -1 || true)"
case "$_owner" in
  "") : ;;
  *"$(pwd)"*) : ;;
  *) warn "another checkout may already own the compose project '$PROJECT_NAME'"
     warn "use --project-name <name> if the stack below is not the one you expect" ;;
esac

# ------------------------------------------------------------------- secrets

gen_hex() { # 32 bytes, hex
  if [ "$RANDOM_SOURCE" = "openssl" ]; then openssl rand -hex 32
  else head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'; fi
}

gen_fernet() { # 32 bytes, url-safe base64 (Fernet key format)
  if [ "$RANDOM_SOURCE" = "openssl" ]; then
    openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n'
  else
    head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '\n'
  fi
}

gen_password() { # url-safe, no shell metacharacters
  if [ "$RANDOM_SOURCE" = "openssl" ]; then
    openssl rand -base64 24 | tr -d '\n' | tr '+/=' 'xyz'
  else
    head -c 24 /dev/urandom | base64 | tr -d '\n' | tr '+/=' 'xyz'
  fi
}

set_env() { # set_env <key> <value>  (rewrites the line, never duplicates it)
  _k="$1"; _v="$2"
  if grep -q "^${_k}=" .env 2>/dev/null; then
    _tmp="$(mktemp)"
    awk -v k="$_k" -v v="$_v" -F= '
      index($0, k "=") == 1 { print k "=" v; next } { print }
    ' .env > "$_tmp"
    cat "$_tmp" > .env
    rm -f "$_tmp"
  else
    printf '%s=%s\n' "$_k" "$_v" >> .env
  fi
}

get_env() { # get_env <key>
  grep "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- || true
}

# A .env created from the template during THIS run only holds placeholders:
# nothing in it may be mistaken for a real answer.
ENV_EXISTED=0
[ -f ".env" ] && ENV_EXISTED=1

if [ "$ENV_EXISTED" -eq 1 ]; then
  step "Reusing the existing .env"
  ok "secrets left untouched"
else
  # Postgres only applies POSTGRES_PASSWORD when it initialises an empty data
  # directory. Brand-new secrets against an existing volume would fail
  # authentication on every query.
  _volume="$(docker volume ls -q --filter "name=^${PROJECT_NAME}_postgres_data$" 2>/dev/null || true)"
  if [ -n "$_volume" ]; then
    if [ "$RESET_DATA" -eq 1 ]; then
      warn "removing the existing database volume ($_volume)"
      dc down -v >/dev/null 2>&1 || true
    else
      warn "a database from a previous install is still here ($_volume)"
      warn "its password died with the old .env, but the data itself is intact"
      _choice="k"
      if [ "$INTERACTIVE" -eq 1 ]; then
        say ""
        say "    [K] Keep the data (recommended) - the database password is reset in place"
        say "    [W] Wipe it and start from an empty database - everything is lost"
        say ""
        _choice="$(ask 'Keep or wipe? (K/W)' 'K')"
      fi
      case "$_choice" in
        w|W) warn "wiping the existing database"; dc down -v >/dev/null 2>&1 || true ;;
        *)   ADOPT_VOLUME=1; ok "existing data will be kept" ;;
      esac
    fi
  fi
  step "Generating secrets"
  cp .env.example .env
  chmod 600 .env 2>/dev/null || true
  set_env SECRET_KEY "$(gen_hex)"
  set_env SECRETS_ENCRYPTION_KEY "$(gen_fernet)"
  set_env POSTGRES_PASSWORD "$(gen_password)"
  set_env COMPOSE_PROJECT_NAME "$PROJECT_NAME"
  ok "SECRET_KEY, SECRETS_ENCRYPTION_KEY and POSTGRES_PASSWORD written to .env"
  warn "back up SECRETS_ENCRYPTION_KEY: without it, stored plugin secrets are unrecoverable"
fi

# ------------------------------------------------------------------- answers

step "Configuration"

if [ -z "$HTTP_PORT" ]; then
  HTTP_PORT="$(get_env HTTP_PORT)"
  [ -n "$HTTP_PORT" ] || HTTP_PORT="$(ask 'HTTP port to publish' '8080')"
fi
set_env HTTP_PORT "$HTTP_PORT"
ok "interface will listen on port $HTTP_PORT"

EXISTING_ADMIN="$(get_env FIRST_ADMIN_EMAIL)"
[ "$ENV_EXISTED" -eq 1 ] || EXISTING_ADMIN=""
case "$EXISTING_ADMIN" in
  ""|admin@example.org|CHANGE_ME*) EXISTING_ADMIN="" ;;
esac

if [ -z "$ADMIN_EMAIL" ]; then
  if [ -n "$EXISTING_ADMIN" ]; then
    ADMIN_EMAIL="$EXISTING_ADMIN"
  else
    ADMIN_EMAIL="$(ask 'Administrator email' '')"
  fi
fi
[ -n "$ADMIN_EMAIL" ] || die "an administrator email is required (--email)"
case "$ADMIN_EMAIL" in *@*.*) : ;; *) die "invalid email: $ADMIN_EMAIL" ;; esac

if [ -z "$ADMIN_PASSWORD" ] && [ "$ENV_EXISTED" -eq 1 ]; then
  # A previous attempt may have failed after writing it: reuse it rather
  # than asking again - unless it is the template placeholder.
  _stored="$(get_env FIRST_ADMIN_PASSWORD)"
  case "$_stored" in
    CHANGE_ME*|"") : ;;
    *) ADMIN_PASSWORD="$_stored"; ok "administrator password taken from .env" ;;
  esac
fi
case "$ADMIN_PASSWORD" in
  CHANGE_ME*) die "the administrator password is still the .env.example placeholder - choose a real one" ;;
esac
if [ -z "$ADMIN_PASSWORD" ] && [ "$INTERACTIVE" -eq 1 ]; then
  while : ; do
    ADMIN_PASSWORD="$(ask_secret 'Administrator password (12+ chars, upper, lower, digit, symbol)')"
    _confirm="$(ask_secret 'Confirm password')"
    [ "$ADMIN_PASSWORD" = "$_confirm" ] || { warn "passwords do not match"; continue; }
    [ "${#ADMIN_PASSWORD}" -ge 12 ] || { warn "at least 12 characters required"; continue; }
    break
  done
fi
[ -n "$ADMIN_PASSWORD" ] || die "an administrator password is required (--password or interactive mode)"

if [ -z "$PLUGINS" ]; then
  if [ "$INTERACTIVE" -eq 1 ]; then
    say ""
    say "  Available plugins:"
    say "    sherlock     usernames across many public sites"
    say "    holehe       services where an email address is in use"
    say "    phoneinfoga  phone number reconnaissance"
    say "    websearch    targeted search queries (runs nothing without an API key)"
    say "    toutatis     Instagram - optional, needs a session cookie, off by default"
    PLUGINS="$(ask 'Plugins to enable (comma-separated, or none)' "$DEFAULT_PLUGINS")"
  else
    PLUGINS="$DEFAULT_PLUGINS"
  fi
fi
ok "plugins to enable: $PLUGINS"

set_env FIRST_ADMIN_EMAIL "$ADMIN_EMAIL"
set_env FIRST_ADMIN_PASSWORD "$ADMIN_PASSWORD"

# --------------------------------------------------------------------- build

if [ "$DO_BUILD" -eq 1 ]; then
  step "Building the images (first run takes a few minutes)"
  dc build || die "docker compose build failed"
  ok "images built"
else
  warn "build skipped (--no-build)"
fi

if [ "$ADOPT_VOLUME" -eq 1 ]; then
  step "Adopting the existing database"
  _pg_user="$(get_env POSTGRES_USER)"
  [ -n "$_pg_user" ] || _pg_user="reconcore"
  _pg_pass="$(get_env POSTGRES_PASSWORD)"

  dc up -d postgres || die "could not start postgres"

  _ready=0
  _i=0
  while [ "$_i" -lt 40 ]; do
    if dc exec -T postgres pg_isready -U "$_pg_user" >/dev/null 2>&1; then _ready=1; break; fi
    _i=$((_i + 1))
    sleep 2
  done
  [ "$_ready" -eq 1 ] || die "postgres did not become ready"

  # The postgres image trusts local socket connections, which is how the
  # password can be reset without knowing the old one.
  _escaped="$(printf '%s' "$_pg_pass" | sed "s/'/''/g")"
  if ! dc exec -T postgres psql -v ON_ERROR_STOP=1 -U "$_pg_user" -d postgres \
       -c "ALTER USER \"$_pg_user\" WITH PASSWORD '$_escaped';" >/dev/null 2>&1; then
    die "could not reset the database password.
  Re-run with --reset-data to start from an empty database instead."
  fi
  ok "database password re-synchronised, existing data preserved"
fi

step "Starting the stack"
dc up -d || die "docker compose up failed"
ok "containers started"

step "Waiting for the API to become healthy"
_waited=0
while [ "$_waited" -lt 180 ]; do
  if dc exec -T api python -c "
import sys, urllib.request
try:
    sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=3).status == 200 else 1)
except Exception:
    sys.exit(1)
" >/dev/null 2>&1; then
    ok "API is up"
    break
  fi
  _waited=$((_waited + 3))
  sleep 3
done
[ "$_waited" -lt 180 ] || die "the API did not become healthy - check: docker compose logs api"

# --------------------------------------------------------------------- setup

step "Creating the schema, the admin account and enabling plugins"
_setup_log="$(mktemp)"
if ! dc exec -T api python -m app.cli setup --enable "$PLUGINS" 2>&1 | tee "$_setup_log"; then
  # Surface the real error instead of a generic failure message.
  die "setup failed:
$(tail -n 12 "$_setup_log")"
fi
rm -f "$_setup_log"

# The password is no longer needed once the account exists: remove it from .env.
set_env FIRST_ADMIN_PASSWORD ""
ok "FIRST_ADMIN_PASSWORD cleared from .env"

# ------------------------------------------------------------------- summary

step "Done"
say ""
say "  ${BOLD}Interface${OFF}     http://localhost:${HTTP_PORT}"
say "  ${BOLD}Sign in${OFF}       ${ADMIN_EMAIL}"
say "  ${BOLD}API docs${OFF}      http://localhost:${HTTP_PORT}/docs  (development mode only)"
say ""
say "  Useful commands:"
say "    docker compose ps                                        service status"
say "    docker compose logs -f api                               follow the API logs"
say "    docker compose exec api python -m app.cli plugin list     plugin registry"
say "    docker compose exec api python -m app.cli plugin audit all  security reports"
say "    docker compose down                                      stop everything"
say ""
say "  ${YELLOW}Reminder${OFF}: this tool collects personal data from public sources."
say "  Use it only within a lawful, documented framework, and set a retention"
say "  policy (DATA_RETENTION_DAYS in .env)."
say ""

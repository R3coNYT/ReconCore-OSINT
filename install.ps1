<#
.SYNOPSIS
    ReconCore OSINT - one-command installer for Windows.

.DESCRIPTION
    Clones the repository if needed, generates the secrets, builds and starts
    the stack, creates the schema and the administrator account, and enables the
    plugins you choose. Running it twice is safe: nothing already in place is
    overwritten.

.EXAMPLE
    irm https://raw.githubusercontent.com/<owner>/<repo>/main/install.ps1 | iex

.EXAMPLE
    .\install.ps1 -Email admin@example.org -Port 8080

.EXAMPLE
    .\install.ps1 -Email admin@example.org -Password 'S3cret!Passw0rd' -Plugins sherlock,websearch -Yes
#>
[CmdletBinding()]
param(
    [string]   $Email,
    [string]   $Password,
    [int]      $Port,
    [string[]] $Plugins,
    [string]   $Directory,
    [string]   $Repo = 'https://github.com/R3coN/ReconCore-OSINT.git',
    [switch]   $NoBuild,
    [switch]   $Yes
)

$ErrorActionPreference = 'Stop'
$DefaultPlugins = @('sherlock', 'holehe', 'phoneinfoga', 'websearch')

function Write-Step { param([string]$Text) Write-Host "`n==> $Text" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Text) Write-Host "  ok   $Text" -ForegroundColor Green }
function Write-Warn { param([string]$Text) Write-Host "  warn $Text" -ForegroundColor Yellow }
function Stop-Install {
    param([string]$Text)
    Write-Host "`n  error $Text`n" -ForegroundColor Red
    exit 1
}

function Read-Answer {
    param([string]$Prompt, [string]$Default = '')
    if ($Yes) { return $Default }
    $suffix = if ($Default) { " [$Default]" } else { '' }
    $answer = Read-Host "$Prompt$suffix"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $Default }
    return $answer.Trim()
}

function New-RandomBytes {
    param([int]$Count = 32)
    $bytes = New-Object byte[] $Count
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    return $bytes
}

function New-HexSecret { (New-RandomBytes 32 | ForEach-Object { $_.ToString('x2') }) -join '' }

function New-FernetKey {
    # Fernet expects 32 random bytes, url-safe base64 encoded.
    [Convert]::ToBase64String((New-RandomBytes 32)).Replace('+', '-').Replace('/', '_')
}

function New-DbPassword {
    # url-safe characters only: the value travels through .env and connection URLs.
    ([Convert]::ToBase64String((New-RandomBytes 24)) -replace '[+/=]', 'x')
}

function Get-EnvValue {
    param([string]$Key)
    if (-not (Test-Path '.env')) { return '' }
    $line = Select-String -Path '.env' -Pattern "^$([regex]::Escape($Key))=" -SimpleMatch:$false |
            Select-Object -First 1
    if (-not $line) { return '' }
    return $line.Line.Substring($Key.Length + 1)
}

function Set-EnvValue {
    param([string]$Key, [string]$Value)
    $lines = @(Get-Content '.env' -Encoding UTF8)
    $found = $false
    $out = foreach ($line in $lines) {
        if ($line -match "^$([regex]::Escape($Key))=") { $found = $true; "$Key=$Value" } else { $line }
    }
    if (-not $found) { $out += "$Key=$Value" }
    Set-Content -Path '.env' -Value $out -Encoding UTF8
}

# ----------------------------------------------------------------- preflight

Write-Step 'Checking prerequisites'

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Stop-Install 'docker is not installed - https://docs.docker.com/get-docker/'
}
docker compose version *> $null
if ($LASTEXITCODE -ne 0) { Stop-Install 'docker compose v2 is missing (try: docker compose version)' }
docker info *> $null
if ($LASTEXITCODE -ne 0) { Stop-Install 'the Docker daemon is not running - start Docker Desktop' }
Write-Ok 'docker is available'

# -------------------------------------------------------------------- sources

if ((Test-Path 'docker-compose.yml') -and (Test-Path 'backend/app')) {
    Write-Ok "running from an existing checkout: $(Get-Location)"
} else {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Stop-Install 'git is required to clone the repository'
    }
    if (-not $Directory) { $Directory = 'reconcore-osint' }
    if (Test-Path (Join-Path $Directory '.git')) {
        Write-Step "Updating existing clone in $Directory"
        git -C $Directory pull --ff-only
        if ($LASTEXITCODE -ne 0) { Write-Warn 'could not fast-forward, keeping the local state' }
    } else {
        Write-Step "Cloning $Repo into $Directory"
        git clone --depth 1 $Repo $Directory
        if ($LASTEXITCODE -ne 0) { Stop-Install 'clone failed' }
    }
    Set-Location $Directory
    Write-Ok "sources ready in $(Get-Location)"
}

if (-not (Test-Path '.env.example')) { Stop-Install '.env.example not found - is this the right repository?' }

# ------------------------------------------------------------------- secrets

if (Test-Path '.env') {
    Write-Step 'Reusing the existing .env'
    Write-Ok 'secrets left untouched'
} else {
    Write-Step 'Generating secrets'
    Copy-Item '.env.example' '.env'
    Set-EnvValue 'SECRET_KEY'             (New-HexSecret)
    Set-EnvValue 'SECRETS_ENCRYPTION_KEY' (New-FernetKey)
    Set-EnvValue 'POSTGRES_PASSWORD'      (New-DbPassword)
    Write-Ok 'SECRET_KEY, SECRETS_ENCRYPTION_KEY and POSTGRES_PASSWORD written to .env'
    Write-Warn 'back up SECRETS_ENCRYPTION_KEY: without it, stored plugin secrets are unrecoverable'
}

# ------------------------------------------------------------------- answers

Write-Step 'Configuration'

if (-not $Port) {
    $existingPort = Get-EnvValue 'HTTP_PORT'
    $Port = if ($existingPort) { [int]$existingPort } else { [int](Read-Answer 'HTTP port to publish' '8080') }
}
Set-EnvValue 'HTTP_PORT' "$Port"
Write-Ok "interface will listen on port $Port"

$existingAdmin = Get-EnvValue 'FIRST_ADMIN_EMAIL'
if ($existingAdmin -eq 'admin@example.org') { $existingAdmin = '' }

if (-not $Email) {
    $Email = if ($existingAdmin) { $existingAdmin } else { Read-Answer 'Administrator email' }
}
if (-not $Email) { Stop-Install 'an administrator email is required (-Email)' }
if ($Email -notmatch '^[^@\s]+@[^@\s]+\.[^@\s]+$') { Stop-Install "invalid email: $Email" }

if (-not $Password -and -not $Yes) {
    while ($true) {
        $secure  = Read-Host 'Administrator password (12+ chars, upper, lower, digit, symbol)' -AsSecureString
        $confirm = Read-Host 'Confirm password' -AsSecureString
        $plain1 = [System.Net.NetworkCredential]::new('', $secure).Password
        $plain2 = [System.Net.NetworkCredential]::new('', $confirm).Password
        if ($plain1 -ne $plain2)   { Write-Warn 'passwords do not match'; continue }
        if ($plain1.Length -lt 12) { Write-Warn 'at least 12 characters required'; continue }
        $Password = $plain1
        break
    }
}
if (-not $Password) { Stop-Install 'an administrator password is required (-Password or interactive mode)' }

if (-not $Plugins) {
    if ($Yes) {
        $Plugins = $DefaultPlugins
    } else {
        Write-Host ''
        Write-Host '  Available plugins:'
        Write-Host '    sherlock     usernames across many public sites'
        Write-Host '    holehe       services where an email address is in use'
        Write-Host '    phoneinfoga  phone number reconnaissance'
        Write-Host '    websearch    targeted search queries (runs nothing without an API key)'
        Write-Host '    toutatis     Instagram - optional, needs a session cookie, off by default'
        $answer = Read-Answer 'Plugins to enable (comma-separated, or none)' ($DefaultPlugins -join ',')
        $Plugins = $answer -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    }
}
$pluginList = ($Plugins -join ',')
Write-Ok "plugins to enable: $pluginList"

Set-EnvValue 'FIRST_ADMIN_EMAIL'    $Email
Set-EnvValue 'FIRST_ADMIN_PASSWORD' $Password

# --------------------------------------------------------------------- build

if (-not $NoBuild) {
    Write-Step 'Building the images (first run takes a few minutes)'
    docker compose build
    if ($LASTEXITCODE -ne 0) { Stop-Install 'docker compose build failed' }
    Write-Ok 'images built'
} else {
    Write-Warn 'build skipped (-NoBuild)'
}

Write-Step 'Starting the stack'
docker compose up -d
if ($LASTEXITCODE -ne 0) { Stop-Install 'docker compose up failed' }
Write-Ok 'containers started'

Write-Step 'Waiting for the API to become healthy'
$probe = @'
import sys, urllib.request
try:
    sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=3).status == 200 else 1)
except Exception:
    sys.exit(1)
'@
$waited = 0
while ($waited -lt 180) {
    docker compose exec -T api python -c $probe *> $null
    if ($LASTEXITCODE -eq 0) { Write-Ok 'API is up'; break }
    Start-Sleep -Seconds 3
    $waited += 3
}
if ($waited -ge 180) { Stop-Install 'the API did not become healthy - check: docker compose logs api' }

# --------------------------------------------------------------------- setup

Write-Step 'Creating the schema, the admin account and enabling plugins'
docker compose exec -T api python -m app.cli setup --enable $pluginList
if ($LASTEXITCODE -ne 0) { Stop-Install 'setup failed - check: docker compose logs api' }

# The password is no longer needed once the account exists: remove it from .env.
Set-EnvValue 'FIRST_ADMIN_PASSWORD' ''
Write-Ok 'FIRST_ADMIN_PASSWORD cleared from .env'

# ------------------------------------------------------------------- summary

Write-Step 'Done'
Write-Host ''
Write-Host "  Interface     http://localhost:$Port"
Write-Host "  Sign in       $Email"
Write-Host "  API docs      http://localhost:$Port/docs  (development mode only)"
Write-Host ''
Write-Host '  Useful commands:'
Write-Host '    docker compose ps                                            service status'
Write-Host '    docker compose logs -f api                                   follow the API logs'
Write-Host '    docker compose exec api python -m app.cli plugin list         plugin registry'
Write-Host '    docker compose exec api python -m app.cli plugin audit all    security reports'
Write-Host '    docker compose down                                          stop everything'
Write-Host ''
Write-Host '  Reminder: this tool collects personal data from public sources.' -ForegroundColor Yellow
Write-Host '  Use it only within a lawful, documented framework, and set a retention'
Write-Host '  policy (DATA_RETENTION_DAYS in .env).'
Write-Host ''

<#
.SYNOPSIS
    ReconCore OSINT - one-command installer for Windows.

.DESCRIPTION
    Clones the repository if needed, generates the secrets, builds and starts
    the stack, creates the schema and the administrator account, and enables the
    plugins you choose. Running it twice is safe: nothing already in place is
    overwritten.

    Everything is written to reconcore-install.log, and the window is never
    closed on error: if something fails you still have the message on screen and
    in the log.

    Compatible with Windows PowerShell 5.1 and PowerShell 7+.

.EXAMPLE
    irm https://raw.githubusercontent.com/R3coNYT/ReconCore-OSINT/main/install.ps1 | iex

.EXAMPLE
    .\install.ps1 -Email admin@example.org -Port 8080

.EXAMPLE
    .\install.ps1 -Email admin@example.org -Password 'S3cret!Passw0rd' -Plugins sherlock,websearch -Yes
#>
#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]   $Email,
    [string]   $Password,
    [int]      $Port,
    [string[]] $Plugins,
    [string]   $Directory,
    [string]   $Repo = 'https://github.com/R3coNYT/ReconCore-OSINT.git',
    [string]   $ProjectName,
    [switch]   $ResetData,
    [switch]   $NoBuild,
    [switch]   $Yes
)

$DefaultPlugins = @('sherlock', 'holehe', 'phoneinfoga', 'websearch')
$LogFile = Join-Path (Get-Location) 'reconcore-install.log'

function Write-Step { param([string]$Text) Write-Host "`n==> $Text" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Text) Write-Host "  ok   $Text" -ForegroundColor Green }
function Write-Warn { param([string]$Text) Write-Host "  warn $Text" -ForegroundColor Yellow }

# Never calls `exit`: in an interactive session that would close the window and
# take the error message with it. The outer try/catch reports instead.
function Stop-Install { param([string]$Text) throw $Text }

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
    # RandomNumberGenerator::Fill does not exist on .NET Framework, so it is
    # unavailable in Windows PowerShell 5.1. This works on both editions.
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
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
    foreach ($line in [System.IO.File]::ReadAllLines((Resolve-Path '.env'))) {
        if ($line.StartsWith("$Key=")) { return $line.Substring($Key.Length + 1) }
    }
    return ''
}

function Set-EnvValue {
    param([string]$Key, [string]$Value)
    $path  = (Resolve-Path '.env').Path
    $lines = [System.Collections.Generic.List[string]]::new()
    $found = $false
    foreach ($line in [System.IO.File]::ReadAllLines($path)) {
        if ($line.StartsWith("$Key=")) { $lines.Add("$Key=$Value"); $found = $true }
        else { $lines.Add($line) }
    }
    if (-not $found) { $lines.Add("$Key=$Value") }
    # Docker Compose reads .env verbatim: a UTF-8 BOM would corrupt the first
    # variable name, so the file is written without one.
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($path, ($lines -join "`n") + "`n", $utf8NoBom)
}

try { Start-Transcript -Path $LogFile -Append -Force | Out-Null } catch { }

$failed = $null
$adoptVolume = $false
try {
    # ------------------------------------------------------------- preflight

    Write-Step 'Checking prerequisites'
    Write-Ok "PowerShell $($PSVersionTable.PSVersion) ($($PSVersionTable.PSEdition))"

    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Stop-Install 'docker is not installed - https://docs.docker.com/get-docker/'
    }
    docker compose version *> $null
    if ($LASTEXITCODE -ne 0) { Stop-Install 'docker compose v2 is missing (try: docker compose version)' }
    docker info *> $null
    if ($LASTEXITCODE -ne 0) { Stop-Install 'the Docker daemon is not running - start Docker Desktop' }
    Write-Ok 'docker is available'

    # --------------------------------------------------------------- sources

    if ((Test-Path 'docker-compose.yml') -and (Test-Path 'backend/app')) {
        Write-Ok "running from an existing checkout: $(Get-Location)"
        # Prefer the clone's own remote over the built-in default.
        $origin = (git config --get remote.origin.url 2>$null)
        if ($origin) { $Repo = "$origin".Trim() }
    } else {
        if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
            Stop-Install 'git is required to clone the repository - https://git-scm.com/download/win'
        }
        if (-not $Directory) { $Directory = 'reconcore-osint' }
        if (Test-Path (Join-Path $Directory '.git')) {
            Write-Step "Updating existing clone in $Directory"
            git -C $Directory pull --ff-only
            if ($LASTEXITCODE -ne 0) { Write-Warn 'could not fast-forward, keeping the local state' }
        } else {
            Write-Step "Cloning $Repo into $Directory"
            git clone --depth 1 $Repo $Directory
            if ($LASTEXITCODE -ne 0) { Stop-Install "clone failed: $Repo" }
        }
        Set-Location $Directory
        $LogFile = Join-Path (Get-Location) 'reconcore-install.log'
        Write-Ok "sources ready in $(Get-Location)"
    }

    if (-not (Test-Path '.env.example')) {
        Stop-Install '.env.example not found - is this the right repository?'
    }

    # ------------------------------------------------------- compose project

    # Compose derives the project name from the directory name, so two
    # checkouts with the same folder name would share containers and volumes.
    # Pinning it makes the installation self-contained.
    if (-not $ProjectName) {
        $ProjectName = ((Split-Path -Leaf (Get-Location)).ToLower() -replace '[^a-z0-9_-]', '-').Trim('-')
    }
    $composeArgs = @('compose', '-p', $ProjectName)
    Write-Ok "compose project: $ProjectName"

    $here = (Get-Location).Path
    $others = $null
    try { $others = docker compose ls --all --format json | ConvertFrom-Json } catch { }
    foreach ($p in @($others)) {
        if ($p.Name -eq $ProjectName -and $p.ConfigFiles -and -not $p.ConfigFiles.StartsWith($here)) {
            Stop-Install (
                "another checkout already owns the compose project '$ProjectName':`n" +
                "    $($p.ConfigFiles)`n" +
                "Starting here would take over its containers and volumes. Re-run with" +
                " -ProjectName <other-name>, or remove the other stack first."
            )
        }
    }

    # --------------------------------------------------------------- secrets

    # A .env created from the template during THIS run only holds
    # placeholders: nothing in it may be mistaken for a real answer.
    $envExisted = Test-Path '.env'

    if ($envExisted) {
        Write-Step 'Reusing the existing .env'
        Write-Ok 'secrets left untouched'
    } else {
        # Postgres only applies POSTGRES_PASSWORD when it initialises an empty
        # data directory. Brand-new secrets against an existing volume would
        # fail authentication on every query.
        $volume = docker volume ls -q --filter "name=^$($ProjectName)_postgres_data$"
        if ($volume) {
            if ($ResetData) {
                Write-Warn "removing the existing database volume ($volume)"
                docker @composeArgs down -v *> $null
            } else {
                Write-Warn "a database from a previous install is still here ($volume)"
                Write-Warn 'its password died with the old .env, but the data itself is intact'
                $choice = 'k'
                if (-not $Yes) {
                    Write-Host ''
                    Write-Host '    [K] Keep the data (recommended) - the database password is reset in place'
                    Write-Host '    [W] Wipe it and start from an empty database - everything is lost'
                    Write-Host ''
                    $choice = (Read-Answer 'Keep or wipe? (K/W)' 'K').ToLower()
                }
                if ($choice -eq 'w') {
                    Write-Warn 'wiping the existing database'
                    docker @composeArgs down -v *> $null
                } else {
                    $adoptVolume = $true
                    Write-Ok 'existing data will be kept'
                }
            }
        }
        Write-Step 'Generating secrets'
        Copy-Item '.env.example' '.env'
        Set-EnvValue 'SECRET_KEY'             (New-HexSecret)
        Set-EnvValue 'SECRETS_ENCRYPTION_KEY' (New-FernetKey)
        Set-EnvValue 'POSTGRES_PASSWORD'      (New-DbPassword)
        Set-EnvValue 'COMPOSE_PROJECT_NAME'   $ProjectName
        Write-Ok 'SECRET_KEY, SECRETS_ENCRYPTION_KEY and POSTGRES_PASSWORD written to .env'
        Write-Warn 'back up SECRETS_ENCRYPTION_KEY: without it, stored plugin secrets are unrecoverable'
    }

    # --------------------------------------------------------------- answers

    Write-Step 'Configuration'

    if (-not $Port) {
        $existingPort = Get-EnvValue 'HTTP_PORT'
        $Port = if ($existingPort) { [int]$existingPort } else { [int](Read-Answer 'HTTP port to publish' '8080') }
    }
    Set-EnvValue 'HTTP_PORT' "$Port"
    Write-Ok "interface will listen on port $Port"

    $existingAdmin = Get-EnvValue 'FIRST_ADMIN_EMAIL'
    if (-not $envExisted -or $existingAdmin -eq 'admin@example.org' -or
        $existingAdmin.StartsWith('CHANGE_ME')) { $existingAdmin = '' }

    if (-not $Email) {
        $Email = if ($existingAdmin) { $existingAdmin } else { Read-Answer 'Administrator email' }
    }
    if (-not $Email) { Stop-Install 'an administrator email is required (-Email)' }
    if ($Email -notmatch '^[^@\s]+@[^@\s]+\.[^@\s]+$') { Stop-Install "invalid email: $Email" }

    if (-not $Password -and $envExisted) {
        # A previous attempt may have failed after writing it: reuse it
        # rather than asking again - unless it is the template placeholder.
        $stored = Get-EnvValue 'FIRST_ADMIN_PASSWORD'
        if ($stored -and -not $stored.StartsWith('CHANGE_ME')) {
            $Password = $stored
            Write-Ok 'administrator password taken from .env'
        }
    }
    if ($Password -and $Password.StartsWith('CHANGE_ME')) {
        Stop-Install 'the administrator password is still the .env.example placeholder - choose a real one'
    }
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

    # ----------------------------------------------------------------- build

    if (-not $NoBuild) {
        Write-Step 'Building the images (first run takes a few minutes)'
        docker @composeArgs build
        if ($LASTEXITCODE -ne 0) { Stop-Install 'docker compose build failed' }
        Write-Ok 'images built'
    } else {
        Write-Warn 'build skipped (-NoBuild)'
    }

    if ($adoptVolume) {
        Write-Step 'Adopting the existing database'
        $pgUser = Get-EnvValue 'POSTGRES_USER'
        if (-not $pgUser) { $pgUser = 'reconcore' }
        $pgPass = Get-EnvValue 'POSTGRES_PASSWORD'

        docker @composeArgs up -d postgres
        if ($LASTEXITCODE -ne 0) { Stop-Install 'could not start postgres' }

        $ready = $false
        for ($i = 0; $i -lt 40; $i++) {
            $null = docker @composeArgs exec -T postgres pg_isready -U $pgUser 2>&1
            if ($LASTEXITCODE -eq 0) { $ready = $true; break }
            Start-Sleep -Seconds 2
        }
        if (-not $ready) { Stop-Install 'postgres did not become ready' }

        # The postgres image trusts local socket connections, which is how the
        # password can be reset without knowing the old one.
        $escaped = $pgPass.Replace("'", "''")
        $sql = 'ALTER USER "' + $pgUser + '" WITH PASSWORD ''' + $escaped + ''';'
        $alter = docker @composeArgs exec -T postgres psql -v ON_ERROR_STOP=1 -U $pgUser -d postgres -c $sql 2>&1
        if ($LASTEXITCODE -ne 0) {
            Stop-Install ("could not reset the database password:`n" + ($alter -join "`n") +
                          "`nRe-run with -ResetData to start from an empty database instead.")
        }
        Write-Ok 'database password re-synchronised, existing data preserved'
    }

    Write-Step 'Starting the stack'
    docker @composeArgs up -d
    if ($LASTEXITCODE -ne 0) { Stop-Install 'docker compose up failed' }
    Write-Ok 'containers started'

    Write-Step 'Waiting for the API to become healthy'
    # Kept on a single line: a multi-line argument does not survive the trip
    # through the Windows command line.
    $probe = "import sys,urllib.request" +
             ";sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health',timeout=3).status==200 else 1)"
    $waited = 0
    while ($waited -lt 180) {
        # 2>&1 into the pipeline: a not-yet-listening API prints a Python
        # traceback that must not scare the user during normal startup.
        $null = docker @composeArgs exec -T api python -c $probe 2>&1
        if ($LASTEXITCODE -eq 0) { Write-Ok 'API is up'; break }
        Start-Sleep -Seconds 3
        $waited += 3
    }
    if ($waited -ge 180) { Stop-Install 'the API did not become healthy - check: docker compose logs api' }

    # ----------------------------------------------------------------- setup

    Write-Step 'Creating the schema, the admin account and enabling plugins'
    $setupOutput = docker @composeArgs exec -T api python -m app.cli setup --enable $pluginList 2>&1
    $setupOutput | ForEach-Object { Write-Host "  $_" }
    if ($LASTEXITCODE -ne 0) {
        # Surface the real error instead of a generic failure message.
        $tail = ($setupOutput | Select-Object -Last 12) -join "`n"
        Stop-Install "setup failed:`n$tail"
    }

    # The password is no longer needed once the account exists: remove it from .env.
    Set-EnvValue 'FIRST_ADMIN_PASSWORD' ''
    Write-Ok 'FIRST_ADMIN_PASSWORD cleared from .env'

    # --------------------------------------------------------------- summary

    Write-Step 'Done'
    Write-Host ''
    Write-Host "  Interface     http://localhost:$Port"
    Write-Host "  Sign in       $Email"
    Write-Host "  API docs      http://localhost:$Port/docs  (development mode only)"
    Write-Host ''
    Write-Host '  Useful commands:'
    Write-Host "    docker compose -p $ProjectName ps                             service status"
    Write-Host "    docker compose -p $ProjectName logs -f api                    follow the API logs"
    Write-Host "    docker compose -p $ProjectName exec api python -m app.cli plugin list   plugin registry"
    Write-Host "    docker compose -p $ProjectName down                           stop everything"
    Write-Host ''
    Write-Host '  Reminder: this tool collects personal data from public sources.' -ForegroundColor Yellow
    Write-Host '  Use it only within a lawful, documented framework, and set a retention'
    Write-Host '  policy (DATA_RETENTION_DAYS in .env).'
    Write-Host ''
}
catch {
    $failed = $_
    Write-Host ''
    Write-Host '  ============================ INSTALL FAILED ============================' -ForegroundColor Red
    Write-Host "  $($_.Exception.Message)" -ForegroundColor Red
    if ($_.InvocationInfo -and $_.InvocationInfo.ScriptLineNumber) {
        Write-Host "  at line $($_.InvocationInfo.ScriptLineNumber): $($_.InvocationInfo.Line.Trim())" -ForegroundColor DarkGray
    }
    Write-Host ''
    Write-Host "  Full log: $LogFile" -ForegroundColor Yellow
    Write-Host '  Container logs: docker compose logs --tail=50' -ForegroundColor Yellow
    Write-Host '  ========================================================================' -ForegroundColor Red
    Write-Host ''
}
finally {
    try { Stop-Transcript | Out-Null } catch { }
    # The window stays open so the message above can actually be read.
    if (-not $Yes -and $Host.UI.RawUI) {
        try { Read-Host 'Press Enter to close' | Out-Null } catch { }
    }
}

if ($failed -and $Yes) { exit 1 }

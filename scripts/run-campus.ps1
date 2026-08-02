<#
    run-campus.ps1 — start the on-site half of the hybrid deployment.

    Goal: a freshly cloned repo plus this one command ends up serving.
    Everything the script can work out for itself, it works out — you only ever
    supply the values that are genuinely secret.

    It is idempotent. Run it again after a `git pull`, after a reboot, or after
    a DHCP lease moves this machine to a new address, and it does the right
    thing with no hand-editing.

        powershell -ExecutionPolicy Bypass -File scripts\run-campus.ps1

    What it handles for you
      * creates backend\venv and installs requirements when they are missing
      * creates backend\.env from the campus template on first run, then asks
        once for the values that cannot be derived (SECRET_KEY, DATABASE_URL, R2)
      * detects this machine's LAN address on every run and serves on it. The
        addresses go through the environment, which python-dotenv lets win over
        .env, so a changed IP never means editing a file
      * pins RUN_MIGRATIONS=false so this half can never migrate the shared
        database — Railway owns the schema
      * rebuilds the React bundle only when the sources are newer than the
        last build, and reinstalls node modules only when the lockfile moved
      * pings the cameras actually registered in the database

    Flags
      -Port <n>        serve on a different port (default 8000)
      -SkipFrontend    never rebuild, even if the sources changed
      -Rebuild         force a rebuild even if the bundle looks current
      -Reconfigure     re-ask for the secret values and rewrite them into .env
#>

[CmdletBinding()]
param(
    [int]$Port = 8000,
    [switch]$SkipFrontend,
    [switch]$Rebuild,
    [switch]$Reconfigure
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

function Say($msg, $colour = 'Cyan') { Write-Host "[campus] $msg" -ForegroundColor $colour }

# ── 1. Python environment ────────────────────────────────────────────────────
$python = Join-Path $repo 'backend\venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    Say 'No virtualenv found - creating backend\venv (one-time, a few minutes)...'
    & python -m venv (Join-Path $repo 'backend\venv')
    if (-not (Test-Path $python)) {
        Write-Error 'Could not create the virtualenv. Is Python installed and on PATH?'
    }
    & $python -m pip install --upgrade pip --quiet
    & $python -m pip install -r (Join-Path $repo 'requirements.txt')
    Say 'Dependencies installed.' 'Green'
}

# ── 2. .env — created from the template, secrets asked for once ──────────────
$envFile  = Join-Path $repo 'backend\.env'
$template = Join-Path $repo 'backend\.env.campus.example'

if (-not (Test-Path $envFile)) {
    if (-not (Test-Path $template)) { Write-Error "Missing $template - cannot bootstrap .env." }
    Copy-Item $template $envFile
    Say 'Created backend\.env from the campus template.' 'Green'
    $Reconfigure = $true
}

# Values this machine cannot derive. SECRET_KEY and DATABASE_URL must match
# Railway exactly: the key signs the JWTs both halves accept, and the database
# string is what makes the two halves one system rather than two.
$secrets = @(
    @{ Key = 'SECRET_KEY';           Prompt = "Django SECRET_KEY (byte-identical to Railway's)" },
    @{ Key = 'DATABASE_URL';         Prompt = 'Neon DATABASE_URL (the same one Railway uses)' },
    @{ Key = 'R2_ACCESS_KEY_ID';     Prompt = 'Cloudflare R2 access key id' },
    @{ Key = 'R2_SECRET_ACCESS_KEY'; Prompt = 'Cloudflare R2 secret access key' },
    @{ Key = 'R2_BUCKET_NAME';       Prompt = 'R2 bucket name' },
    @{ Key = 'R2_ACCOUNT_ID';        Prompt = 'R2 account id' },
    @{ Key = 'R2_PUBLIC_URL';        Prompt = 'R2 public URL' }
)

function Get-EnvValue($key) {
    $line = Select-String -Path $envFile -Pattern "^$key=" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $line) { return '' }
    return ($line.Line -replace "^$key=", '').Trim()
}

function Set-EnvValue($key, $value) {
    $content = Get-Content $envFile
    if ($content | Select-String -Pattern "^$key=" -Quiet) {
        $content = $content -replace "^$key=.*", "$key=$value"
    } else {
        $content += "$key=$value"
    }
    Set-Content -Path $envFile -Value $content -Encoding utf8
}

# A value still carrying a template placeholder counts as unset.
function Test-Placeholder($v) {
    return ($v -eq '') -or ($v -match '^<.*>$') -or ($v -match 'same as Railway|CHANGE|your-|paste')
}

$missing = @($secrets | Where-Object { Test-Placeholder (Get-EnvValue $_.Key) })
if ($Reconfigure -or $missing.Count -gt 0) {
    Say 'Values needed before this half can join the shared system.' 'Yellow'
    Write-Host '        Press Enter to keep what is already in .env.' -ForegroundColor DarkGray
    foreach ($s in $secrets) {
        $current = Get-EnvValue $s.Key
        if (Test-Placeholder $current)                 { $shown = '(not set)' }
        elseif ($s.Key -match 'SECRET|KEY|DATABASE')   { $shown = '(set - hidden)' }
        else                                           { $shown = $current }
        $entered = Read-Host "  $($s.Prompt) [$shown]"
        if ($entered -and $entered.Trim()) { Set-EnvValue $s.Key $entered.Trim() }
    }
    Say 'Saved to backend\.env.' 'Green'
}

# ── 3. This machine's address, recomputed every run ──────────────────────────
# Passed through the environment rather than written into .env: python-dotenv
# does not override variables already set in the process, so these win. A new
# DHCP lease therefore needs no file edit at all.
$lan = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
        Sort-Object -Property @{ Expression = { $_.IPAddress -like '192.168.*' } } -Descending |
        Select-Object -First 1).IPAddress
if (-not $lan) {
    $lan = '127.0.0.1'
    Say 'No LAN address found - serving on localhost only.' 'Yellow'
}

$origin = "http://${lan}:$Port"
$env:ALLOWED_HOSTS        = "localhost,127.0.0.1,$lan"
$env:FRONTEND_URL         = $origin
$env:BACKEND_URL          = $origin
$env:CSRF_TRUSTED_ORIGINS = $origin
$env:SECURE_SSL_REDIRECT  = 'false'   # plain HTTP on the LAN; the redirect would loop
$env:RUN_MIGRATIONS       = 'false'   # Railway owns the schema for the shared DB

# ── 4. Camera reachability, read from the database ───────────────────────────
# These addresses used to be hardcoded here and drifted out of date, so the
# check reported NO ROUTE for cameras that were not the configured ones.
#
# Via a management command, not `python -c "..."`: PowerShell strips the quotes
# out of a multi-line string when handing it to a native exe, and the inline
# version died with a SyntaxError. Errors are shown rather than swallowed —
# hiding them is what let that failure masquerade as "no cameras registered".
Say 'Checking the cameras registered in the database...'
Push-Location (Join-Path $repo 'backend')
$camOut = & $python manage.py camera_hosts 2>&1
$camOk  = ($LASTEXITCODE -eq 0)
Pop-Location

$cams = @()
if ($camOk) {
    $cams = @($camOut | Where-Object { $_ -match "`t" })
} else {
    Write-Host '  could not read the camera list:' -ForegroundColor Yellow
    $camOut | Select-Object -Last 3 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
}

if ($cams.Count -eq 0 -and $camOk) {
    Write-Host '  no cameras registered yet - add them in Device Management' -ForegroundColor DarkGray
} elseif ($cams.Count -gt 0) {
    foreach ($row in $cams) {
        $name, $ip = $row -split "`t", 2
        if (Test-Connection -ComputerName $ip -Count 1 -Quiet -ErrorAction SilentlyContinue) {
            Write-Host "  reachable : $name ($ip)" -ForegroundColor Green
        } else {
            Write-Host "  NO ROUTE  : $name ($ip) - live scanning will not work from this machine" -ForegroundColor Yellow
        }
    }
}

# ── 5. Frontend bundle, rebuilt only when stale ──────────────────────────────
$buildDir  = Join-Path $repo 'backend\frontend_build'
$needBuild = $false
if (-not $SkipFrontend) {
    if ($Rebuild -or -not (Test-Path $buildDir)) {
        $needBuild = $true
    } else {
        $built = (Get-Item $buildDir).LastWriteTime
        $newest = Get-ChildItem (Join-Path $repo 'frontend\src'), (Join-Path $repo 'frontend\package.json') `
                      -Recurse -File -ErrorAction SilentlyContinue |
                  Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($newest -and $newest.LastWriteTime -gt $built) { $needBuild = $true }
    }
}

if ($needBuild) {
    # `npm ci` wipes and reinstalls node_modules — the slow part. Only when the
    # lockfile actually moved.
    $stamp = Join-Path $repo 'frontend\node_modules\.campus-lock-stamp'
    $lock  = Join-Path $repo 'frontend\package-lock.json'
    $needInstall = (-not (Test-Path (Join-Path $repo 'frontend\node_modules'))) -or (-not (Test-Path $stamp))
    if (-not $needInstall -and (Test-Path $lock)) {
        $needInstall = (Get-Item $lock).LastWriteTime -gt (Get-Item $stamp).LastWriteTime
    }
    if ($needInstall) {
        Say 'Installing frontend dependencies (lockfile changed)...'
        npm --prefix frontend ci
        New-Item -ItemType File -Path $stamp -Force | Out-Null
    }

    Say 'Building the React bundle...'
    npm --prefix frontend run build
    if (Test-Path $buildDir) { Remove-Item -Recurse -Force $buildDir }
    Copy-Item -Recurse (Join-Path $repo 'frontend\dist') $buildDir
    Say 'Bundle placed in backend\frontend_build' 'Green'
} elseif (-not $SkipFrontend) {
    Say 'Bundle is current - skipping the rebuild.' 'DarkGray'
}

# ── 6. Serve ─────────────────────────────────────────────────────────────────
Set-Location (Join-Path $repo 'backend')
Say 'Collecting static files...'
& $python manage.py collectstatic --noinput --clear | Select-Object -Last 1

# Deliberately no `migrate` — RUN_MIGRATIONS=false above, and Railway owns the
# schema for the shared database.

Write-Host ''
Say "Serving on $origin" 'Green'
Say 'Guards should use that address. Ctrl+C to stop.' 'Green'
Write-Host ''

& $python -m daphne -b 0.0.0.0 -p $Port config.asgi:application

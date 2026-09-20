<#
.SYNOPSIS
    Start the app for local development and debugging, in one step.

.DESCRIPTION
    What it does, in order:
      1. Runs the read-only health check (manage.py doctor) and shows the result.
         If anything FAILs, it asks whether to carry on.
      2. Opens a new window running the backend web server (daphne) with the
         readable logging from backend/debug_settings.py.
      3. Opens a new window running the frontend dev server (Vite, port 5173).
      4. Optionally opens a third window with the Celery background worker.

    This is for a developer's own machine. The campus deployment has its own
    launcher (scripts/run-campus.ps1); this script does not replace it.

    CAUTION: the backend uses whatever database and file storage backend/.env
    names. The health check in step 1 tells you which; if it says REMOTE or
    LIVE, what you do in the app while debugging changes real data.

    DEFAULT: BACKGROUND JOBS ARE ON. On a bare launch (no switches) the
    backend runs BOTH of these by itself, against that database:
      - the daily scheduler (automatic backup, archiving expired accounts,
        purging old records), and
      - parking-camera auto-detection (which keeps writing bay occupancy).
    That is MORE than the Railway (cloud) server runs: by default Railway
    runs only the daily scheduler, because auto-detection switches itself
    off there (the cameras cannot be reached from the cloud). The campus
    server, by default, runs both - like a bare launch of this script.
    Add -NoBackgroundJobs to turn BOTH off. The script prints
    "Background jobs: ON" or "OFF" when it starts the backend, so you can
    always see which state you are in.

.PARAMETER Port
    Port for the backend web server. Default 8000.

.PARAMETER LogLevel
    How much the backend logs: DEBUG, INFO (default), WARNING or ERROR.

.PARAMETER LogSql
    Also log every database query (very noisy).

.PARAMETER SkipDoctor
    Skip the health check in step 1.

.PARAMETER NoFrontend
    Start only the backend.

.PARAMETER WithCelery
    Also start the Celery worker (needs a reachable Redis; see README). Only
    the worker is started, not Celery's own timetable ("beat"), so it runs
    tasks the app hands it but never starts scheduled ones by itself.

.PARAMETER NoBackgroundJobs
    Turn OFF the backend's background jobs: the daily scheduler (backup,
    archive expired accounts, purge old records) and parking-camera
    auto-detection. Recommended when debugging against the shared database.
    Without this switch they are ON (see DEFAULT above).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\dev.ps1
    # background jobs ON (the default)
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\dev.ps1 -NoBackgroundJobs
    # background jobs OFF - nothing is backed up, archived, purged or detected by itself
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\dev.ps1 -LogLevel DEBUG -WithCelery
#>

# -- The options this script accepts (see the help block above) ---------------
param(
    [int]$Port = 8000,
    [ValidateSet('DEBUG', 'INFO', 'WARNING', 'ERROR')][string]$LogLevel = 'INFO',
    [switch]$LogSql,
    [switch]$SkipDoctor,
    [switch]$NoFrontend,
    [switch]$WithCelery,
    [switch]$NoBackgroundJobs
)

# Stop at the first unexpected error instead of carrying on half-started.
$ErrorActionPreference = 'Stop'

# -- Work out where everything is ----------------------------------------------
# This script lives at the repository root, so every other path starts here.
$Root     = $PSScriptRoot
$Backend  = Join-Path $Root 'backend'
$Frontend = Join-Path $Root 'frontend'
# The project's own Python, inside the backend's virtual environment.
$Python   = Join-Path $Backend 'venv\Scripts\python.exe'

# -- Check the basics before starting anything ----------------------------------
# Without the backend's Python environment nothing below can run.
if (-not (Test-Path $Python)) {
    Write-Host "Backend Python not found at $Python" -ForegroundColor Red
    Write-Host "Create it first - see README, 'Getting Started'." -ForegroundColor Red
    exit 1
}
# The frontend needs npm (it comes with Node.js), unless we are skipping it.
if (-not $NoFrontend -and -not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Host "npm not found - install Node.js, or run with -NoFrontend." -ForegroundColor Red
    exit 1
}

# -- A helper that opens a new PowerShell window running some commands ----------
# The commands are passed "encoded" (Base64) so paths with spaces, such as
# this user folder, cannot break the quoting.
function Start-Window([string]$Title, [string]$WorkingDir, [string]$Commands) {
    # Name the window, then run the commands; -NoExit keeps it open afterwards
    # so you can read any error.
    $script  = "`$Host.UI.RawUI.WindowTitle = '$Title'; $Commands"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script))
    Start-Process powershell -WorkingDirectory $WorkingDir -ArgumentList '-NoExit', '-EncodedCommand', $encoded
}

# Wrap a path in single quotes for use inside those commands (any ' inside is doubled).
function Quote([string]$Text) { "'" + ($Text -replace "'", "''") + "'" }

# The settings every backend window uses: normal settings plus readable logging.
$EnvSetup = "`$env:DJANGO_SETTINGS_MODULE = 'debug_settings'; `$env:LOG_LEVEL = '$LogLevel'; " +
            "`$env:LOG_SQL = '$(if ($LogSql) { '1' } else { '' })'; "

# -- Background jobs: decide ON or OFF, and be truthful about it ----------------------
# Two switches control them, both read by the backend when it starts:
#   DISABLE_DAILY_SCHEDULER     = 1 stops the daily backup / archive / purge jobs
#   DISABLE_PARKING_AUTODETECT  = 1 stops parking-camera auto-detection
# Read what a switch is currently set to: this window's environment wins,
# otherwise backend/.env (which is how the backend itself resolves it).
function Get-Switch([string]$Name) {
    $fromEnv = [Environment]::GetEnvironmentVariable($Name)
    if ($fromEnv) { return "$fromEnv, from environment" }
    $line = Get-Content (Join-Path $Backend '.env') -ErrorAction SilentlyContinue |
            Where-Object { $_ -match "^\s*$Name\s*=" } | Select-Object -First 1
    if ($line) { return (($line -split '=', 2)[1].Trim()) + ', from backend/.env' }
    return ''
}
# True when a switch value means "disabled" (the backend accepts 1 / true / yes).
function Test-Disabled([string]$Value) { return $Value -match '^(1|true|yes)\b' }

if ($NoBackgroundJobs) {
    # Asked for OFF: set both switches for the backend window only.
    $EnvSetup += "`$env:DISABLE_DAILY_SCHEDULER = '1'; `$env:DISABLE_PARKING_AUTODETECT = '1'; "
    $schedulerOn = $false
    $autodetectOn = $false
} else {
    # Default: leave the switches exactly as they are, and report what that means.
    $schedulerOn  = -not (Test-Disabled (Get-Switch 'DISABLE_DAILY_SCHEDULER'))
    $autodetectOn = -not (Test-Disabled (Get-Switch 'DISABLE_PARKING_AUTODETECT'))
}

# -- Step 1: health check ---------------------------------------------------------
if (-not $SkipDoctor) {
    Write-Host "`n== Health check (manage.py doctor) ==" -ForegroundColor Cyan
    # Run it in THIS window with the debug settings, then read its exit code:
    # 0 = nothing failed (warnings allowed), 1 = something failed.
    Push-Location $Backend
    try {
        $env:DJANGO_SETTINGS_MODULE = 'debug_settings'
        & $Python manage.py doctor
        $doctorExit = $LASTEXITCODE
    } finally {
        # Put this window back exactly as it was.
        Remove-Item Env:DJANGO_SETTINGS_MODULE -ErrorAction SilentlyContinue
        Pop-Location
    }
    # If something failed, let the developer decide whether to continue.
    if ($doctorExit -ne 0) {
        $answer = Read-Host "`nThe health check reported failures. Start anyway? (y/N)"
        if ($answer -notmatch '^[yY]') { exit 1 }
    }
}

# -- Step 2: backend web server ---------------------------------------------------
# First say plainly whether background jobs will run, before anything starts.
if ($schedulerOn -or $autodetectOn) {
    Write-Host "`n== Background jobs: ON ==" -ForegroundColor Yellow
    if ($schedulerOn)  { Write-Host "   Daily scheduler WILL run: automatic backup, archive expired accounts, purge old records." -ForegroundColor Yellow }
    else               { Write-Host "   Daily scheduler: off (DISABLE_DAILY_SCHEDULER=$(Get-Switch 'DISABLE_DAILY_SCHEDULER'))" -ForegroundColor Gray }
    if ($autodetectOn) { Write-Host "   Parking-camera auto-detection WILL run and keep writing bay occupancy." -ForegroundColor Yellow }
    else               { Write-Host "   Parking-camera auto-detection: off (DISABLE_PARKING_AUTODETECT=$(Get-Switch 'DISABLE_PARKING_AUTODETECT'))" -ForegroundColor Gray }
    Write-Host "   They act on the database named in backend/.env (see the health check above)." -ForegroundColor Yellow
    Write-Host "   To start without them:  .\dev.ps1 -NoBackgroundJobs" -ForegroundColor Yellow
} else {
    Write-Host "`n== Background jobs: OFF ==" -ForegroundColor Green
    Write-Host "   Nothing is backed up, archived, purged or auto-detected by itself in this backend." -ForegroundColor Green
}

Write-Host "`n== Starting backend on http://127.0.0.1:$Port (new window) ==" -ForegroundColor Cyan
Start-Window 'SLC VMS - backend' $Backend (
    $EnvSetup + "& $(Quote $Python) -m daphne -b 127.0.0.1 -p $Port config.asgi:application")

# -- Step 3: frontend dev server ---------------------------------------------------
if (-not $NoFrontend) {
    Write-Host "== Starting frontend on http://localhost:5173 (new window) ==" -ForegroundColor Cyan
    Start-Window 'SLC VMS - frontend' $Frontend 'npm run dev'
}

# -- Step 4 (optional): Celery background worker ------------------------------------
# --pool=solo is required on Windows (see README, "Running the Project").
if ($WithCelery) {
    Write-Host "== Starting Celery worker (new window) ==" -ForegroundColor Cyan
    Start-Window 'SLC VMS - celery' $Backend (
        $EnvSetup + "& $(Quote $Python) -m celery -A config worker -l info --pool=solo")
}

# -- Where to look next -------------------------------------------------------------
Write-Host "`nLogs: $(Join-Path $Backend 'logs') (server.log, celery.log, manage.log)" -ForegroundColor Gray
Write-Host "Stop everything by closing the windows (or Ctrl+C in each)." -ForegroundColor Gray

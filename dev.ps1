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

.PARAMETER SimClock
    Start the INSTRUCTOR DEMO instead: a local copy of the system (database
    slc_sim_demo on this PC's PostgreSQL, never the live one) whose date can
    be moved from the admin sidebar's Test Clock page. Uses
    backend/sim_settings.py. The health check is skipped and parking-camera
    auto-detection is off; the daily scheduler runs, so time-based jobs fire
    by themselves. Emails go to SIM_EMAIL_TO from backend/.env, or are only
    printed in the backend window when it is not set.

    The demo uses its own ports, backend 8765 and page 5174, never 8000/5173:
    the installed campus app listens on 8000 against the LIVE database, and a
    demo page proxying to it would show the real system. If either demo port
    is taken the script stops. The browser opens at http://127.0.0.1:5174.

.PARAMETER SimSetup
    With -SimClock: create the demo database first (a copy of the screenshot
    demo database, slc_manual_demo), apply migrations, open a registration
    period for the current school year and set the clock to the real date.
    Safe to repeat: an existing demo database is kept.

.PARAMETER Reset
    With -SimClock -SimSetup: drop the demo database and start over.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\dev.ps1
    # background jobs ON (the default)
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\dev.ps1 -SimClock -SimSetup
    # the instructor demo on its own local database, with the Test Clock
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
    [switch]$NoBackgroundJobs,
    [switch]$SimClock,
    [switch]$SimSetup,
    [switch]$Reset
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

# The settings every backend window uses: normal settings plus readable logging,
# or for the instructor demo the same on its own local database and moved clock.
$SettingsModule = if ($SimClock) { 'sim_settings' } else { 'debug_settings' }
$EnvSetup = "`$env:DJANGO_SETTINGS_MODULE = '$SettingsModule'; `$env:LOG_LEVEL = '$LogLevel'; " +
            "`$env:LOG_SQL = '$(if ($LogSql) { '1' } else { '' })'; "

# The instructor demo's own ports (see .PARAMETER SimClock).
$FrontendPort = 5173
if ($SimClock) {
    if (-not $PSBoundParameters.ContainsKey('Port')) { $Port = 8765 }
    $FrontendPort = 5174
    foreach ($p in $Port, $FrontendPort) {
        $busy = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($busy) {
            $owner = (Get-CimInstance Win32_Process -Filter "ProcessId=$($busy.OwningProcess)").CommandLine
            Write-Host "`nPort $p is already in use, so the demo cannot start safely:" -ForegroundColor Red
            Write-Host "   $owner" -ForegroundColor Red
            Write-Host "   Close that window (an earlier demo?) and run this again." -ForegroundColor Red
            exit 1
        }
    }
}

if ($SimClock) {
    # The demo's own database never has cameras to watch, and the doctor
    # checks the normal settings' database, which the demo does not use.
    $EnvSetup += "`$env:DISABLE_PARKING_AUTODETECT = '1'; "
    $SkipDoctor = $true
    Write-Host "`n== INSTRUCTOR DEMO: simulated clock, local database slc_sim_demo ==" -ForegroundColor Red
    Write-Host "   Nothing here touches the live database. Move the date from the admin" -ForegroundColor Red
    Write-Host "   sidebar's Test Clock page, or:  python manage.py sim_clock advance 1d" -ForegroundColor Red
    if ($SimSetup) {
        Write-Host "`n== Setting up the demo database ==" -ForegroundColor Cyan
        Push-Location $Backend
        try {
            $env:DJANGO_SETTINGS_MODULE = 'sim_settings'
            $setupArgs = @('manage.py', 'sim_setup')
            if ($Reset) { $setupArgs += '--reset' }
            & $Python @setupArgs
            if ($LASTEXITCODE -ne 0) { Write-Host 'Demo setup failed (see above).' -ForegroundColor Red; exit 1 }
        } finally {
            Remove-Item Env:DJANGO_SETTINGS_MODULE -ErrorAction SilentlyContinue
            Pop-Location
        }
    }
} elseif ($SimSetup -or $Reset) {
    Write-Host '-SimSetup and -Reset only apply together with -SimClock.' -ForegroundColor Red
    exit 1
}

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
if ($SimClock) { $autodetectOn = $false }       # switched off for the demo above

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
    elseif ($SimClock) { Write-Host "   Parking-camera auto-detection: off (the demo has no cameras)" -ForegroundColor Gray }
    else               { Write-Host "   Parking-camera auto-detection: off (DISABLE_PARKING_AUTODETECT=$(Get-Switch 'DISABLE_PARKING_AUTODETECT'))" -ForegroundColor Gray }
    if ($SimClock) { Write-Host "   They act on the demo database slc_sim_demo only." -ForegroundColor Yellow }
    else           { Write-Host "   They act on the database named in backend/.env (see the health check above)." -ForegroundColor Yellow }
    Write-Host "   To start without them:  .\dev.ps1 -NoBackgroundJobs" -ForegroundColor Yellow
} else {
    Write-Host "`n== Background jobs: OFF ==" -ForegroundColor Green
    Write-Host "   Nothing is backed up, archived, purged or auto-detected by itself in this backend." -ForegroundColor Green
}

Write-Host "`n== Starting backend on http://127.0.0.1:$Port (new window) ==" -ForegroundColor Cyan
Start-Window $(if ($SimClock) { 'SLC VMS - backend (SIMULATED CLOCK DEMO)' } else { 'SLC VMS - backend' }) $Backend (
    $EnvSetup + "& $(Quote $Python) -m daphne -b 127.0.0.1 -p $Port config.asgi:application")

# -- Step 3: frontend dev server ---------------------------------------------------
if (-not $NoFrontend) {
    if ($SimClock) {
        # Pointed at the demo backend explicitly: the default (8000) may be
        # the live campus app.
        Write-Host "== Starting frontend on http://127.0.0.1:$FrontendPort (new window) ==" -ForegroundColor Cyan
        Start-Window 'SLC VMS - frontend (SIMULATED CLOCK DEMO)' $Frontend (
            "`$env:BACKEND_URL = 'http://127.0.0.1:$Port'; npx vite --host 127.0.0.1 --port $FrontendPort --strictPort")
    } else {
        Write-Host "== Starting frontend on http://localhost:5173 (new window) ==" -ForegroundColor Cyan
        Start-Window 'SLC VMS - frontend' $Frontend 'npm run dev'
    }
}

# -- Step 4 (optional): Celery background worker ------------------------------------
# --pool=solo is required on Windows (see README, "Running the Project").
if ($WithCelery) {
    Write-Host "== Starting Celery worker (new window) ==" -ForegroundColor Cyan
    Start-Window 'SLC VMS - celery' $Backend (
        $EnvSetup + "& $(Quote $Python) -m celery -A config worker -l info --pool=solo")
}

# -- The demo: open it once both halves answer ---------------------------------------
if ($SimClock -and -not $NoFrontend) {
    Write-Host "`nWaiting for the demo to come up..." -ForegroundColor Gray
    $url = "http://127.0.0.1:$FrontendPort"
    $ready = $false
    foreach ($i in 1..60) {
        try {
            $api = Invoke-WebRequest "$url/api/deployment/" -UseBasicParsing -TimeoutSec 3
            if ($api.Content -match '"sim_clock"') { $ready = $true; break }
        } catch { }
        Start-Sleep -Seconds 2
    }
    if ($ready) {
        Write-Host "Instructor demo ready: $url  (admin cdso.demo@slc-sflu.edu.ph / Demo@2026!)" -ForegroundColor Green
        Start-Process $url
    } else {
        Write-Host "The demo did not answer at $url within 2 minutes; check the two new windows for errors." -ForegroundColor Red
    }
}

# -- Where to look next -------------------------------------------------------------
Write-Host "`nLogs: $(Join-Path $Backend 'logs') (server.log, celery.log, manage.log)" -ForegroundColor Gray
Write-Host "Stop everything by closing the windows (or Ctrl+C in each)." -ForegroundColor Gray

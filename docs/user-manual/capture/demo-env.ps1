# Dot-source before any manage.py call for the manual screenshots:
#
#     . docs\user-manual\capture\demo-env.ps1
#
# The database itself is pinned in demo_settings.py rather than through an
# environment variable, because an env var set to '' is DELETED in PowerShell
# and load_dotenv then refills it from backend\.env - which points at Neon.
# Pinning it in the settings module is the only way it cannot be refilled.
#
# The local PostgreSQL role reuses the DB_USER / DB_PASSWORD from backend\.env
# so there is no second password to keep anywhere. Only the HOST differs:
# 127.0.0.1, database slc_manual_demo.

$repo    = (Resolve-Path "$PSScriptRoot\..\..\..").Path
$capture = $PSScriptRoot

$kv = @{}
Get-Content "$repo\backend\.env" | ForEach-Object {
    if ($_ -match '^\s*([A-Z0-9_]+)\s*=\s*(.*)$') { $kv[$matches[1]] = $matches[2].Trim('"', "'") }
}

$env:DEMO_PG_USER     = $kv.DB_USER
$env:DEMO_PG_PASSWORD = $kv.DB_PASSWORD
$env:PGPASSWORD       = $kv.DB_PASSWORD
$env:DEMO_MEDIA_ROOT  = "$capture\demo_media"
$env:PYTHONPATH       = $capture
$env:DJANGO_SETTINGS_MODULE = 'demo_settings'

# Both off: the manual's figures must show a database that only the capture
# script changes. The scheduler would archive and purge rows mid-run, and
# auto-detection would keep rewriting bay occupancy between the screenshot and
# the callout measurements taken from it.
$env:DISABLE_DAILY_SCHEDULER    = 'true'
$env:DISABLE_PARKING_AUTODETECT = 'true'

$py = "$repo\backend\venv\Scripts\python.exe"

Write-Host "demo env ready - database slc_manual_demo on 127.0.0.1, media in $env:DEMO_MEDIA_ROOT"

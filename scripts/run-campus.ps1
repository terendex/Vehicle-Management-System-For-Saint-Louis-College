<#
    run-campus.ps1 — start the on-site half of the hybrid deployment.

    Builds the React bundle, places it where Django serves it, and starts Daphne
    bound to the LAN so guards can reach it from any campus machine.

    Run from the repository root:
        powershell -ExecutionPolicy Bypass -File scripts\run-campus.ps1

    Requires backend\.env to exist (copy backend\.env.campus.example).
#>

[CmdletBinding()]
param(
    # Port to serve on. 8000 matches the URLs in .env.campus.example.
    [int]$Port = 8000,

    # Skip the frontend rebuild when only backend code changed — saves ~30s.
    [switch]$SkipFrontend
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$envFile = Join-Path $repo 'backend\.env'
if (-not (Test-Path $envFile)) {
    Write-Error "backend\.env not found. Copy backend\.env.campus.example to backend\.env and fill it in."
}

# The campus box must be able to reach the cameras; warn early rather than
# letting the guard discover it at the gate.
Write-Host '[campus] Checking camera reachability...' -ForegroundColor Cyan
foreach ($ip in @('192.168.137.83', '192.168.137.86')) {
    if (Test-Connection -ComputerName $ip -Count 1 -Quiet -ErrorAction SilentlyContinue) {
        Write-Host "  reachable : $ip" -ForegroundColor Green
    } else {
        Write-Host "  NO ROUTE  : $ip  (live scanning from this machine will not work)" -ForegroundColor Yellow
    }
}

if (-not $SkipFrontend) {
    Write-Host '[campus] Building the React bundle...' -ForegroundColor Cyan
    npm --prefix frontend ci
    npm --prefix frontend run build

    $buildDir = Join-Path $repo 'backend\frontend_build'
    if (Test-Path $buildDir) { Remove-Item -Recurse -Force $buildDir }
    Copy-Item -Recurse (Join-Path $repo 'frontend\dist') $buildDir
    Write-Host '[campus] Bundle placed in backend\frontend_build' -ForegroundColor Green
}

Set-Location (Join-Path $repo 'backend')
$python = Join-Path $repo 'backend\venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }

Write-Host '[campus] Collecting static files...' -ForegroundColor Cyan
& $python manage.py collectstatic --noinput --clear | Select-Object -Last 1

# Deliberately no `migrate` here — Railway owns the schema for the shared
# database (see RUN_MIGRATIONS in .env.campus.example).

$lan = (Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object { $_.IPAddress -like '192.168.*' } |
        Select-Object -First 1).IPAddress
Write-Host ''
Write-Host "[campus] Serving on http://${lan}:$Port" -ForegroundColor Green
Write-Host '[campus] Guards should use that address. Ctrl+C to stop.' -ForegroundColor Green
Write-Host ''

& $python -m daphne -b 0.0.0.0 -p $Port config.asgi:application

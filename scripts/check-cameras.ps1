<#
    check-cameras.ps1 - can this machine reach each camera registered in the database?

    Prints one line per camera, in the shapes the launcher's status regex reads:
        reachable : <name> (<host>)
        NO ROUTE  : <name> (<host>) - live scanning will not work from this machine
    or "no cameras registered yet", or "could not read the camera list:".

    Its own script rather than inlined in run-campus.ps1, because the launcher
    reruns it while the server is up. Checked only at startup, the Cameras pill
    kept saying "none added" long after an admin had registered one.
#>
param(
    [Parameter(Mandatory=$true)][string]$Repo
)

$python = Join-Path $Repo 'backend\venv\Scripts\python.exe'

# Via a management command, not `python -c "..."`: PowerShell strips the quotes
# out of a multi-line string when handing it to a native exe, and the inline
# version died with a SyntaxError. Errors are shown rather than swallowed —
# hiding them is what let that failure masquerade as "no cameras registered".
Push-Location (Join-Path $Repo 'backend')
# ErrorActionPreference is dropped to Continue for exactly this call. With it
# at Stop, `2>&1` on a NATIVE command turns every stderr line into a
# terminating ErrorRecord - so a Django warning, or a database it cannot reach,
# killed the caller with a NativeCommandError instead of falling through to the
# "could not read the camera list" branch below that exists to handle precisely
# that. The server then never started.
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    $camOut = & $python manage.py camera_hosts 2>&1
    $camOk  = ($LASTEXITCODE -eq 0)
} catch {
    $camOut = @($_.Exception.Message)
    $camOk  = $false
} finally {
    $ErrorActionPreference = $prevEAP
    Pop-Location
}

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

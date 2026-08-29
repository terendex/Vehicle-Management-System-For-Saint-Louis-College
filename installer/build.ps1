<#
    build.ps1 - produce installer\out\SLC-Smart-Parking-Campus-Setup.exe.

        powershell -ExecutionPolicy Bypass -File installer\build.ps1

    Regenerates the artwork from the app's own logo, then compiles the Inno
    script. Inno Setup itself is fetched through winget if it is not already
    installed, so a clean machine needs nothing prepared by hand.

    You do NOT need to rebuild this for an ordinary code change. The installer
    carries no application code - it clones the repository and the installed
    copy updates itself from the branch it was told to track. Reissue it only
    when something in installer\ changes: the bootstrap, the launcher shortcut,
    the artwork, or the version below.

    Flags
      -Version <x.y.z>  stamp a version into the setup and its file properties
      -SkipAssets       reuse installer\assets as-is
      -Sign             run signtool over the finished exe (needs SIGNTOOL_ARGS)
      -UiTest           throwaway unelevated build, for walking the wizard
                        pages on a machine where a UAC prompt cannot be
                        answered. Installs nowhere useful. Never ship it.
      -WithCredentials  bake installer\credentials.dat into the setup, so it
                        installs with no credential entry at all. Produces
                        SLC-Smart-Parking-Campus-Setup-CONFIGURED.exe.
                        Run
                        embed-credentials.ps1 first. READ ITS HEADER: the
                        result is as sensitive as the credentials themselves,
                        because anyone holding it can recover them.
#>

[CmdletBinding()]
param(
    [string]$Version,
    [switch]$SkipAssets,
    [switch]$Sign,
    [switch]$UiTest,
    [switch]$WithCredentials
)

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$iss  = Join-Path $here 'SLC-VMS-Campus.iss'
$out  = Join-Path $here 'out'

function Say($m, $c = 'Cyan') { Write-Host "[build] $m" -ForegroundColor $c }

# --- Inno Setup -------------------------------------------------------------
function Find-Iscc {
    $c = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }

    # Its own uninstall key is the only location that stays right. Inno 6.7
    # moved the default from Program Files to a per-user directory, so a build
    # script that only knows the Program Files paths reports the compiler
    # missing on a machine where winget has just installed it successfully.
    foreach ($root in @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
                        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall',
                        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall')) {
        foreach ($key in @(Get-ChildItem $root -ErrorAction SilentlyContinue |
                           Where-Object { $_.PSChildName -like 'Inno Setup*_is1' })) {
            $loc = (Get-ItemProperty $key.PSPath -ErrorAction SilentlyContinue).InstallLocation
            if ($loc) {
                $exe = Join-Path $loc 'ISCC.exe'
                if (Test-Path $exe) { return $exe }
            }
        }
    }

    foreach ($p in @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
                     "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
                     "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
                     "${env:ProgramFiles(x86)}\Inno Setup 5\ISCC.exe")) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

$iscc = Find-Iscc
if (-not $iscc) {
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        Write-Error 'Inno Setup is not installed and winget is unavailable. Install it from https://jrsoftware.org/isdl.php and re-run.'
    }
    Say 'Inno Setup not found - installing it with winget (one time)...' 'Yellow'
    & winget install --id JRSoftware.InnoSetup -e --source winget `
        --accept-package-agreements --accept-source-agreements --silent
    $iscc = Find-Iscc
    if (-not $iscc) { Write-Error 'winget finished but ISCC.exe is still not on this machine.' }
}
Say "Compiler: $iscc"

# --- artwork ----------------------------------------------------------------
if ($SkipAssets) {
    Say 'Reusing installer\assets.' 'DarkGray'
} else {
    Say 'Generating artwork from frontend\src\assets\slclogo.jpg...'
    & (Join-Path $here 'make-assets.ps1')
}

foreach ($required in @('assets\slc-vms.ico', 'assets\wizard-large.bmp', 'assets\wizard-small.bmp',
                        'assets\slclogo.jpg', 'bootstrap.ps1', 'start-campus.ps1', 'start-campus.vbs',
                        'LICENSE.txt')) {
    if (-not (Test-Path (Join-Path $here $required))) { Write-Error "Missing installer\$required" }
}

# --- compile ----------------------------------------------------------------
if (-not (Test-Path $out)) { New-Item -ItemType Directory -Path $out -Force | Out-Null }

# Deliberately NOT /Q. Quiet mode suppresses compiler warnings, and one of them
# was a real bug: Inno warned that a per-user area ({userstartup}) was being
# written by an elevated install, which would have put the auto-start shortcut
# in the installing administrator's Startup folder instead of the guard's.
# Warnings are surfaced below rather than hidden.
$isccArgs = @()
if ($Version) { $isccArgs += "/DAppVersion=$Version" }
if ($UiTest) {
    $isccArgs += '/DUITEST'
    Say 'UI-TEST BUILD - runs unelevated, installs nowhere useful. Do not ship it.' 'Yellow'
}
if ($WithCredentials) {
    $blob = Join-Path $here 'credentials.dat'
    if (-not (Test-Path $blob)) {
        Write-Error 'No installer\credentials.dat. Run embed-credentials.ps1 -FromEnv first.'
    }
    $isccArgs += '/DWITHCREDS'
    Write-Host ''
    Write-Host '  ###############################################################' -ForegroundColor Red
    Write-Host '  #  BUILDING AN INSTALLER THAT CARRIES LIVE CREDENTIALS         #' -ForegroundColor Red
    Write-Host '  #                                                             #' -ForegroundColor Red
    Write-Host '  #  Obfuscated, NOT encrypted - the key ships with it. Anyone   #' -ForegroundColor Red
    Write-Host '  #  who gets this exe can recover the Neon DATABASE_URL and     #' -ForegroundColor Red
    Write-Host '  #  the JWT SECRET_KEY. Hand it only to people you would give   #' -ForegroundColor Red
    Write-Host '  #  direct database access. Never upload it anywhere.           #' -ForegroundColor Red
    Write-Host '  ###############################################################' -ForegroundColor Red
    Write-Host ''
}
$isccArgs += "`"$iss`""

Say 'Compiling...'
Push-Location $here
try {
    $isccOut = & $iscc @isccArgs 2>&1
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($code -ne 0) {
    $isccOut | Select-Object -Last 20 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
    Write-Error "ISCC exited with $code"
}

$warnings = @($isccOut | Select-String -Pattern 'Warning:|Deprecated')
if ($warnings.Count) {
    Write-Host ''
    Say "$($warnings.Count) compiler warning(s) - read them, they are not noise:" 'Yellow'
    $warnings | ForEach-Object { Write-Host "  $($_.Line.Trim())" -ForegroundColor Yellow }
    Write-Host ''
}

$exe = Join-Path $out $(
    if     ($UiTest)          { 'SLC-Smart-Parking-Campus-Setup-UITEST.exe' }
    elseif ($WithCredentials) { 'SLC-Smart-Parking-Campus-Setup-CONFIGURED.exe' }
    else                      { 'SLC-Smart-Parking-Campus-Setup.exe' })
if (-not (Test-Path $exe)) { Write-Error 'The compiler reported success but produced no exe.' }

# --- optional signing -------------------------------------------------------
# Unsigned, Windows SmartScreen warns the first few hundred people who run it.
# That is survivable for an installer handed to one campus IT office, and a
# code-signing certificate is a real yearly cost - so this is opt-in rather
# than a hard requirement.
if ($Sign) {
    if (-not $env:SIGNTOOL_ARGS) {
        Write-Error 'Set SIGNTOOL_ARGS to the signtool arguments for your certificate, e.g. "sign /fd SHA256 /a /tr http://timestamp.digicert.com /td SHA256"'
    }
    Say 'Signing...'
    & signtool.exe ($env:SIGNTOOL_ARGS -split ' ') $exe
    if ($LASTEXITCODE -ne 0) { Write-Error "signtool exited with $LASTEXITCODE" }
}

$size = [math]::Round((Get-Item $exe).Length / 1MB, 2)
Write-Host ''
Say "Built $exe  ($size MB)" 'Green'
Write-Host ''
Write-Host '  Give this file to the campus machine. On first run it installs Git,' -ForegroundColor DarkGray
Write-Host '  Python 3.11 and Node, clones the repository, opens the firewall port,' -ForegroundColor DarkGray
Write-Host '  and leaves a launcher that checks the branch for updates every few' -ForegroundColor DarkGray
Write-Host '  minutes.' -ForegroundColor DarkGray
Write-Host ''

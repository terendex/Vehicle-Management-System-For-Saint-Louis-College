<#
    start-campus.ps1 - the stable entry point the Start Menu shortcut runs.

    Deliberately thin, and deliberately NOT part of the git checkout. Everything
    that changes lives in <install>\app, which updates itself; this file has to
    keep working across every one of those updates, so it does exactly two
    things:

        * if the application folder is missing or incomplete, run the installer
          bootstrap again to repair it
        * otherwise hand off to the launcher inside the checkout

    That second point is what makes the launcher itself updatable: the shortcut
    points here, here points into the checkout, and a `git pull` that rewrites
    campus-launcher.ps1 changes what starts next time without touching anything
    the installer owns.
#>

[CmdletBinding()]
param([switch]$Repair)

$ErrorActionPreference = 'Stop'
$here    = $PSScriptRoot
$install = Split-Path -Parent $here
$app     = Join-Path $install 'app'
$entry   = Join-Path $app 'scripts\campus-launcher.ps1'

if ($Repair -or -not (Test-Path $entry)) {
    $bootstrap = Join-Path $here 'bootstrap.ps1'
    if (-not (Test-Path $bootstrap)) {
        [void][System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms')
        [System.Windows.Forms.MessageBox]::Show(
            "The application folder is missing and the repair tool is not there either.`n`n" +
            "Expected: $entry`n`nReinstall the Smart Parking and Vehicle Verification System to fix this.",
            'Smart Parking and Vehicle Verification System', 'OK', 'Error') | Out-Null
        exit 1
    }
    # Elevated: repair reinstalls prerequisites and touches the firewall rule.
    # -WindowStyle Hidden so the elevated host does not flash a console before
    # bootstrap.ps1 draws its own window.
    Start-Process powershell.exe -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
        '-File', "`"$bootstrap`"", '-InstallDir', "`"$install`""
    )
    if (-not (Test-Path $entry)) { exit 1 }
}

& $entry

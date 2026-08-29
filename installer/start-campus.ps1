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
    #
    # Wrapped because declining the UAC prompt makes RunAs THROW rather than
    # return a failure. Unhandled, that killed this script with no console and
    # no window - which looks exactly like the app crashing on launch.
    try {
        Start-Process powershell.exe -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
            '-File', "`"$bootstrap`"", '-InstallDir', "`"$install`""
        )
    } catch {
        [void][System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms')
        [System.Windows.Forms.MessageBox]::Show(
            "This machine still needs to finish setting up, and that needs administrator approval." +
            [Environment]::NewLine + [Environment]::NewLine +
            "Repair was cancelled, so the application has not been downloaded yet. Run " +
            """Repair installation"" from the Start Menu and approve the prompt.",
            'Smart Parking and Vehicle Verification System', 'OK', 'Warning') | Out-Null
        exit 1
    }

    if (-not (Test-Path $entry)) {
        [void][System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms')
        [System.Windows.Forms.MessageBox]::Show(
            "Setup finished but the application is still not there." + [Environment]::NewLine + [Environment]::NewLine +
            "Expected: $entry" + [Environment]::NewLine + [Environment]::NewLine +
            "Open the setup window's Details pane next time, or reinstall.",
            'Smart Parking and Vehicle Verification System', 'OK', 'Error') | Out-Null
        exit 1
    }
}

# Everything from here runs under wscript with no console, so an unhandled
# error would kill the process with nothing on screen and nothing written down -
# the window simply never appears, which is indistinguishable from "it crashed".
# Catch it, write it where it can be found, and say so.
try {
    & $entry
} catch {
    $logDir = Join-Path $env:LOCALAPPDATA 'SLC-VMS\logs'
    if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
    $crash = Join-Path $logDir ('crash-' + (Get-Date -Format 'yyyy-MM-dd_HHmmss') + '.txt')

    $detail = @(
        "Smart Parking and Vehicle Verification System - launcher crash"
        (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
        ""
        "Entry point : $entry"
        "PowerShell  : $($PSVersionTable.PSVersion)"
        ""
        "Message     : $($_.Exception.Message)"
        "Type        : $($_.Exception.GetType().FullName)"
        "At          : $($_.InvocationInfo.ScriptName):$($_.InvocationInfo.ScriptLineNumber)"
        "Line        : $($_.InvocationInfo.Line.Trim())"
        ""
        "--- stack ---"
        $_.ScriptStackTrace
        ""
        "--- .NET stack ---"
        $_.Exception.StackTrace
    ) -join [Environment]::NewLine
    Set-Content -Path $crash -Value $detail -Encoding UTF8

    [void][System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms')
    [System.Windows.Forms.MessageBox]::Show(
        "The launcher could not start." + [Environment]::NewLine + [Environment]::NewLine +
        $_.Exception.Message + [Environment]::NewLine + [Environment]::NewLine +
        "Details were written to:" + [Environment]::NewLine + $crash,
        'Smart Parking and Vehicle Verification System', 'OK', 'Error') | Out-Null
    exit 1
}

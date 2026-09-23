# Polls the launcher window (read-only, via UI Automation) until it reports
# RUNNING, then captures it. Gives up after ~12 minutes.
$s = Split-Path -Parent $MyInvocation.MyCommand.Path
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$AE = [System.Windows.Automation.AutomationElement]
$title = 'Smart Parking and Vehicle Verification System - Campus'
for ($i = 0; $i -lt 48; $i++) {
    $cond = New-Object System.Windows.Automation.PropertyCondition($AE::NameProperty, $title)
    $win = $AE::RootElement.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
    if ($win) {
        $texts = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition) |
                 ForEach-Object { $_.Current.Name }
        $state = @($texts | Where-Object { $_ -in 'RUNNING', 'STARTING', 'STOPPED', 'STOPPING' })[0]
        "poll $i : $state"
        if ($state -eq 'RUNNING') {
            Start-Sleep -Seconds 6   # let the pills and address settle
            $pidL = $win.Current.ProcessId
            powershell -NoProfile -ExecutionPolicy Bypass -File "$s\wincap.ps1" -Action capture -TitleLike $title -ProcessId $pidL -Out "$s\desktop\launcher_live.png"
            'CAPTURED RUNNING'
            exit 0
        }
    } else { "poll $i : no launcher window" }
    Start-Sleep -Seconds 15
}
'TIMED OUT waiting for RUNNING'

# Window capture helpers: list top-level windows, PrintWindow a window to PNG,
# and dump UI Automation element rectangles relative to the window.
param(
    [string]$Action = 'list',
    [string]$TitleLike = '',
    [string]$Out = '',
    [int]$ProcessId = 0
)
Add-Type -AssemblyName System.Drawing, UIAutomationClient, UIAutomationTypes
Add-Type @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class W {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr l);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("dwmapi.dll")] public static extern int DwmGetWindowAttribute(IntPtr h, int a, out RECT r, int size);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
}
'@
[void][W]::SetProcessDPIAware()

function Get-Windows {
    $list = New-Object System.Collections.ArrayList
    [W]::EnumWindows({ param($h, $l)
        if ([W]::IsWindowVisible($h)) {
            $sb = New-Object System.Text.StringBuilder 512
            [void][W]::GetWindowText($h, $sb, 512)
            if ($sb.Length -gt 0) {
                $p = 0; [void][W]::GetWindowThreadProcessId($h, [ref]$p)
                $r = New-Object W+RECT; [void][W]::GetWindowRect($h, [ref]$r)
                [void]$list.Add([pscustomobject]@{ Handle = $h; Pid = $p; Title = $sb.ToString(); W = $r.R - $r.L; H = $r.B - $r.T })
            }
        }
        return $true }, [IntPtr]::Zero) | Out-Null
    $list
}

$wins = Get-Windows
if ($Action -eq 'list') { $wins | Format-Table Pid, W, H, Title -AutoSize | Out-String -Width 200; return }

$win = $wins | Where-Object { $_.Title -like $TitleLike -and ($ProcessId -eq 0 -or $_.Pid -eq $ProcessId) } | Select-Object -First 1
if (-not $win) { throw "no window like '$TitleLike'" }
$h = $win.Handle

$wr = New-Object W+RECT; [void][W]::GetWindowRect($h, [ref]$wr)
# DWMWA_EXTENDED_FRAME_BOUNDS (9): the visible frame, without the invisible resize border.
$fr = New-Object W+RECT; [void][W]::DwmGetWindowAttribute($h, 9, [ref]$fr, 16)

if ($Action -eq 'capture') {
    $bw = $wr.R - $wr.L; $bh = $wr.B - $wr.T
    $bmp = New-Object System.Drawing.Bitmap $bw, $bh
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $hdc = $g.GetHdc()
    [void][W]::PrintWindow($h, $hdc, 2)   # PW_RENDERFULLCONTENT
    $g.ReleaseHdc($hdc); $g.Dispose()
    $cx = $fr.L - $wr.L; $cy = $fr.T - $wr.T
    $crop = $bmp.Clone((New-Object System.Drawing.Rectangle $cx, $cy, ($fr.R - $fr.L), ($fr.B - $fr.T)), $bmp.PixelFormat)
    $crop.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
    "saved $Out $($crop.Width)x$($crop.Height)"
    $bmp.Dispose(); $crop.Dispose()
}

if ($Action -eq 'elements') {
    $root = [System.Windows.Automation.AutomationElement]::FromHandle($h)
    $all = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    $rows = foreach ($e in $all) {
        $c = $e.Current
        $b = $c.BoundingRectangle
        if ($b.IsEmpty -or $b.Width -lt 2) { continue }
        [pscustomobject]@{
            type = $c.ControlType.ProgrammaticName -replace 'ControlType\.', ''
            name = $c.Name; id = $c.AutomationId
            x = [int]($b.X - $fr.L); y = [int]($b.Y - $fr.T); w = [int]$b.Width; h = [int]$b.Height
        }
    }
    if ($Out) { $rows | ConvertTo-Json -Depth 3 | Set-Content -Encoding utf8 $Out; "wrote $($rows.Count) elements" }
    else { $rows | Format-Table -AutoSize | Out-String -Width 250 }
}

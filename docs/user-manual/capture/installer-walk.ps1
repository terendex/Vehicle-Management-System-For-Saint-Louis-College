# Walks the UITEST installer wizard page by page, capturing each one, and
# cancels at the Ready page so nothing is ever installed.
$ErrorActionPreference = 'Stop'
$scratch = Split-Path -Parent $MyInvocation.MyCommand.Path
$exe = 'C:\Users\axel jonas tangalin\Documents\Test Project\Vehicle-Management-System-For-Saint-Louis-College\installer\out\SLC-Smart-Parking-Campus-Setup-UITEST.exe'
$outDir = Join-Path $scratch 'desktop\installer'
Remove-Item "$outDir\*" -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $outDir | Out-Null

Add-Type @'
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public static class C {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr p, EnumProc cb, IntPtr l);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool IsWindowEnabled(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
  public static string Text(IntPtr h) { var s = new StringBuilder(512); GetWindowText(h, s, 512); return s.ToString(); }
  public static string Cls(IntPtr h) { var s = new StringBuilder(256); GetClassName(h, s, 256); return s.ToString(); }
  public static List<IntPtr> Tops() { var l = new List<IntPtr>(); EnumWindows((h, x) => { if (IsWindowVisible(h)) l.Add(h); return true; }, IntPtr.Zero); return l; }
  public static List<IntPtr> Kids(IntPtr p) { var l = new List<IntPtr>(); EnumChildWindows(p, (h, x) => { l.Add(h); return true; }, IntPtr.Zero); return l; }
}
'@

$proc = Start-Process -FilePath $exe -PassThru
Start-Sleep -Seconds 4

function Get-SetupWindow {
    foreach ($h in [C]::Tops()) {
        $p = 0; [void][C]::GetWindowThreadProcessId($h, [ref]$p)
        $pr = Get-Process -Id $p -ErrorAction SilentlyContinue
        if ($pr -and $pr.ProcessName -like 'SLC-Smart-Parking-Campus-Setup-UITEST*') {
            $t = [C]::Text($h)
            if ($t) { return [pscustomobject]@{ H = $h; Title = $t; Pid = $p } }
        }
    }
}

function Click-Child($h, [string]$pattern) {
    foreach ($k in [C]::Kids($h)) {
        $t = ([C]::Text($k) -replace '&', '').Trim()
        if ($t -match $pattern -and [C]::IsWindowVisible($k) -and [C]::IsWindowEnabled($k)) {
            [void][C]::SendMessage($k, 0x00F5, [IntPtr]::Zero, [IntPtr]::Zero)   # BM_CLICK
            return $true
        }
    }
    return $false
}

$page = 0
for ($i = 0; $i -lt 20; $i++) {
    $w = $null
    for ($t = 0; $t -lt 30 -and -not $w; $t++) { $w = Get-SetupWindow; if (-not $w) { Start-Sleep -Milliseconds 500 } }
    if (-not $w) { 'no setup window'; break }
    Start-Sleep -Milliseconds 1500
    $page++
    $name = '{0:D2}' -f $page
    & powershell -NoProfile -ExecutionPolicy Bypass -File "$scratch\wincap.ps1" -Action capture -TitleLike $w.Title -ProcessId $w.Pid -Out "$outDir\$name.png" | Out-Null
    & powershell -NoProfile -ExecutionPolicy Bypass -File "$scratch\wincap.ps1" -Action elements -TitleLike $w.Title -ProcessId $w.Pid -Out "$outDir\$name.json" | Out-Null
    $visible = @([C]::Kids($w.H) | Where-Object { [C]::IsWindowVisible($_) } | ForEach-Object { ([C]::Text($_) -replace '&', '').Trim() } | Where-Object { $_ })
    "page $name : $($w.Title) :: $($visible[0..3] -join ' | ')"

    if ($visible -contains 'Install') {
        'reached Ready page - cancelling'
        break
    }
    [void](Click-Child $w.H '^I accept the agreement$')
    Start-Sleep -Milliseconds 300
    if (-not (Click-Child $w.H '^(Next >|Next|OK|Yes)$')) { "no Next on page $name"; break }
    Start-Sleep -Seconds 2
}

# Stop at the Ready page: nothing has been installed yet. (Clicking Cancel
# blocks this script on Setup's own 'Exit Setup?' dialog.)

Get-Process | Where-Object { $_.ProcessName -like 'SLC-Smart-Parking-Campus-Setup-UITEST*' } | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
'remaining: ' + @(Get-Process | Where-Object { $_.ProcessName -like 'SLC-Smart-Parking-Campus-Setup-UITEST*' }).Count

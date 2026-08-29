<#
    bootstrap.ps1 - the setup stage the Inno wizard hands off to.

    Inno can copy files and write registry keys. It cannot sensibly install
    Python, clone a repository, or report progress on either, so it installs a
    small launcher folder and then runs this, elevated, with the install
    directory it chose.

    What it does, in order:

        1. Git            - needed for every update after this one
        2. Python 3.11    - the version EasyOCR and OpenCV are tested against
        3. Node.js LTS    - builds the React bundle
        4. Application    - clones the repository into <InstallDir>\app
        5. Firewall       - opens the port so guards can reach this machine

    What it deliberately does NOT do: create the virtualenv, pip install, or
    build the frontend. Those already happen on the first run of
    scripts\run-campus.ps1, where the launcher streams them into a log the
    operator can see. Doing them here would mean twenty silent minutes behind
    an installer progress bar, and no way to tell a slow download from a hang.

    Credentials are not asked for here either - the launcher has a panel for
    them and shows it by itself on a machine that is not configured yet. One
    form, one place.

        powershell -ExecutionPolicy Bypass -File bootstrap.ps1 -InstallDir C:\SLC-VMS
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$InstallDir,
    [string]$RepoUrl = 'https://github.com/terendex/Vehicle-Management-System-For-Saint-Louis-College.git',
    [string]$Branch  = 'jonas',
    [int]$Port       = 8000,

    # Which steps the operator selected on the wizard's component page. An IT
    # department that manages Python or Git centrally can deselect those and
    # this will not touch them. Defaults to everything so running this script
    # by hand, or as the Repair shortcut, still does the whole job.
    [string]$Steps = 'git,python,node,app,firewall'
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase

$AppDir     = Join-Path $InstallDir 'app'
$ConfigPath = Join-Path $env:LOCALAPPDATA 'SLC-VMS\launcher.json'
$Wanted     = @($Steps -split ',' | ForEach-Object { $_.Trim().ToLower() } | Where-Object { $_ })
$Queue      = New-Object System.Collections.Concurrent.ConcurrentQueue[string]
$PathAdds   = New-Object System.Collections.Generic.List[string]

# ---------------------------------------------------------------------------
#  Window
# ---------------------------------------------------------------------------
# Same tokens as the web app and the launcher (frontend\src\index.css): navy
# #03396C on an #EEF4F9 ground, #D3E1EC hairlines, 12px cards, 9px buttons.
# Someone sees this window once and the launcher every day; they should not
# look like they came from different products.
$xaml = @'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="SLC Vehicle Management - Setup"
        Width="640" Height="580" ResizeMode="NoResize" WindowStyle="None"
        WindowStartupLocation="CenterScreen" Background="#FFEEF4F9"
        FontFamily="Inter, Segoe UI"
        TextOptions.TextFormattingMode="Display">
  <Window.Resources>
    <SolidColorBrush x:Key="Navy"    Color="#FF03396C"/>
    <SolidColorBrush x:Key="Ink"     Color="#FF0B2340"/>
    <SolidColorBrush x:Key="Text"    Color="#FF2E4C63"/>
    <SolidColorBrush x:Key="Muted"   Color="#FF4A6B85"/>
    <SolidColorBrush x:Key="Placeholder" Color="#FF64839C"/>
    <SolidColorBrush x:Key="Disabled" Color="#FF9DB6C9"/>
    <SolidColorBrush x:Key="Line"    Color="#FFD3E1EC"/>
    <SolidColorBrush x:Key="Surface" Color="#FFFFFFFF"/>
    <SolidColorBrush x:Key="Surface2" Color="#FFF7FAFC"/>
    <SolidColorBrush x:Key="Sky50"   Color="#FFEAF2F8"/>

    <LinearGradientBrush x:Key="Brand" StartPoint="0,0" EndPoint="1,0">
      <GradientStop Color="#FF03396C" Offset="0"/>
      <GradientStop Color="#FF0B5C9C" Offset="0.55"/>
      <GradientStop Color="#FF1072B3" Offset="1"/>
    </LinearGradientBrush>

    <Style x:Key="Ghost" TargetType="Button">
      <Setter Property="Foreground" Value="{StaticResource Navy}"/>
      <Setter Property="FontSize" Value="12"/>
      <Setter Property="FontWeight" Value="Medium"/>
      <Setter Property="Height" Value="34"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="b" Background="{StaticResource Surface}" BorderBrush="{StaticResource Line}"
                    BorderThickness="1" CornerRadius="9" Padding="16,0">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="b" Property="Background" Value="{StaticResource Sky50}"/>
              </Trigger>
              <Trigger Property="IsEnabled" Value="False">
                <Setter Property="Foreground" Value="{StaticResource Disabled}"/>
                <Setter TargetName="b" Property="Background" Value="{StaticResource Surface2}"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

    <Style x:Key="Primary" TargetType="Button" BasedOn="{StaticResource Ghost}">
      <Setter Property="Foreground" Value="White"/>
      <Setter Property="FontWeight" Value="SemiBold"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="b" Background="{StaticResource Navy}" CornerRadius="9" Padding="20,0">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="b" Property="Opacity" Value="0.90"/>
              </Trigger>
              <Trigger Property="IsEnabled" Value="False">
                <Setter TargetName="b" Property="Background" Value="{StaticResource Disabled}"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
  </Window.Resources>

  <Border BorderBrush="{StaticResource Line}" BorderThickness="1">
   <Grid>
    <Grid.RowDefinitions>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="*"/>
      <RowDefinition Height="Auto"/>
    </Grid.RowDefinitions>

    <!-- header: the same lockup the web pages carry -->
    <Border Grid.Row="0" Background="{StaticResource Brand}" Padding="24,18">
      <StackPanel Orientation="Horizontal">
        <!-- An Ellipse filled with an ImageBrush, not an Image in a rounded
             Border: ClipToBounds clips to the rectangular bounds and ignores
             CornerRadius, which draws the seal as a rounded square. -->
        <Grid Width="38" Height="38">
          <Ellipse Fill="White"/>
          <TextBlock x:Name="LogoFallback" Text="SLC" FontSize="12" FontWeight="Bold"
                     Foreground="#FF03396C" HorizontalAlignment="Center" VerticalAlignment="Center"/>
          <Ellipse x:Name="Logo" Stroke="#66FFFFFF" StrokeThickness="1"
                   RenderOptions.BitmapScalingMode="HighQuality"/>
        </Grid>
        <StackPanel Margin="13,0,0,0" VerticalAlignment="Center">
          <TextBlock Text="Vehicle Management System" Foreground="White" FontSize="15" FontWeight="SemiBold"/>
          <TextBlock Text="Saint Louis College - campus installation" Foreground="#FFCFE3F5" FontSize="11.5"/>
        </StackPanel>
      </StackPanel>
    </Border>

    <!-- steps -->
    <Border Grid.Row="1" Background="{StaticResource Surface}" BorderBrush="{StaticResource Line}"
            BorderThickness="0,0,0,1" Padding="24,20,24,12">
      <StackPanel x:Name="Steps"/>
    </Border>

    <!-- log -->
    <Grid Grid.Row="2" Margin="24,14,24,0">
      <Grid.RowDefinitions>
        <RowDefinition Height="Auto"/>
        <RowDefinition Height="*"/>
      </Grid.RowDefinitions>
      <Grid Grid.Row="0">
        <TextBlock x:Name="Phase" Foreground="{StaticResource Muted}" FontSize="11.5" VerticalAlignment="Center"
                   TextWrapping="Wrap" Margin="0,0,90,0"/>
        <Button x:Name="BtnDetails" Style="{StaticResource Ghost}" Content="Details" Height="26"
                HorizontalAlignment="Right" VerticalAlignment="Center"/>
      </Grid>
      <Border Grid.Row="1" x:Name="LogBox" Background="{StaticResource Surface2}" CornerRadius="8" Margin="0,10,0,0"
              Visibility="Collapsed" BorderBrush="{StaticResource Line}" BorderThickness="1">
        <ListBox x:Name="LogList" Background="Transparent" BorderThickness="0" Padding="11,7"
                 Foreground="{StaticResource Text}" FontFamily="Consolas" FontSize="11"
                 ScrollViewer.HorizontalScrollBarVisibility="Disabled"
                 VirtualizingStackPanel.IsVirtualizing="True"
                 VirtualizingStackPanel.VirtualizationMode="Recycling"/>
      </Border>
    </Grid>

    <!-- footer -->
    <Border Grid.Row="3" Background="{StaticResource Surface}" BorderBrush="{StaticResource Line}"
            BorderThickness="0,1,0,0" Padding="24,14">
      <Grid>
        <ProgressBar x:Name="Bar" Height="5" Width="240" HorizontalAlignment="Left" VerticalAlignment="Center"
                     Background="#FFEAF2F8" Foreground="#FF1072B3" BorderThickness="0" IsIndeterminate="True"/>
        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right">
          <Button x:Name="BtnCancel" Style="{StaticResource Ghost}" Content="Cancel" Margin="0,0,8,0"/>
          <Button x:Name="BtnDone" Style="{StaticResource Primary}" Content="Finish" IsEnabled="False"/>
        </StackPanel>
      </Grid>
    </Border>
   </Grid>
  </Border>
</Window>
'@

$win = [Windows.Markup.XamlReader]::Parse($xaml)
$ui = @{}
foreach ($n in @('Logo','LogoFallback','Steps','Phase','BtnDetails','LogBox','LogList','Bar','BtnCancel','BtnDone')) {
    $ui[$n] = $win.FindName($n)
}

function Write-Log([string]$Text) {
    if (-not $Text) { return }
    [void]$ui.LogList.Items.Add($Text)
    while ($ui.LogList.Items.Count -gt 500) { $ui.LogList.Items.RemoveAt(0) }
    $ui.LogList.ScrollIntoView($ui.LogList.Items[$ui.LogList.Items.Count - 1])
}

# ---------------------------------------------------------------------------
#  Step rows
# ---------------------------------------------------------------------------
$Rows = @()

# The same status trio the web app uses for approved / pending / rejected
# (--success, --warning, --danger in frontend\src\index.css), so a green dot
# here means what a green badge means there. Built once and frozen: WPF skips
# change tracking on a frozen brush, and these never change.
$Ink        = [Windows.Media.BrushConverter]::new().ConvertFrom('#FF0B2340')
$MutedInk   = [Windows.Media.BrushConverter]::new().ConvertFrom('#FF64839C')
$StepColour = @{
    pending = [Windows.Media.BrushConverter]::new().ConvertFrom('#FF9DB6C9')  # --disabled
    running = [Windows.Media.BrushConverter]::new().ConvertFrom('#FF8A6B00')  # --warning
    done    = [Windows.Media.BrushConverter]::new().ConvertFrom('#FF0F7A5A')  # --success
    skipped = [Windows.Media.BrushConverter]::new().ConvertFrom('#FF0F7A5A')
    failed  = [Windows.Media.BrushConverter]::new().ConvertFrom('#FFC62828')  # --danger
}
foreach ($b in @($Ink, $MutedInk) + @($StepColour.Values)) { if ($b.CanFreeze) { $b.Freeze() } }

function Add-StepRow([string]$Title, [string]$Note) {
    $g = New-Object Windows.Controls.Grid
    $g.Margin = '0,0,0,13'
    $c1 = New-Object Windows.Controls.ColumnDefinition; $c1.Width = 'Auto'
    $c2 = New-Object Windows.Controls.ColumnDefinition
    $g.ColumnDefinitions.Add($c1); $g.ColumnDefinitions.Add($c2)

    $dot = New-Object Windows.Shapes.Ellipse
    $dot.Width = 9; $dot.Height = 9; $dot.VerticalAlignment = 'Center'
    $dot.Fill = $StepColour.pending
    [Windows.Controls.Grid]::SetColumn($dot, 0)
    [void]$g.Children.Add($dot)

    $sp = New-Object Windows.Controls.StackPanel
    $sp.Margin = '14,0,0,0'
    [Windows.Controls.Grid]::SetColumn($sp, 1)

    # No explicit FontFamily: these inherit "Inter, Segoe UI" from the Window
    # once they are in the tree, which is the same stack the web app asks for.
    $t = New-Object Windows.Controls.TextBlock
    $t.Text = $Title; $t.FontSize = 13; $t.FontWeight = 'SemiBold'
    $t.Foreground = $MutedInk
    [void]$sp.Children.Add($t)

    $s = New-Object Windows.Controls.TextBlock
    $s.Text = $Note; $s.FontSize = 11
    $s.Foreground = $MutedInk
    $s.TextWrapping = 'Wrap'
    [void]$sp.Children.Add($s)

    [void]$g.Children.Add($sp)
    [void]$ui.Steps.Children.Add($g)
    return @{ Dot = $dot; Title = $t; Note = $s }
}

function Set-StepState([int]$Index, [string]$State, [string]$Note) {
    $r = $Rows[$Index]
    $fill = if ($StepColour.ContainsKey($State)) { $StepColour[$State] } else { $StepColour.pending }
    $r.Dot.Fill = $fill
    $r.Title.Foreground = if ($State -eq 'pending') { $MutedInk } else { $Ink }
    if ($PSBoundParameters.ContainsKey('Note')) { $r.Note.Text = $Note }
}

# ---------------------------------------------------------------------------
#  Running an external command with its output in the log
# ---------------------------------------------------------------------------
$Proc = $null
$Subs = @()

# Windows PowerShell runs on .NET Framework, where ProcessStartInfo has no
# ArgumentList - only the single Arguments string. So the quoting is done here.
# It matters: the install directory is operator-chosen and "C:\Program Files\
# SLC VMS" would otherwise reach git as three separate arguments.
function Format-Arg([string]$Value) {
    if ($Value -match '[\s"]') { return '"' + ($Value -replace '"', '\"') + '"' }
    return $Value
}

function Start-Tool {
    param([string]$File, [string[]]$ToolArgs)
    $line = (($ToolArgs | ForEach-Object { Format-Arg $_ }) -join ' ')
    Write-Log ("> $File $line")

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName  = $File
    $psi.Arguments = $line
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError  = $true

    $p = New-Object System.Diagnostics.Process
    $p.StartInfo = $psi
    $p.EnableRaisingEvents = $true
    $script:Subs = @(
        (Register-ObjectEvent -InputObject $p -EventName OutputDataReceived -MessageData $Queue -Action {
            if ($null -ne $EventArgs.Data) { $Event.MessageData.Enqueue($EventArgs.Data) } }),
        (Register-ObjectEvent -InputObject $p -EventName ErrorDataReceived -MessageData $Queue -Action {
            if ($null -ne $EventArgs.Data) { $Event.MessageData.Enqueue($EventArgs.Data) } })
    )
    [void]$p.Start()
    $p.BeginOutputReadLine()
    $p.BeginErrorReadLine()
    return $p
}

function Clear-Proc {
    foreach ($s in $script:Subs) { try { Unregister-Event -SubscriptionId $s.Id -ErrorAction SilentlyContinue } catch { } }
    $script:Subs = @()
    $script:Proc = $null
}

# ---------------------------------------------------------------------------
#  Prerequisite discovery
# ---------------------------------------------------------------------------
# winget is the only supported way to install these silently on a stock Windows
# 11 box. On an older image it is missing, and there is nothing this script can
# honestly do about that except say so and name the download.
function Get-Winget {
    $c = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    return $null
}

function Find-Git {
    $c = Get-Command git.exe -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    foreach ($p in @("$env:ProgramFiles\Git\cmd\git.exe", "${env:ProgramFiles(x86)}\Git\cmd\git.exe",
                     "$env:LOCALAPPDATA\Programs\Git\cmd\git.exe")) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

# Specifically 3.11. A machine with 3.9 or 3.13 on PATH will build a venv that
# fails much later inside an EasyOCR or OpenCV wheel, with an error naming
# neither the version nor this decision.
function Find-Python311 {
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        $probe = & $py.Source -3.11 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $probe) { return "$probe".Trim() }
    }
    foreach ($p in @("$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
                     "$env:ProgramFiles\Python311\python.exe",
                     "C:\Python311\python.exe")) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

function Find-Node {
    $c = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    foreach ($p in @("$env:ProgramFiles\nodejs\node.exe", "${env:ProgramFiles(x86)}\nodejs\node.exe")) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

function Add-ToolPath([string]$ExePath) {
    if (-not $ExePath) { return }
    $dir = Split-Path -Parent $ExePath
    if ($dir -and -not $PathAdds.Contains($dir)) {
        [void]$PathAdds.Add($dir)
        # Also for this process, so a later step can use what an earlier one
        # just installed without waiting for a reboot to publish the PATH.
        $env:PATH = "$dir;$env:PATH"
    }
}

# ---------------------------------------------------------------------------
#  The steps
# ---------------------------------------------------------------------------
$WingetExe = Get-Winget

function New-WingetStep {
    param([string]$Id, [scriptblock]$Finder, [string]$Human)
    return @{
        Begin = {
            $found = & $Finder
            if ($found) {
                Add-ToolPath $found
                return @{ Done = $true; Note = $found }
            }
            if (-not $WingetExe) {
                return @{ Done = $true; Failed = $true
                          Note = "not installed, and winget is unavailable - install $Human by hand" }
            }
            return Start-Tool $WingetExe @('install', '--id', $Id, '-e', '--source', 'winget',
                                           '--accept-package-agreements', '--accept-source-agreements',
                                           '--silent')
        }.GetNewClosure()
        End = {
            param($ExitCode)
            $found = & $Finder
            if ($found) { Add-ToolPath $found; return @{ Note = $found } }
            return @{ Failed = $true; Note = "winget exited $ExitCode and $Human is still not on this machine" }
        }.GetNewClosure()
    }
}

$Steps = @(
    @{ Id = 'git'; Title = 'Git'; Note = 'checking...'
       Spec  = (New-WingetStep -Id 'Git.Git'          -Finder { Find-Git }        -Human 'Git') },
    @{ Id = 'python'; Title = 'Python 3.11'; Note = 'checking...'
       Spec  = (New-WingetStep -Id 'Python.Python.3.11' -Finder { Find-Python311 } -Human 'Python 3.11') },
    @{ Id = 'node'; Title = 'Node.js'; Note = 'checking...'
       Spec  = (New-WingetStep -Id 'OpenJS.NodeJS.LTS' -Finder { Find-Node }       -Human 'Node.js') },

    @{ Id = 'app'; Title = 'Application files'; Note = 'waiting'
       Spec  = @{
         Begin = {
            $git = Find-Git
            if (-not $git) { return @{ Done = $true; Failed = $true; Note = 'no Git - cannot download the application' } }

            # Windows still caps most paths at 260 characters, and git reports
            # hitting that as "fatal: '$GIT_DIR' too big" - which names neither
            # the path nor the limit, and reads like a corrupt repository. The
            # deepest tracked file here is about 60 characters below the root,
            # so anything past ~120 will fail partway through a checkout.
            # Catching it before the download says what is actually wrong.
            if ($AppDir.Length -gt 120) {
                return @{ Done = $true; Failed = $true
                          Note = "the install path is $($AppDir.Length) characters, which is too long for git on Windows - reinstall somewhere shorter, such as C:\SLC-VMS" }
            }

            if (Test-Path (Join-Path $AppDir '.git')) {
                # A reinstall over an existing checkout. Fetching rather than
                # re-cloning keeps backend\.env, the venv and the trained model
                # weights that live in this folder.
                Write-Log 'Existing checkout found - fetching instead of cloning.'
                return Start-Tool $git @('-C', $AppDir, 'fetch', '--progress', 'origin', $Branch)
            }
            if (-not (Test-Path $InstallDir)) { New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null }

            $script:CloneAttempt++

            # A plain, complete clone. Measured against the real remote it is
            # about 182 MB for this branch - 27k objects, of which the 119 MB
            # checked out at HEAD is most of the weight. That is a single
            # one-time download and not worth complicating.
            #
            # Deliberately NOT --filter=blob:none or --depth 1. A partial clone
            # would save roughly 60 MB of historical blobs and buy a lazy fetch
            # on every later checkout; a shallow clone grafts a new root on each
            # fetch, which breaks the `pull --ff-only` the update button depends
            # on. Neither trade is worth 60 MB. (A local .git here can look far
            # larger - unreachable objects from other branches - but that is not
            # what the server sends.)
            return Start-Tool $git @('clone', '--progress', '--branch', $Branch, $RepoUrl, $AppDir)
         }
         End = {
            param($ExitCode)

            # Judge the download by git's exit code, not by whether some file
            # turned up. Those are two different questions, and conflating them
            # meant a clone that succeeded but landed on a branch missing the
            # launcher was treated as a network failure - which then deleted
            # 300 MB of good download and fetched it all again.
            if ($ExitCode -eq 0) {
                if (Test-Path (Join-Path $AppDir 'scripts\campus-launcher.ps1')) {
                    return @{ Note = $AppDir }
                }
                return @{ Failed = $true
                          Note = "downloaded, but branch '$Branch' does not contain scripts\campus-launcher.ps1 - push it to that branch, then run Repair from the Start Menu" }
            }

            # A real failure. The usual cause on a campus network is the
            # connection dropping partway through 182 MB, which succeeds on a
            # second attempt - so try once more before making someone rerun the
            # whole installer.
            if ($script:CloneAttempt -eq 1) {
                Write-Log 'Clone failed - retrying once.'
                if (Test-Path $AppDir) {
                    try {
                        Remove-Item -Recurse -Force $AppDir -ErrorAction Stop
                    } catch {
                        # Cloning into a non-empty directory fails too, so a
                        # half-deleted folder is worse than not retrying.
                        return @{ Failed = $true
                                  Note = "clone failed (exit $ExitCode) and the partial download could not be removed: $($_.Exception.Message.Trim())" }
                    }
                }
                return @{ Retry = $true }
            }
            return @{ Failed = $true; Note = "download failed (exit $ExitCode)" }
         }
       } },

    @{ Id = 'firewall'; Title = 'Firewall'; Note = 'waiting'
       Spec  = @{
         Begin = {
            # Without this the server starts, binds 0.0.0.0, and is still
            # unreachable from every phone at the gate - a failure that looks
            # like the app is broken.
            $name = 'SLC VMS'
            try {
                $existing = Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue
                if ($existing) {
                    return @{ Done = $true; Note = "rule '$name' already present" }
                }
                New-NetFirewallRule -DisplayName $name -Direction Inbound -LocalPort $Port `
                    -Protocol TCP -Action Allow -Profile Any -ErrorAction Stop | Out-Null
                return @{ Done = $true; Note = "TCP $Port open for inbound connections" }
            } catch {
                return @{ Done = $true; Failed = $true
                          Note = "could not add the rule ($($_.Exception.Message.Trim())) - add it by hand" }
            }
         }
         End = { param($ExitCode) return @{} }
       } }
)

foreach ($s in $Steps) { $Rows += ,(Add-StepRow $s.Title $s.Note) }

# ---------------------------------------------------------------------------
#  Driver
# ---------------------------------------------------------------------------
$Index        = 0
$Started      = $false
$Failures     = 0
$Finished     = $false
$CloneAttempt = 0

function Complete-Run {
    $script:Finished = $true
    $ui.Bar.IsIndeterminate = $false
    $ui.Bar.Value = 100
    $ui.BtnDone.IsEnabled = $true
    $ui.BtnCancel.IsEnabled = $false

    # Written even on a partial failure: the launcher can still use whatever
    # was found, and an operator fixing one missing tool by hand should not
    # have to rerun the whole installer to have it recorded.
    try {
        $dir = Split-Path -Parent $ConfigPath
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }

        # Same shape campus-config.ps1 reads. Only the keys this stage actually
        # knows about are written; the launcher fills in its own defaults for
        # the rest, so an old config file is never a reason to fail here.
        $existing = @{}
        if (Test-Path $ConfigPath) {
            try { (Get-Content $ConfigPath -Raw | ConvertFrom-Json).PSObject.Properties |
                    ForEach-Object { $existing[$_.Name] = $_.Value } } catch { }
        }
        $existing['Branch']      = $Branch
        $existing['Port']        = $Port
        $existing['PathPrepend'] = @($PathAdds)
        if (-not $existing.ContainsKey('UpdatePollMinutes')) { $existing['UpdatePollMinutes'] = 5 }
        if (-not $existing.ContainsKey('AutoStart'))         { $existing['AutoStart'] = $true }

        [pscustomobject]$existing | ConvertTo-Json | Set-Content -Path $ConfigPath -Encoding UTF8
        Write-Log "Wrote $ConfigPath"
    } catch {
        Write-Log "Could not write the launcher config: $($_.Exception.Message)"
    }

    if ($Failures -gt 0) {
        $ui.Phase.Text = "Finished with $Failures problem(s) - open Details before starting the server."
        $ui.LogBox.Visibility = 'Visible'
    } else {
        $ui.Phase.Text = 'Ready. The launcher will ask for the shared credentials the first time it opens.'
    }
}

function Complete-Step($Result) {
    # A step can ask to be run again instead of advancing - the clone uses this
    # to fall back from a partial clone to a full one.
    if ($Result -and $Result.Retry) {
        $script:Started = $false
        return
    }
    if ($Result -and $Result.Failed) {
        $script:Failures++
        Set-StepState $Index 'failed' $Result.Note
        Write-Log "FAILED: $($Steps[$Index].Title) - $($Result.Note)"
    } else {
        $note = if ($Result -and $Result.Note) { $Result.Note } else { 'done' }
        Set-StepState $Index 'done' $note
    }
    $script:Index++
    $script:Started = $false
}

$timer = New-Object Windows.Threading.DispatcherTimer
$timer.Interval = [TimeSpan]::FromMilliseconds(150)
$timer.Add_Tick({
    $line = ''
    $n = 0
    while ($Queue.TryDequeue([ref]$line) -and $n -lt 120) { Write-Log $line; $n++ }

    if ($script:Proc) {
        if (-not $script:Proc.HasExited) { return }
        $code = $script:Proc.ExitCode
        Clear-Proc
        $end = & $Steps[$Index].Spec.End $code
        Complete-Step $end
        return
    }

    if ($script:Finished) { return }
    if ($Index -ge $Steps.Count) { Complete-Run; return }

    if (-not $script:Started) {
        $script:Started = $true

        # Deselected on the wizard's component page. Shown rather than hidden:
        # someone reading this window later needs to know that Python was not
        # touched because it was not asked for, not because it was missed.
        $id = $Steps[$Index].Id
        if ($Wanted -notcontains $id) {
            Set-StepState $Index 'skipped' 'not selected - left alone'
            Write-Log "Skipping $($Steps[$Index].Title): not selected."
            $script:Index++
            $script:Started = $false
            return
        }

        Set-StepState $Index 'running' 'working...'
        $ui.Phase.Text = "Step $($Index + 1) of $($Steps.Count) - $($Steps[$Index].Title)"
        try {
            $r = & $Steps[$Index].Spec.Begin
        } catch {
            Complete-Step @{ Failed = $true; Note = $_.Exception.Message.Trim() }
            return
        }
        if ($r -is [System.Diagnostics.Process]) { $script:Proc = $r; return }
        Complete-Step $r
    }
})

# ---------------------------------------------------------------------------
#  Boot
# ---------------------------------------------------------------------------
function Set-LogoImage {
    $logo = Join-Path $PSScriptRoot 'slclogo.jpg'
    if (-not (Test-Path $logo)) { return }
    try {
        # Decode near the size it will be drawn at. Handing WPF the full 394px
        # source for a 38px circle makes it squeeze the seal with a box filter,
        # which is what turned the lettering to mush.
        $dpi = 1.0
        $src = [Windows.PresentationSource]::FromVisual($win)
        if ($src -and $src.CompositionTarget) { $dpi = $src.CompositionTarget.TransformToDevice.M11 }
        if ($dpi -le 0) { $dpi = 1.0 }

        $bmp = New-Object Windows.Media.Imaging.BitmapImage
        $bmp.BeginInit()
        $bmp.UriSource = New-Object Uri($logo)
        $bmp.DecodePixelWidth = [int][math]::Ceiling(38 * $dpi * 2)
        $bmp.CacheOption = 'OnLoad'
        $bmp.EndInit()
        $bmp.Freeze()

        $brush = New-Object Windows.Media.ImageBrush($bmp)
        $brush.Stretch = 'UniformToFill'
        $brush.Freeze()
        $ui.Logo.Fill = $brush
        $ui.LogoFallback.Visibility = 'Collapsed'
    } catch {
        # The "SLC" lettermark behind it stays visible. A logo that will not
        # load is not a reason to stop an install.
    }
}
$win.Add_ContentRendered({ Set-LogoImage })

$ui.BtnDetails.Add_Click({
    $ui.LogBox.Visibility = if ($ui.LogBox.Visibility -eq 'Visible') { 'Collapsed' } else { 'Visible' }
})
$ui.BtnDone.Add_Click({ $win.Close() })
$ui.BtnCancel.Add_Click({
    # Killing a half-finished winget or clone leaves a mess someone has to
    # untangle by hand, so cancel stops after the step in flight rather than
    # in the middle of it.
    $script:Index = $Steps.Count
    $ui.Phase.Text = 'Cancelling after the current step...'
    $ui.BtnCancel.IsEnabled = $false
})

Write-Log "Install directory: $InstallDir"
Write-Log "Repository: $RepoUrl ($Branch)"
if (-not $WingetExe) { Write-Log 'winget was not found - missing prerequisites cannot be installed automatically.' }

$win.Add_Closing({
    $timer.Stop()
    if ($script:Proc -and -not $script:Proc.HasExited) {
        try { Start-Process taskkill -ArgumentList @('/PID', $script:Proc.Id, '/T', '/F') -NoNewWindow -Wait } catch { }
    }
    Clear-Proc
})

$timer.Start()
[void]$win.ShowDialog()

# Inno reads this: a non-zero code makes the wizard say setup did not fully
# succeed, which is the honest outcome when a prerequisite is still missing.
exit $(if ($Failures -gt 0) { 1 } else { 0 })

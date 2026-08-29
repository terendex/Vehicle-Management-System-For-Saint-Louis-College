<#
    bootstrap.ps1 - the setup stage the Inno wizard hands off to.

    Inno can copy files and write registry keys. It cannot sensibly install
    Python, clone a repository, or report progress on either, so it installs a
    small launcher folder and then runs this, elevated, with the install
    directory it chose.

    What it does, in order:

        1. Git            - needed for every update after this one
        2. Python 3.12    - the version requirements.txt pins resolve against
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
    [string]$Branch  = 'main',
    [int]$Port       = 8000,

    # Which steps the operator selected on the wizard's component page. An IT
    # department that manages Python or Git centrally can deselect those and
    # this will not touch them. Defaults to everything so running this script
    # by hand, or as the Repair shortcut, still does the whole job.
    [string]$Steps = 'git,python,node,app,credentials,firewall'
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
        Title="Smart Parking and Vehicle Verification System - Setup"
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
          <TextBlock Text="SAINT LOUIS COLLEGE" Foreground="White" FontSize="13.5" FontWeight="Bold"/>
          <TextBlock Text="Smart Parking and Vehicle Verification System" Foreground="#FFCFE3F5" FontSize="11.5"/>
          <TextBlock Text="Campus installation" Foreground="#FF9FC4E8" FontSize="10.5" Margin="0,2,0,0"/>
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
#  Embedded credentials
# ---------------------------------------------------------------------------
# Present only in an installer built with build.ps1 -WithCredentials. See the
# header of embed-credentials.ps1: this is OBFUSCATED, NOT ENCRYPTED. The key is
# three lines below the data because the installer must open it with nobody
# present to type a passphrase. Anyone holding such an installer can recover the
# live database URL and the JWT signing key.
#
# Duplicated from embed-credentials.ps1 on purpose - this file ships inside the
# installer and runs before the repository exists, so it cannot dot-source
# anything out of scripts\.
function Get-ObfuscationKey {
    $seed = 'SLC-VMS::campus-installer::v1'
    return [System.Security.Cryptography.SHA256]::Create().ComputeHash(
        [System.Text.Encoding]::UTF8.GetBytes($seed))
}

function Unprotect-Blob {
    param([byte[]]$Bytes)
    $aes = [System.Security.Cryptography.Aes]::Create()
    $aes.Key = Get-ObfuscationKey
    $iv = New-Object byte[] 16
    [Array]::Copy($Bytes, 0, $iv, 0, 16)
    $aes.IV = $iv
    $dec = $aes.CreateDecryptor()
    $plain = $dec.TransformFinalBlock($Bytes, 16, $Bytes.Length - 16)
    $dec.Dispose(); $aes.Dispose()
    return [System.Text.Encoding]::UTF8.GetString($plain)
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

# Specifically 3.12: requirements.txt pins packages that declare
# requires_python >=3.12, so a 3.11 venv cannot resolve them at all. pip calls
# that a missing distribution, which reads like a bad pin rather than a wrong
# interpreter.
function Find-Python312 {
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        $probe = & $py.Source -3.12 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $probe) { return "$probe".Trim() }
    }
    foreach ($p in @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
                     "$env:ProgramFiles\Python312\python.exe",
                     "C:\Python312\python.exe")) {
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

$StepPlan = @(
    @{ Id = 'git'; Title = 'Git'; Note = 'checking...'
       Spec  = (New-WingetStep -Id 'Git.Git'          -Finder { Find-Git }        -Human 'Git') },
    @{ Id = 'python'; Title = 'Python 3.12'; Note = 'checking...'
       Spec  = (New-WingetStep -Id 'Python.Python.3.12' -Finder { Find-Python312 } -Human 'Python 3.12') },
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
                    # This clone was made by an ELEVATED process, so the folder
                    # belongs to Administrators. The launcher afterwards runs as
                    # whichever ordinary account is signed in, and git refuses a
                    # repository it thinks someone else owns:
                    #     fatal: detected dubious ownership in repository at ...
                    # Registering it in the SYSTEM config (not --global, which
                    # would only cover the installing administrator) makes it
                    # trusted for every account that will ever use this machine.
                    # Registered with FORWARD slashes: git compares
                    # safe.directory against its own spelling of the path, and
                    # on Windows that uses '/'. A backslash form is stored
                    # happily and then never matches anything. Both are added
                    # because it costs nothing and removes the guesswork.
                    $git = Find-Git
                    if ($git) {
                        $forward  = $AppDir.Replace('\', '/')
                        $existing = @(& $git config --system --get-all safe.directory 2>$null)
                        foreach ($form in @($forward, $AppDir)) {
                            if ($existing -notcontains $form) {
                                & $git config --system --add safe.directory $form 2>&1 | Out-Null
                            }
                        }
                        Write-Log "Registered $forward as a safe git directory for all users."
                    }
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

    @{ Id = 'credentials'; Title = 'Shared credentials'; Note = 'waiting'
       Spec  = @{
         Begin = {
            # After the clone on purpose: this writes backend\.env inside the
            # checkout, and it seeds from the campus template that arrives with
            # it, so every commented explanation in that template survives.
            $blobFile = Join-Path $PSScriptRoot 'credentials.dat'
            if (-not (Test-Path $blobFile)) {
                return @{ Done = $true
                          Note = 'none embedded - the launcher will ask on first run' }
            }
            $envFile = Join-Path $AppDir 'backend\.env'
            if (-not (Test-Path $AppDir)) {
                return @{ Done = $true; Failed = $true
                          Note = 'no application folder to write backend\.env into' }
            }

            try {
                $text = Unprotect-Blob ([System.IO.File]::ReadAllBytes($blobFile))
            } catch {
                return @{ Done = $true; Failed = $true
                          Note = "the embedded credentials could not be read ($($_.Exception.Message.Trim()))" }
            }

            $template = Join-Path $AppDir 'backend\.env.campus.example'
            if ((-not (Test-Path $envFile)) -and (Test-Path $template)) {
                Copy-Item $template $envFile
            }

            # Written with a plain rewrite, never -replace: a SECRET_KEY or a
            # Neon password containing a dollar sign would come back mangled,
            # because "$1" in a replacement string is a capture reference.
            $lines = @()
            if (Test-Path $envFile) { $lines = @([System.IO.File]::ReadAllLines($envFile)) }
            $count = 0
            foreach ($line in ($text -split "`n")) {
                $t = $line.Trim()
                if (-not $t) { continue }
                $eq = $t.IndexOf('=')
                if ($eq -lt 1) { continue }
                $key = $t.Substring(0, $eq)
                $val = $t.Substring($eq + 1)
                $found = $false
                for ($i = 0; $i -lt $lines.Count; $i++) {
                    if ($lines[$i] -like "$key=*") { $lines[$i] = "$key=$val"; $found = $true; break }
                }
                if (-not $found) { $lines += "$key=$val" }
                $count++
            }
            # No BOM: python-dotenv reads .env as plain utf-8 and would fold a
            # BOM into the first key's name.
            [System.IO.File]::WriteAllLines($envFile, $lines, (New-Object System.Text.UTF8Encoding($false)))

            # The blob has done its job. Leaving a second copy of the
            # credentials sitting in the launcher folder serves nothing -
            # backend\.env is what the server actually reads.
            try { Remove-Item $blobFile -Force -ErrorAction Stop } catch { }

            return @{ Done = $true; Note = "$count values written to backend\.env" }
         }
         End = { param($ExitCode) return @{} }
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

foreach ($s in $StepPlan) { $Rows += ,(Add-StepRow $s.Title $s.Note) }

# ---------------------------------------------------------------------------
#  Driver
# ---------------------------------------------------------------------------
$Index        = 0
$Started      = $false
$Failures     = 0
$Finished     = $false
$CloneAttempt = 0
$Cancelled    = $false

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

    # The one thing that actually matters is whether the application is on disk.
    # Every other step can be skipped deliberately, but without this there is
    # nothing to launch - so it is checked at the end rather than trusted to the
    # step having reported success earlier.
    $entry = Join-Path $AppDir 'scripts\campus-launcher.ps1'
    if (($Wanted -contains 'app') -and -not (Test-Path $entry)) {
        $script:Failures++
        Write-Log "FAILED: the application was not downloaded - $entry is missing."
    }

    # The checkout is created here, after Inno has finished, so it cannot be
    # hidden from the [Dirs] section. Hiding it keeps the install folder down to
    # the launcher shortcut, the licence and the uninstaller. Git is entirely
    # happy working inside a hidden directory.
    if (Test-Path $AppDir) {
        try {
            $d = Get-Item $AppDir -Force
            $d.Attributes = $d.Attributes -bor [System.IO.FileAttributes]::Hidden
        } catch {
            Write-Log "Could not hide $AppDir : $($_.Exception.Message.Trim())"
        }
    }

    if ($script:Cancelled) {
        $ui.Phase.Text = 'CANCELLED - setup did not finish. Nothing is installed. ' +
                         'Use "Repair installation" in the Start Menu to complete it.'
        $ui.LogBox.Visibility = 'Visible'
    } elseif ($Failures -gt 0) {
        $ui.Phase.Text = "Finished with $Failures problem(s) - open Details, and use " +
                         '"Repair installation" in the Start Menu once they are resolved.'
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
        Write-Log "FAILED: $($StepPlan[$Index].Title) - $($Result.Note)"
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
        $end = & $StepPlan[$Index].Spec.End $code
        Complete-Step $end
        return
    }

    if ($script:Finished) { return }
    if ($Index -ge $StepPlan.Count) { Complete-Run; return }

    if (-not $script:Started) {
        $script:Started = $true

        # Deselected on the wizard's component page. Shown rather than hidden:
        # someone reading this window later needs to know that Python was not
        # touched because it was not asked for, not because it was missed.
        $id = $StepPlan[$Index].Id
        if ($Wanted -notcontains $id) {
            Set-StepState $Index 'skipped' 'not selected - left alone'
            Write-Log "Skipping $($StepPlan[$Index].Title): not selected."
            $script:Index++
            $script:Started = $false
            return
        }

        Set-StepState $Index 'running' 'working...'
        $ui.Phase.Text = "Step $($Index + 1) of $($StepPlan.Count) - $($StepPlan[$Index].Title)"
        try {
            $r = & $StepPlan[$Index].Spec.Begin
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
    #
    # The flag matters as much as the jump. Without it, cancelling skipped
    # straight to the finish and reported "Ready" with a zero exit code, so a
    # cancelled setup looked exactly like a completed one - the wizard still
    # wrote its registry key saying the product was installed, while the
    # application had never been downloaded at all.
    $script:Cancelled = $true
    $script:Index = $StepPlan.Count
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
exit $(if ($Failures -gt 0 -or $Cancelled) { 1 } else { 0 })

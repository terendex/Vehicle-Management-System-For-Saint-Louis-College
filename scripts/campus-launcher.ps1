<#
    campus-launcher.ps1 - the desktop app the campus machine actually runs.

    It is a shell around scripts\run-campus.ps1, not a replacement for it. The
    server logic stays in one place; this window starts that script as a child
    process, reads its output, and turns the lines it prints into something a
    guard can read at a glance:

        * one state - Stopped / Starting / Running / Stopped with an error
        * the LAN URL, copyable, with the two entry points guards need
        * three health pills, all derived from lines the server already prints
        * the whole log, live, so a failure is visible instead of silent

    It also watches the repository. Every few minutes it fetches the configured
    branch and, if commits have landed, offers a one-click "Update & restart".
    It never pulls on its own: this machine is serving live gate scanning, and a
    restart drops the camera feeds for the length of a rebuild. That has to be
    someone's decision, not a surprise mid-shift.

    LOOK AND FEEL. Every colour, radius and type size below is taken from the
    web app's own tokens in frontend\src\index.css - the navy #03396C brand, the
    #EEF4F9 page ground, #D3E1EC hairlines, 12px cards, 9px buttons, and the
    status trio (#0F7A5A / #8A6B00 / #C62828). A guard moves between this window
    and the browser all shift; they should not look like two products.

    COST. Everything on the hot path - a log line arriving, a timer tick - is
    constant time. See Write-Log and Read-ServerLine, both of which say what
    they are avoiding and why.

    Run it directly for debugging:
        powershell -ExecutionPolicy Bypass -File scripts\campus-launcher.ps1

    The Start Menu shortcut the installer creates does not point here directly.
    It runs installer\start-campus.vbs -> start-campus.ps1, which live outside
    the checkout and hand off to this file. That indirection is what lets this
    window be updated by a `git pull` like everything else.
#>

[CmdletBinding()]
param(
    [int]$Port,
    [string]$Branch,
    [switch]$NoAutoStart
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'campus-config.ps1')

Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase, System.Windows.Forms

# ---------------------------------------------------------------------------
#  State
# ---------------------------------------------------------------------------
$cfg = Get-CampusLauncherConfig
if ($PSBoundParameters.ContainsKey('Port'))   { $cfg.Port   = $Port }
if ($PSBoundParameters.ContainsKey('Branch')) { $cfg.Branch = $Branch }

# Must happen before anything looks for git.exe or starts the server: the
# installer may have put Python, Node and Git on the machine after the Explorer
# that launched this window was started, and that Explorer handed us its stale
# PATH.
Add-CampusToolPaths $cfg

# Named $App, not $S. PowerShell variable names are case-INSENSITIVE, so a
# single-letter $S is silently the same variable as the $s in any nearby
# `foreach ($s in ...)`. That is not hypothetical: it clobbered this state
# object mid-loop and the launcher died with "The property 'Subs' cannot be
# found on this object" - which names the symptom and hides the cause
# completely. Keep this name distinctive.
$App = [ordered]@{
    Repo        = $repo
    EnvFile     = Join-Path $repo 'backend\.env'
    Proc        = $null          # the run-campus.ps1 child
    Queue       = New-Object System.Collections.Concurrent.ConcurrentQueue[string]
    Subs        = @()            # Register-ObjectEvent subscriptions, for cleanup
    State       = 'Stopped'
    Origin      = ''
    CamOk       = 0
    CamBad      = 0
    Behind      = 0
    HeadSubject = ''
    FetchProc   = $null
    LastCheck   = $null
    Restarting  = $false         # a stop that is the first half of an update
    LogWriter   = $null
    LogPath     = ''
    LogDirty    = $false         # a line arrived this tick; scroll and flush once
    AutoOpened  = $false         # OpenOnStart fires once per server start, not per restart loop
}

$LogDir = Join-Path $env:LOCALAPPDATA 'SLC-VMS\logs'
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

# ---------------------------------------------------------------------------
#  Window
# ---------------------------------------------------------------------------
# WindowStyle=None with AllowsTransparency left FALSE on purpose: transparency
# forces WPF onto a software rendering path, and this machine is simultaneously
# decoding four RTSP streams. A square-cornered window that costs nothing beats
# a rounded one that steals frames from the detector.
$xaml = @'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Smart Parking and Vehicle Verification System - Campus"
        Width="1060" Height="900" MinWidth="900" MinHeight="620"
        WindowStyle="None" ResizeMode="CanResizeWithGrip"
        WindowStartupLocation="CenterScreen"
        Background="#FFEEF4F9" UseLayoutRounding="True"
        FontFamily="Inter, Segoe UI"
        TextOptions.TextFormattingMode="Display">

  <Window.Resources>
    <!-- Straight from frontend\src\index.css. Names match the CSS custom
         properties so the two can be diffed by eye. -->
    <SolidColorBrush x:Key="Navy"       Color="#FF03396C"/>
    <SolidColorBrush x:Key="Navy600"    Color="#FF084A85"/>
    <SolidColorBrush x:Key="Blue"       Color="#FF1072B3"/>
    <SolidColorBrush x:Key="Sky"        Color="#FFBDD4E5"/>
    <SolidColorBrush x:Key="Sky50"      Color="#FFEAF2F8"/>
    <SolidColorBrush x:Key="Ink"        Color="#FF0B2340"/>
    <SolidColorBrush x:Key="Text"       Color="#FF2E4C63"/>
    <SolidColorBrush x:Key="Muted"      Color="#FF4A6B85"/>
    <SolidColorBrush x:Key="Placeholder" Color="#FF64839C"/>
    <SolidColorBrush x:Key="Disabled"   Color="#FF9DB6C9"/>
    <SolidColorBrush x:Key="Line"       Color="#FFD3E1EC"/>
    <SolidColorBrush x:Key="LineStrong" Color="#FFBDD4E5"/>
    <SolidColorBrush x:Key="Surface"    Color="#FFFFFFFF"/>
    <SolidColorBrush x:Key="Surface2"   Color="#FFF7FAFC"/>
    <SolidColorBrush x:Key="Bg"         Color="#FFEEF4F9"/>
    <SolidColorBrush x:Key="Success"    Color="#FF0F7A5A"/>
    <SolidColorBrush x:Key="Danger"     Color="#FFC62828"/>
    <SolidColorBrush x:Key="Warning"    Color="#FF8A6B00"/>

    <!-- The navy to blue sweep from the brand sheet, CSS token brand-gradient.
         Token names are written without their leading dashes throughout this
         document: an XML comment may not contain a double hyphen. -->
    <LinearGradientBrush x:Key="Brand" StartPoint="0,0" EndPoint="1,0">
      <GradientStop Color="#FF03396C" Offset="0"/>
      <GradientStop Color="#FF0B5C9C" Offset="0.55"/>
      <GradientStop Color="#FF1072B3" Offset="1"/>
    </LinearGradientBrush>

    <!-- CSS token shadow: 0 2px 8px rgba(3,57,108,0.08). Tinted navy, which is
         what the web app uses rather than a neutral black. -->
    <DropShadowEffect x:Key="CardShadow" Color="#FF03396C" Opacity="0.10"
                      BlurRadius="10" ShadowDepth="2" Direction="270"/>

    <Style x:Key="Card" TargetType="Border">
      <Setter Property="Background" Value="{StaticResource Surface}"/>
      <Setter Property="BorderBrush" Value="{StaticResource Line}"/>
      <Setter Property="BorderThickness" Value="1"/>
      <Setter Property="CornerRadius" Value="12"/>
      <Setter Property="Padding" Value="16"/>
      <Setter Property="Effect" Value="{StaticResource CardShadow}"/>
    </Style>

    <!-- Section caption: uppercase, tracked out, placeholder blue. -->
    <Style x:Key="H" TargetType="TextBlock">
      <Setter Property="Foreground" Value="{StaticResource Placeholder}"/>
      <Setter Property="FontSize" Value="10.5"/>
      <Setter Property="FontWeight" Value="SemiBold"/>
    </Style>

    <!-- Primary action: the navy token, the same one the web buttons use.
         Background is set from code so one button is both start and stop. -->
    <Style x:Key="BigBtn" TargetType="Button">
      <Setter Property="Foreground" Value="White"/>
      <Setter Property="FontSize" Value="13.5"/>
      <Setter Property="FontWeight" Value="SemiBold"/>
      <Setter Property="Height" Value="42"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="Background" Value="{StaticResource Navy}"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="b" Background="{TemplateBinding Background}" CornerRadius="9">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="b" Property="Opacity" Value="0.90"/>
              </Trigger>
              <Trigger Property="IsEnabled" Value="False">
                <Setter TargetName="b" Property="Background" Value="{StaticResource Disabled}"/>
                <Setter Property="Cursor" Value="Arrow"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

    <!-- Secondary: white on a hairline, navy label. -->
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
                    BorderThickness="1" CornerRadius="9" Padding="10,0">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="b" Property="Background" Value="{StaticResource Sky50}"/>
                <Setter TargetName="b" Property="BorderBrush" Value="{StaticResource LineStrong}"/>
              </Trigger>
              <Trigger Property="IsEnabled" Value="False">
                <Setter Property="Foreground" Value="{StaticResource Disabled}"/>
                <Setter TargetName="b" Property="Background" Value="{StaticResource Surface2}"/>
                <Setter Property="Cursor" Value="Arrow"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

    <Style x:Key="Chrome" TargetType="Button">
      <Setter Property="Foreground" Value="White"/>
      <Setter Property="FontFamily" Value="Segoe MDL2 Assets"/>
      <Setter Property="FontSize" Value="10"/>
      <Setter Property="Width" Value="46"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="b" Background="#00000000">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="b" Property="Background" Value="#33FFFFFF"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

    <!-- Inputs: surface-2 fill on a 1.5px line, 8px radius, and the
         rgba(3,57,108,0.08) focus ring the web forms use. -->
    <Style x:Key="Field" TargetType="TextBox">
      <Setter Property="Background" Value="{StaticResource Surface2}"/>
      <Setter Property="Foreground" Value="{StaticResource Ink}"/>
      <Setter Property="BorderBrush" Value="{StaticResource Line}"/>
      <Setter Property="BorderThickness" Value="1.5"/>
      <Setter Property="Padding" Value="9,7"/>
      <Setter Property="FontSize" Value="12.5"/>
      <Setter Property="CaretBrush" Value="{StaticResource Ink}"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="TextBox">
            <Border x:Name="b" Background="{TemplateBinding Background}"
                    BorderBrush="{TemplateBinding BorderBrush}"
                    BorderThickness="{TemplateBinding BorderThickness}" CornerRadius="8">
              <ScrollViewer x:Name="PART_ContentHost" Margin="{TemplateBinding Padding}"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsKeyboardFocused" Value="True">
                <Setter TargetName="b" Property="BorderBrush" Value="{StaticResource Navy}"/>
                <Setter TargetName="b" Property="Effect">
                  <Setter.Value>
                    <DropShadowEffect Color="#FF03396C" Opacity="0.20" BlurRadius="7" ShadowDepth="0"/>
                  </Setter.Value>
                </Setter>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

    <!-- ComboBox, retemplated. The stock one is Aero-era grey with a beveled
         drop arrow and looks nothing like the rest of this window or the web
         app; there is no lighter way to change that than replacing the
         template, because the default draws its own chrome. Matches the Field
         style above so the two controls read as one family. -->
    <Style TargetType="ComboBoxItem">
      <Setter Property="Padding" Value="10,7"/>
      <Setter Property="Foreground" Value="{StaticResource Ink}"/>
      <Setter Property="FontSize" Value="12.5"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="ComboBoxItem">
            <Border x:Name="b" Background="Transparent" Padding="{TemplateBinding Padding}" CornerRadius="6">
              <ContentPresenter/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsHighlighted" Value="True">
                <Setter TargetName="b" Property="Background" Value="{StaticResource Sky50}"/>
              </Trigger>
              <Trigger Property="IsSelected" Value="True">
                <Setter TargetName="b" Property="Background" Value="{StaticResource Sky50}"/>
                <Setter Property="Foreground" Value="{StaticResource Navy}"/>
                <Setter Property="FontWeight" Value="SemiBold"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

    <Style x:Key="Combo" TargetType="ComboBox">
      <Setter Property="Foreground" Value="{StaticResource Ink}"/>
      <Setter Property="FontSize" Value="12.5"/>
      <Setter Property="Height" Value="34"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="ComboBox">
            <Grid>
              <ToggleButton x:Name="Toggle" Focusable="False" ClickMode="Press"
                            IsChecked="{Binding IsDropDownOpen, Mode=TwoWay, RelativeSource={RelativeSource TemplatedParent}}">
                <ToggleButton.Template>
                  <ControlTemplate TargetType="ToggleButton">
                    <Border x:Name="bg" Background="{StaticResource Surface2}"
                            BorderBrush="{StaticResource Line}" BorderThickness="1.5" CornerRadius="8">
                      <Path HorizontalAlignment="Right" VerticalAlignment="Center" Margin="0,0,12,0"
                            Data="M0,0 L4.5,4.5 L9,0" Stroke="{StaticResource Muted}" StrokeThickness="1.6"/>
                    </Border>
                    <ControlTemplate.Triggers>
                      <Trigger Property="IsMouseOver" Value="True">
                        <Setter TargetName="bg" Property="BorderBrush" Value="{StaticResource LineStrong}"/>
                      </Trigger>
                      <Trigger Property="IsChecked" Value="True">
                        <Setter TargetName="bg" Property="BorderBrush" Value="{StaticResource Navy}"/>
                      </Trigger>
                    </ControlTemplate.Triggers>
                  </ControlTemplate>
                </ToggleButton.Template>
              </ToggleButton>
              <ContentPresenter Margin="11,0,32,0" VerticalAlignment="Center" IsHitTestVisible="False"
                                Content="{TemplateBinding SelectionBoxItem}"
                                ContentTemplate="{TemplateBinding SelectionBoxItemTemplate}"/>
              <Popup x:Name="PART_Popup" Placement="Bottom" AllowsTransparency="True" PopupAnimation="Fade"
                     IsOpen="{TemplateBinding IsDropDownOpen}" Focusable="False">
                <Border Background="{StaticResource Surface}" BorderBrush="{StaticResource Line}"
                        BorderThickness="1" CornerRadius="8" Padding="4" Margin="0,4,0,0"
                        MinWidth="{Binding ActualWidth, RelativeSource={RelativeSource TemplatedParent}}">
                  <StackPanel IsItemsHost="True" KeyboardNavigation.DirectionalNavigation="Contained"/>
                </Border>
              </Popup>
            </Grid>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

    <!-- The activity log. A ListBox so WPF virtualises it: a first run's pip
         install emits thousands of lines, and a stack of TextBlocks would make
         the window crawl. -->
    <Style x:Key="LogItem" TargetType="ListBoxItem">
      <Setter Property="Padding" Value="0"/>
      <Setter Property="Margin" Value="0"/>
      <Setter Property="Background" Value="Transparent"/>
      <Setter Property="BorderThickness" Value="0"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="ListBoxItem"><ContentPresenter/></ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
  </Window.Resources>

  <Grid>
    <Grid.RowDefinitions>
      <RowDefinition Height="54"/>
      <RowDefinition Height="*"/>
      <RowDefinition Height="28"/>
    </Grid.RowDefinitions>

    <!-- brand header, the same lockup the web pages carry -->
    <Border x:Name="TitleBar" Grid.Row="0" Background="{StaticResource Brand}">
      <Grid>
        <StackPanel Orientation="Horizontal" VerticalAlignment="Center" Margin="16,0,0,0">
          <!-- An Ellipse filled with an ImageBrush, not an Image inside a
               rounded Border. ClipToBounds clips to the rectangular bounds and
               ignores CornerRadius entirely, so that version drew the seal as a
               rounded square. The web header is border-radius:50% and this has
               to match it. -->
          <Grid Width="36" Height="36">
            <Ellipse Fill="White"/>
            <TextBlock x:Name="LogoFallback" Text="SLC" FontSize="11" FontWeight="Bold"
                       Foreground="#FF03396C" HorizontalAlignment="Center" VerticalAlignment="Center"/>
            <Ellipse x:Name="Logo" Stroke="#66FFFFFF" StrokeThickness="1"
                     RenderOptions.BitmapScalingMode="HighQuality"/>
          </Grid>
          <!-- The same two-line lockup the web pages carry, in the same order:
               the college on top, the system name under it. Taken from
               frontend\src\...\header-title / header-subtitle. The system name
               is never abbreviated or truncated - that is the rule the shared
               slc-header.css exists to enforce, and it holds here too. -->
          <StackPanel Margin="12,0,0,0" VerticalAlignment="Center">
            <TextBlock Text="SAINT LOUIS COLLEGE" Foreground="White"
                       FontSize="13" FontWeight="Bold"/>
            <TextBlock Text="Smart Parking and Vehicle Verification System" Foreground="#FFCFE3F5"
                       FontSize="11" Margin="0,1,0,0"/>
          </StackPanel>
          <Border Background="#33FFFFFF" CornerRadius="20" Padding="10,3" Margin="14,0,0,0"
                  VerticalAlignment="Center">
            <TextBlock Text="CAMPUS GATE TERMINAL" Foreground="White" FontSize="9.5"
                       FontWeight="SemiBold"/>
          </Border>
        </StackPanel>
        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right" VerticalAlignment="Stretch">
          <Button x:Name="BtnMin"   Style="{StaticResource Chrome}" Content="&#xE921;"/>
          <Button x:Name="BtnClose" Style="{StaticResource Chrome}" Content="&#xE8BB;"/>
        </StackPanel>
      </Grid>
    </Border>

    <!-- body -->
    <Grid Grid.Row="1" Margin="16">
      <Grid.ColumnDefinitions>
        <ColumnDefinition Width="400"/>
        <ColumnDefinition Width="16"/>
        <ColumnDefinition Width="*"/>
      </Grid.ColumnDefinitions>

      <ScrollViewer Grid.Column="0" VerticalScrollBarVisibility="Auto" Padding="0,0,4,0">
       <StackPanel>

        <!-- status -->
        <Border Style="{StaticResource Card}">
          <StackPanel>
            <StackPanel Orientation="Horizontal">
              <Ellipse x:Name="Dot" Width="11" Height="11" Fill="{StaticResource Disabled}" VerticalAlignment="Center"/>
              <TextBlock x:Name="StateText" Text="STOPPED" Foreground="{StaticResource Ink}"
                         FontSize="17" FontWeight="Bold" Margin="10,0,0,0" VerticalAlignment="Center"/>
            </StackPanel>
            <TextBlock x:Name="OriginText" Text="not serving" Foreground="{StaticResource Placeholder}"
                       FontFamily="Consolas" FontSize="14" Margin="0,10,0,0"/>
            <TextBlock x:Name="SubText" Text="Press Start to bring the gate terminal up."
                       Foreground="{StaticResource Muted}" FontSize="11.5"
                       TextWrapping="Wrap" Margin="0,6,0,0"/>

            <UniformGrid Rows="1" Columns="3" Margin="0,16,0,0">
              <Border Background="{StaticResource Surface2}" BorderBrush="{StaticResource Line}"
                      BorderThickness="1" CornerRadius="8" Padding="9,8" Margin="0,0,5,0">
                <StackPanel>
                  <TextBlock Text="DATABASE" Style="{StaticResource H}"/>
                  <StackPanel Orientation="Horizontal" Margin="0,5,0,0">
                    <Ellipse x:Name="PillDbDot" Width="7" Height="7" Fill="{StaticResource Disabled}" VerticalAlignment="Center"/>
                    <TextBlock x:Name="PillDb" Text="unknown" Foreground="{StaticResource Muted}"
                               FontSize="11.5" Margin="6,0,0,0" TextTrimming="CharacterEllipsis"/>
                  </StackPanel>
                </StackPanel>
              </Border>
              <Border Background="{StaticResource Surface2}" BorderBrush="{StaticResource Line}"
                      BorderThickness="1" CornerRadius="8" Padding="9,8" Margin="5,0">
                <StackPanel>
                  <TextBlock Text="CAMERAS" Style="{StaticResource H}"/>
                  <StackPanel Orientation="Horizontal" Margin="0,5,0,0">
                    <Ellipse x:Name="PillCamDot" Width="7" Height="7" Fill="{StaticResource Disabled}" VerticalAlignment="Center"/>
                    <TextBlock x:Name="PillCam" Text="unknown" Foreground="{StaticResource Muted}"
                               FontSize="11.5" Margin="6,0,0,0" TextTrimming="CharacterEllipsis"/>
                  </StackPanel>
                </StackPanel>
              </Border>
              <Border Background="{StaticResource Surface2}" BorderBrush="{StaticResource Line}"
                      BorderThickness="1" CornerRadius="8" Padding="9,8" Margin="5,0,0,0">
                <StackPanel>
                  <TextBlock Text="REALTIME" Style="{StaticResource H}"/>
                  <StackPanel Orientation="Horizontal" Margin="0,5,0,0">
                    <Ellipse x:Name="PillRtDot" Width="7" Height="7" Fill="{StaticResource Disabled}" VerticalAlignment="Center"/>
                    <TextBlock x:Name="PillRt" Text="unknown" Foreground="{StaticResource Muted}"
                               FontSize="11.5" Margin="6,0,0,0" TextTrimming="CharacterEllipsis"/>
                  </StackPanel>
                </StackPanel>
              </Border>
            </UniformGrid>

            <Button x:Name="BtnStart" Style="{StaticResource BigBtn}" Content="Start server" Margin="0,16,0,0"/>

            <UniformGrid Rows="1" Columns="3" Margin="0,9,0,0">
              <Button x:Name="BtnGuard" Style="{StaticResource Ghost}" Content="Guard terminal" Margin="0,0,4,0"/>
              <Button x:Name="BtnAdmin" Style="{StaticResource Ghost}" Content="Admin login"    Margin="4,0"/>
              <Button x:Name="BtnCopy"  Style="{StaticResource Ghost}" Content="Copy URL"       Margin="4,0,0,0"/>
            </UniformGrid>
          </StackPanel>
        </Border>

        <!-- updates -->
        <Border Style="{StaticResource Card}" Margin="0,12,0,0">
          <StackPanel>
            <Grid>
              <TextBlock Text="UPDATES" Style="{StaticResource H}"/>
              <Border HorizontalAlignment="Right" Background="{StaticResource Sky50}"
                      BorderBrush="{StaticResource LineStrong}" BorderThickness="1"
                      CornerRadius="20" Padding="9,2" Margin="0,-3,0,0">
                <TextBlock x:Name="BranchText" Text="main" Foreground="{StaticResource Navy}"
                           FontFamily="Consolas" FontSize="10.5"/>
              </Border>
            </Grid>
            <TextBlock x:Name="UpdateHead" Text="Checking..." Foreground="{StaticResource Ink}"
                       FontSize="13.5" FontWeight="SemiBold" Margin="0,9,0,0" TextWrapping="Wrap"/>
            <TextBlock x:Name="UpdateSub" Text="" Foreground="{StaticResource Muted}"
                       FontSize="11.5" Margin="0,4,0,0" TextWrapping="Wrap"/>
            <Button x:Name="BtnUpdate" Style="{StaticResource BigBtn}" Content="Update and restart"
                    Margin="0,12,0,0" Visibility="Collapsed"/>
            <Grid Margin="0,10,0,0">
              <TextBlock x:Name="LastCheckText" Text="" Foreground="{StaticResource Placeholder}"
                         FontSize="10.5" VerticalAlignment="Center"/>
              <Button x:Name="BtnCheck" Style="{StaticResource Ghost}" Content="Check now"
                      HorizontalAlignment="Right" Height="28"/>
            </Grid>
          </StackPanel>
        </Border>

        <!-- settings -->
        <Border Style="{StaticResource Card}" Margin="0,12,0,0">
          <StackPanel>
            <TextBlock Text="SETTINGS" Style="{StaticResource H}"/>
            <!-- Port only. The branch this machine tracks is deliberately NOT
                 editable here: it is decided once, during installation, and a
                 gate terminal pointed at an arbitrary branch by whoever is
                 sitting at it is a way to put untested code in front of guards.
                 It stays visible as the chip on the UPDATES card above, so it
                 can still be read at a glance - just not changed. -->
            <Grid Margin="0,11,0,0">
              <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="*"/>
              </Grid.ColumnDefinitions>
              <StackPanel Grid.Column="0" Margin="0,0,6,0">
                <TextBlock Text="Port" Foreground="{StaticResource Muted}" FontSize="11.5"/>
                <TextBox x:Name="TxtPort" Style="{StaticResource Field}" FontFamily="Consolas" Margin="0,5,0,0"/>
              </StackPanel>
            </Grid>
            <CheckBox x:Name="ChkAuto" Foreground="{StaticResource Muted}" FontSize="11.5" Margin="0,12,0,0"
                      Content=" Start the server when this window opens"/>
            <CheckBox x:Name="ChkKiosk" Foreground="{StaticResource Muted}" FontSize="11.5" Margin="0,9,0,0"
                      Content=" Open pages in kiosk mode (full screen, no address bar)"/>

            <TextBlock Text="Open automatically once the server is up" Foreground="{StaticResource Muted}"
                       FontSize="11.5" Margin="0,13,0,0"/>
            <ComboBox x:Name="CmbOpen" Style="{StaticResource Combo}" Margin="0,5,0,0">
              <ComboBoxItem Content="Nothing"       Tag="none"/>
              <ComboBoxItem Content="Guard terminal" Tag="guard"/>
              <ComboBoxItem Content="Admin login"    Tag="admin"/>
            </ComboBox>

            <UniformGrid Rows="1" Columns="2" Margin="0,13,0,0">
              <Button x:Name="BtnSave"    Style="{StaticResource Ghost}" Content="Save settings" Margin="0,0,4,0"/>
              <Button x:Name="BtnSecrets" Style="{StaticResource Ghost}" Content="Credentials"   Margin="4,0,0,0"/>
            </UniformGrid>
          </StackPanel>
        </Border>

       </StackPanel>
      </ScrollViewer>

      <!-- activity log -->
      <Border Grid.Column="2" Style="{StaticResource Card}" Padding="0">
        <Grid>
          <Grid.RowDefinitions>
            <RowDefinition Height="42"/>
            <RowDefinition Height="*"/>
          </Grid.RowDefinitions>
          <Grid Grid.Row="0" Margin="16,0">
            <TextBlock Text="ACTIVITY" Style="{StaticResource H}" VerticalAlignment="Center"/>
            <StackPanel Orientation="Horizontal" HorizontalAlignment="Right">
              <Button x:Name="BtnLogFolder" Style="{StaticResource Ghost}" Content="Log files" Height="26" Margin="0,0,6,0"/>
              <Button x:Name="BtnClear"     Style="{StaticResource Ghost}" Content="Clear"     Height="26"/>
            </StackPanel>
          </Grid>
          <Border Grid.Row="1" Background="{StaticResource Surface2}" BorderBrush="{StaticResource Line}"
                  BorderThickness="0,1,0,0" CornerRadius="0,0,11,11">
            <ListBox x:Name="LogList" Background="Transparent" BorderThickness="0" Padding="12,8"
                     ItemContainerStyle="{StaticResource LogItem}"
                     ScrollViewer.HorizontalScrollBarVisibility="Disabled"
                     VirtualizingStackPanel.IsVirtualizing="True"
                     VirtualizingStackPanel.VirtualizationMode="Recycling">
              <ListBox.ItemTemplate>
                <DataTemplate>
                  <Grid Margin="0,0.5">
                    <Grid.ColumnDefinitions>
                      <ColumnDefinition Width="58"/>
                      <ColumnDefinition Width="*"/>
                    </Grid.ColumnDefinitions>
                    <TextBlock Grid.Column="0" Text="{Binding Time}" Foreground="#FF9DB6C9"
                               FontFamily="Consolas" FontSize="11"/>
                    <TextBlock Grid.Column="1" Text="{Binding Text}" Foreground="{Binding Brush}"
                               FontFamily="Consolas" FontSize="11.5" TextWrapping="Wrap"/>
                  </Grid>
                </DataTemplate>
              </ListBox.ItemTemplate>
            </ListBox>
          </Border>
        </Grid>
      </Border>
    </Grid>

    <!-- status bar -->
    <Border Grid.Row="2" Background="{StaticResource Surface}" BorderBrush="{StaticResource Line}" BorderThickness="0,1,0,0">
      <Grid Margin="16,0,28,0">
        <TextBlock x:Name="RepoText" Foreground="{StaticResource Placeholder}" FontSize="10.5"
                   VerticalAlignment="Center" TextTrimming="CharacterEllipsis"/>
        <TextBlock x:Name="CommitText" HorizontalAlignment="Right" Foreground="{StaticResource Placeholder}"
                   FontFamily="Consolas" FontSize="10.5" VerticalAlignment="Center"/>
      </Grid>
    </Border>

    <!-- Credentials overlay. The RowSpan is load-bearing: without an explicit
         Grid.Row a child defaults to row 0, so this dimmed sheet and its card
         were being laid out inside the 54px title bar and clipped to it. -->
    <Grid x:Name="SecretsOverlay" Grid.Row="0" Grid.RowSpan="3"
          Background="#B30B2340" Visibility="Collapsed">
      <Border Style="{StaticResource Card}" Width="580" MaxHeight="580" Padding="24"
              VerticalAlignment="Center" HorizontalAlignment="Center">
        <DockPanel>
          <TextBlock DockPanel.Dock="Top" Text="Shared credentials" Foreground="{StaticResource Ink}"
                     FontSize="16" FontWeight="SemiBold"/>
          <TextBlock DockPanel.Dock="Top" TextWrapping="Wrap" Foreground="{StaticResource Muted}"
                     FontSize="11.5" Margin="0,7,0,14"
                     Text="These come from Railway and must match it exactly. The secret key signs the logins both halves accept, and the database URL is what makes them one system. Leave a box empty to keep what is already saved."/>
          <UniformGrid DockPanel.Dock="Bottom" Rows="1" Columns="2" Margin="0,16,0,0">
            <Button x:Name="BtnSecretsCancel" Style="{StaticResource Ghost}" Content="Cancel" Height="38" Margin="0,0,5,0"/>
            <Button x:Name="BtnSecretsSave" Style="{StaticResource BigBtn}" Content="Save" Height="38" Margin="5,0,0,0"/>
          </UniformGrid>
          <ScrollViewer VerticalScrollBarVisibility="Auto">
            <StackPanel x:Name="SecretsPanel" Margin="0,0,8,0"/>
          </ScrollViewer>
        </DockPanel>
      </Border>
    </Grid>
  </Grid>
</Window>
'@

$win = [Windows.Markup.XamlReader]::Parse($xaml)

# An exception thrown inside a WPF event handler or a dispatcher timer does not
# propagate out to whoever started this script - it tears the message loop down
# where nobody can see it. Under wscript there is no console either, so the
# window just vanishes. This writes what happened somewhere findable and says
# so, instead of disappearing.
$win.Dispatcher.Add_UnhandledException({
    param($sender, $e)
    try {
        $dir = Join-Path $env:LOCALAPPDATA 'SLC-VMS\logs'
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        $file = Join-Path $dir ('crash-' + (Get-Date -Format 'yyyy-MM-dd_HHmmss') + '.txt')
        @(
            "Smart Parking and Vehicle Verification System - launcher UI crash"
            (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
            "Repository : $repo"
            ""
            "Message    : $($e.Exception.Message)"
            "Type       : $($e.Exception.GetType().FullName)"
            ""
            $e.Exception.StackTrace
        ) -join [Environment]::NewLine | Set-Content -Path $file -Encoding UTF8

        [System.Windows.Forms.MessageBox]::Show(
            "The launcher hit an error and has to close." + [Environment]::NewLine + [Environment]::NewLine +
            $e.Exception.Message + [Environment]::NewLine + [Environment]::NewLine +
            "Details: $file",
            'Smart Parking and Vehicle Verification System', 'OK', 'Error') | Out-Null
    } catch { }
    # Left unhandled on purpose: the state after a dispatcher exception is not
    # trustworthy, and limping on would hide the fault rather than fix it.
})

$ui = @{}
foreach ($n in @('TitleBar','Logo','LogoFallback','BtnMin','BtnClose','Dot','StateText','OriginText','SubText',
                 'PillDb','PillDbDot','PillCam','PillCamDot','PillRt','PillRtDot','BtnStart','BtnGuard','BtnAdmin',
                 'BtnCopy','UpdateHead','UpdateSub','BtnUpdate','BtnCheck','LastCheckText','BranchText','TxtPort',
                 'ChkAuto','ChkKiosk','CmbOpen','BtnSave','BtnSecrets','LogList','BtnClear','BtnLogFolder','RepoText',
                 'CommitText','SecretsOverlay','SecretsPanel','BtnSecretsSave','BtnSecretsCancel')) {
    $ui[$n] = $win.FindName($n)
}

# Pulled from the window's own resources rather than re-declared, so there is
# exactly one definition of each colour. Frozen brushes skip WPF's change
# tracking on every render - these never change, so there is nothing to track.
$Palette = @{}
foreach ($k in @('Navy','Navy600','Blue','Sky','Sky50','Ink','Text','Muted','Placeholder','Disabled',
                 'Line','LineStrong','Surface','Surface2','Bg','Success','Danger','Warning')) {
    $b = $win.FindResource($k)
    if ($b.CanFreeze -and -not $b.IsFrozen) { $b.Freeze() }
    $Palette[$k] = $b
}

# ---------------------------------------------------------------------------
#  Logging
# ---------------------------------------------------------------------------
$LogItems = New-Object System.Collections.ObjectModel.ObservableCollection[object]
$ui.LogList.ItemsSource = $LogItems

$LogCapHigh = 1200   # start trimming here
$LogCapKeep = 800    # trim down to here, in one pass

# Adding is all this does. Everything whose cost would grow with the number of
# lines already shown - trimming the collection, scrolling to the bottom,
# flushing the file - is deferred to the timer and done once per tick instead of
# once per line. A first run's pip install arrives in bursts of hundreds of
# lines, and doing any of those three per line is what turns the window to glue.
function Write-Log {
    param([string]$Text, [string]$Kind = 'info')
    $lineBrush = switch ($Kind) {
        'ok'    { $Palette.Success }
        'warn'  { $Palette.Warning }
        'error' { $Palette.Danger }
        'note'  { $Palette.Blue }
        'dim'   { $Palette.Placeholder }
        default { $Palette.Text }
    }
    $stamp = (Get-Date).ToString('HH:mm:ss')
    $LogItems.Add([pscustomobject]@{ Time = $stamp; Text = $Text; Brush = $lineBrush })
    $App.LogDirty = $true
    if ($App.LogWriter) { try { $App.LogWriter.WriteLine("$stamp  $Text") } catch { } }
}

# Called once per tick, not once per line. The trim is amortised: it runs only
# after 400 lines have accumulated past the cap, so the per-line cost of keeping
# the list bounded is constant.
function Update-LogView {
    if (-not $App.LogDirty) { return }
    $App.LogDirty = $false

    if ($LogItems.Count -gt $LogCapHigh) {
        $drop = $LogItems.Count - $LogCapKeep
        for ($i = 0; $i -lt $drop; $i++) { $LogItems.RemoveAt(0) }
    }
    if ($LogItems.Count -gt 0) { $ui.LogList.ScrollIntoView($LogItems[$LogItems.Count - 1]) }
    if ($App.LogWriter) { try { $App.LogWriter.Flush() } catch { } }
}

# ---------------------------------------------------------------------------
#  Presentation helpers
# ---------------------------------------------------------------------------
function Set-Pill {
    param([string]$Which, [string]$Text, [string]$Kind)
    # Never name a local here $brush. PowerShell variable names are
    # case-insensitive, so it would be the same variable as a $Brush palette -
    # a bug whose only symptom is text rendering with a null Foreground.
    $dotFill = switch ($Kind) {
        'ok'   { $Palette.Success }
        'warn' { $Palette.Warning }
        'bad'  { $Palette.Danger }
        default { $Palette.Disabled }
    }
    $ui["Pill$Which"].Text       = $Text
    $ui["Pill$Which"].Foreground = if ($Kind -eq 'none') { $Palette.Muted } else { $dotFill }
    $ui["Pill${Which}Dot"].Fill  = $dotFill
}

function Set-State {
    param([string]$State, [string]$Detail = '')
    $App.State = $State
    switch ($State) {
        'Stopped' {
            $ui.Dot.Fill = $Palette.Disabled
            $ui.StateText.Text = 'STOPPED'
            $ui.StateText.Foreground = $Palette.Muted
            $ui.OriginText.Text = 'not serving'
            $ui.OriginText.Foreground = $Palette.Placeholder
            $ui.BtnStart.Content = 'Start server'
            $ui.BtnStart.Background = $Palette.Navy
            $ui.BtnStart.IsEnabled = $true
            Set-Pill 'Db'  'unknown' 'none'
            Set-Pill 'Cam' 'unknown' 'none'
            Set-Pill 'Rt'  'unknown' 'none'
        }
        'Starting' {
            $ui.Dot.Fill = $Palette.Warning
            $ui.StateText.Text = 'STARTING'
            $ui.StateText.Foreground = $Palette.Warning
            $ui.BtnStart.Content = 'Stop server'
            $ui.BtnStart.Background = $Palette.Danger
            $ui.BtnStart.IsEnabled = $true
        }
        'Running' {
            $ui.Dot.Fill = $Palette.Success
            $ui.StateText.Text = 'RUNNING'
            $ui.StateText.Foreground = $Palette.Success
            $ui.BtnStart.Content = 'Stop server'
            $ui.BtnStart.Background = $Palette.Danger
            $ui.BtnStart.IsEnabled = $true
        }
        'Stopping' {
            $ui.Dot.Fill = $Palette.Warning
            $ui.StateText.Text = 'STOPPING'
            $ui.StateText.Foreground = $Palette.Warning
            $ui.BtnStart.IsEnabled = $false
        }
        'Failed' {
            $ui.Dot.Fill = $Palette.Danger
            $ui.StateText.Text = 'STOPPED'
            $ui.StateText.Foreground = $Palette.Danger
            $ui.OriginText.Text = 'not serving'
            $ui.OriginText.Foreground = $Palette.Danger
            $ui.BtnStart.Content = 'Start server'
            $ui.BtnStart.Background = $Palette.Navy
            $ui.BtnStart.IsEnabled = $true
        }
    }
    if ($Detail) { $ui.SubText.Text = $Detail }
    $ui.BtnGuard.IsEnabled = [bool]$App.Origin
    $ui.BtnAdmin.IsEnabled = [bool]$App.Origin
    $ui.BtnCopy.IsEnabled  = [bool]$App.Origin
}

# ---------------------------------------------------------------------------
#  Reading the server's output
# ---------------------------------------------------------------------------
# run-campus.ps1 already prints everything this window needs; it is not asked to
# emit a second machine-readable stream. These patterns match the lines it and
# Django print today - if you reword one of them, reword the pattern with it.
#
# ONE compiled regex, not a chain of ten. The overwhelming majority of lines
# through here are pip and daphne noise that match nothing, and testing each of
# them against ten separate patterns made the common case ten times more
# expensive than it needed to be. Named groups say which alternative hit, so a
# single pass both decides and dispatches.
# The pattern is built into a variable FIRST, then handed to the constructor.
# Concatenating it inline in the argument list silently breaks it: PowerShell
# binds the comma tighter than +, so
#     New-Object regex('a' + 'b', $options)
# is parsed as 'a' + ('b', $options) - the options enum is stringified onto the
# end of the last alternative and passed as part of the pattern, and the
# constructor gets no options at all. The symptom was the realtime pill sitting
# on "checking" forever while the line it needed sat plainly in the log, because
# the last alternative had become "(?<realtime>...) Compiled".
$statusPattern =
    '(?<origin>Serving on\s+(?<url>http://\S+))' +
    '|(?<listen>Listening on TCP address)' +
    '|(?<camok>^\s*reachable\s*:)' +
    '|(?<cambad>^\s*NO ROUTE\s*:)' +
    '|(?<camnone>no cameras registered yet)' +
    '|(?<dbfail>could not read the camera list)' +
    '|(?<realtime>\[settings\] realtime:)'
$StatusRe = New-Object regex($statusPattern, ([System.Text.RegularExpressions.RegexOptions]::Compiled))

$severityPattern =
    '(?<err>build FAILED|Traceback|CommandError|\bERROR\b|Error:|FATAL)' +
    '|(?<warn>\bWARNING\b|Warning:)' +
    '|(?<note>^\[campus\])'
$SeverityRe = New-Object regex($severityPattern, ([System.Text.RegularExpressions.RegexOptions]::Compiled))

# Child processes colour their output. npm and vite emit SGR escapes, which
# reach the log pane as literal "[33m...[0m" noise around the text.
#
# \x1B is the REGEX escape for ESC, written inside a single-quoted PowerShell
# string so PowerShell does not touch it. Do not reach for PowerShell's own `e
# here: that escape only exists from PowerShell 6 onward, and Windows
# PowerShell 5.1 - which is what runs this - silently reads it as the letter
# "e", leaving a pattern that matches nothing and strips nothing.
$ansiPattern = '\x1B\[[0-9;]*[A-Za-z]' + '|\x1B\][^\x07]*(\x07|\x1B\\)'
$AnsiRe = New-Object regex($ansiPattern, ([System.Text.RegularExpressions.RegexOptions]::Compiled))

function Read-ServerLine {
    param([string]$Line)

    $t = $AnsiRe.Replace($Line, '').TrimEnd()
    if ($t.Length -eq 0) { return }

    $kind = 'info'
    $m = $StatusRe.Match($t)

    if ($m.Success) {
        if ($m.Groups['origin'].Success) {
            $App.Origin = $m.Groups['url'].Value
            $ui.OriginText.Text = $App.Origin
            $ui.OriginText.Foreground = $Palette.Blue
            $kind = 'note'
        }
        # Daphne binding the socket is the only honest "it is up" signal. The
        # script prints "Serving on" a second earlier, before collectstatic has
        # handed over, and calling that RUNNING would have guards reloading a
        # page nothing is answering yet.
        elseif ($m.Groups['listen'].Success) {
            Set-State 'Running' 'Guards can reach this machine now. Leave this window open.'
            $kind = 'ok'
            # Only now, not on "Serving on": opening the browser a second before
            # daphne has the socket gives the guard a connection-refused page
            # they then have to know to reload.
            if (-not $App.AutoOpened -and $cfg.OpenOnStart -ne 'none') {
                $App.AutoOpened = $true
                Open-CampusPage $cfg.OpenOnStart
            }
        }
        elseif ($m.Groups['camok'].Success) {
            $App.CamOk++
            Set-Pill 'Cam' "$($App.CamOk) reachable" $(if ($App.CamBad) { 'warn' } else { 'ok' })
            Set-Pill 'Db' 'connected' 'ok'
            $kind = 'ok'
        }
        elseif ($m.Groups['cambad'].Success) {
            $App.CamBad++
            Set-Pill 'Cam' "$($App.CamOk) up, $($App.CamBad) down" $(if ($App.CamOk) { 'warn' } else { 'bad' })
            Set-Pill 'Db' 'connected' 'ok'
            $kind = 'warn'
        }
        elseif ($m.Groups['camnone'].Success) {
            Set-Pill 'Cam' 'none added' 'warn'
            Set-Pill 'Db' 'connected' 'ok'
            $kind = 'warn'
        }
        elseif ($m.Groups['dbfail'].Success) {
            Set-Pill 'Db'  'unreachable' 'bad'
            Set-Pill 'Cam' 'unknown' 'bad'
            $kind = 'error'
        }
        elseif ($m.Groups['realtime'].Success) {
            if     ($t.Contains('shared Redis')) { Set-Pill 'Rt' 'linked'    'ok' }
            elseif ($t.Contains('LOOPBACK'))     { Set-Pill 'Rt' 'loopback'  'warn'; $kind = 'warn' }
            else                                 { Set-Pill 'Rt' 'this half' 'warn' }
        }
    } else {
        $sev = $SeverityRe.Match($t)
        if ($sev.Success) {
            if     ($sev.Groups['err'].Success)  { $kind = 'error' }
            elseif ($sev.Groups['warn'].Success) { $kind = 'warn'  }
            else                                 { $kind = 'note'  }
        }
    }

    Write-Log $t $kind
}

# ---------------------------------------------------------------------------
#  Opening a page, in kiosk mode
# ---------------------------------------------------------------------------
# Chrome first, then Edge, then whatever the machine calls its default browser.
# Only the first two can be put into kiosk mode from the command line; a
# default-browser fallback opens an ordinary window and says so, because
# silently handing a guard a normal browser when kiosk was asked for is exactly
# the kind of thing nobody notices until someone wanders off into a tab.
function Find-Browser {
    $candidates = @(
        @{ Kind = 'chrome'; Paths = @(
            "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
            "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
            "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe") },
        @{ Kind = 'edge';   Paths = @(
            "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
            "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe") }
    )
    foreach ($c in $candidates) {
        foreach ($p in $c.Paths) { if (Test-Path $p) { return @{ Kind = $c.Kind; Exe = $p } } }
    }
    # App Paths covers installs that did not land anywhere above.
    foreach ($exe in @('chrome.exe', 'msedge.exe')) {
        foreach ($hive in @('HKLM:', 'HKCU:')) {
            $key = "$hive\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\$exe"
            $val = (Get-ItemProperty $key -ErrorAction SilentlyContinue).'(default)'
            if ($val -and (Test-Path $val)) {
                return @{ Kind = $(if ($exe -eq 'chrome.exe') { 'chrome' } else { 'edge' }); Exe = $val }
            }
        }
    }
    return $null
}

# Identified by the profile directory in its command line, not by a pid. The
# process Start-Process hands back is a stub that has already exited; the real
# browser is a sibling it spawned. Matching the profile path is what makes this
# safe - it can only ever hit the window we opened, never the operator's own
# browsing.
function Close-KioskBrowser {
    $profileDir = Join-Path $env:LOCALAPPDATA 'SLC-VMS\browser'
    try {
        $ours = Get-CimInstance Win32_Process -Filter "Name='chrome.exe' OR Name='msedge.exe'" -ErrorAction Stop |
                Where-Object { $_.CommandLine -and $_.CommandLine.Contains($profileDir) }
        foreach ($p in $ours) {
            try { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue } catch { }
        }
        return @($ours).Count
    } catch {
        return 0
    }
}

function Open-CampusPage {
    param([ValidateSet('guard', 'admin')][string]$Which)

    if (-not $App.Origin) {
        Write-Log 'Nothing is serving yet - start the server first.' 'warn'
        return
    }
    $path = if ($Which -eq 'guard') { '/security/guard-login' } else { '/login' }
    $url  = "$($App.Origin)$path"
    $name = if ($Which -eq 'guard') { 'guard terminal' } else { 'admin login' }

    if (-not $cfg.Kiosk) {
        Start-Process $url
        # ${name} braced, not $name: - a colon straight after a variable name
        # makes PowerShell read it as a scope qualifier ($script:, $env:).
        Write-Log "Opened the ${name}: $url" 'note'
        return
    }

    $browser = Find-Browser
    if (-not $browser) {
        Start-Process $url
        Write-Log "Kiosk mode needs Chrome or Edge and neither was found - opened the $name in the default browser instead." 'warn'
        return
    }

    # A profile of its own, for three reasons that all bite without it:
    #   * if the browser is already running, a plain --kiosk is handed to the
    #     existing process, which opens an ordinary window and ignores it. A
    #     separate user-data-dir forces a new process, so kiosk actually applies.
    #   * the guard's session stays out of whatever else this machine browses,
    #     and vice versa.
    #   * it gives us a process we own, rather than a pid that exits immediately
    #     after handing off.
    $profileDir = Join-Path $env:LOCALAPPDATA 'SLC-VMS\browser'
    if (-not (Test-Path $profileDir)) { New-Item -ItemType Directory -Path $profileDir -Force | Out-Null }

    # The quotes around the profile path are load-bearing. Start-Process joins
    # -ArgumentList with spaces and quotes nothing, so on the very common
    # "C:\Users\first last\..." profile this argument splits into three; Chrome
    # takes --user-data-dir=C:\Users\first, then treats "last" and the rest as
    # URLs and opens them as tabs. The visible symptom is not an error - it is
    # the page opening in the operator's existing window with kiosk silently
    # ignored, because without its own profile the browser hands off to the
    # process already running.
    $browserArgs = @(
        '--kiosk', $url,
        "--user-data-dir=`"$profileDir`"",
        '--no-first-run',
        '--disable-session-crashed-bubble',
        '--disable-infobars'
    )
    if ($browser.Kind -eq 'edge') {
        # Without this Edge picks its "public browsing" kiosk, which is
        # InPrivate and wipes the session on an idle timer - so a guard who
        # steps away comes back logged out mid-shift.
        $browserArgs += '--edge-kiosk-type=fullscreen'
        $browserArgs += '--no-first-run-experience'
    }

    try {
        # No -PassThru pid to keep: both Chrome and Edge spawn the real browser
        # and let the process we started exit immediately, so that pid is dead
        # within a second. Close-KioskBrowser finds the real ones by profile
        # path instead.
        Start-Process -FilePath $browser.Exe -ArgumentList $browserArgs -ErrorAction Stop | Out-Null
        Write-Log "Opened the $name in $($browser.Kind) kiosk mode. Alt+F4 closes it." 'ok'
    } catch {
        Start-Process $url
        Write-Log "Could not start $($browser.Kind) in kiosk mode ($($_.Exception.Message.Trim())) - opened the default browser." 'warn'
    }
}

# ---------------------------------------------------------------------------
#  Start / stop
# ---------------------------------------------------------------------------
function Start-Server {
    if ($App.Proc -and -not $App.Proc.HasExited) { return }

    $missing = Get-CampusMissingSecrets -EnvFile $App.EnvFile -RequiredOnly
    if ($missing.Count -gt 0) {
        Write-Log ("Cannot start: " + (($missing | ForEach-Object { $_.Key }) -join ', ') + " not set.") 'error'
        Set-State 'Failed' 'Fill in the shared credentials first.'
        Show-Secrets
        return
    }

    $App.CamOk = 0; $App.CamBad = 0; $App.Origin = ''; $App.AutoOpened = $false
    Set-State 'Starting' 'Preparing the environment. A first run installs dependencies and can take several minutes.'
    Set-Pill 'Db' 'checking' 'none'; Set-Pill 'Cam' 'checking' 'none'; Set-Pill 'Rt' 'checking' 'none'

    $App.LogPath = Join-Path $LogDir ("campus-" + (Get-Date -Format 'yyyy-MM-dd') + ".log")
    try {
        # UTF-8 without a BOM, to match what we now read from the child.
        $App.LogWriter = New-Object System.IO.StreamWriter($App.LogPath, $true, (New-Object System.Text.UTF8Encoding($false)))
        # Deliberately NOT AutoFlush. That would be one flush syscall per line;
        # Update-LogView flushes once per tick instead.
        $App.LogWriter.AutoFlush = $false
    } catch { $App.LogWriter = $null }

    $script = Join-Path $PSScriptRoot 'run-campus.ps1'
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName  = (Get-Command powershell.exe).Source
    $psi.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$script`" -Port $($cfg.Port) -NonInteractive"
    $psi.WorkingDirectory       = $App.Repo
    $psi.UseShellExecute        = $false
    $psi.CreateNoWindow         = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError  = $true
    # Without these, .NET decodes the child's bytes with the console's ANSI code
    # page. npm and vite emit UTF-8 box-drawing and check marks, which then
    # arrive as mojibake ("Î“Ã¶Ã©" where a tick should be) in both the log pane
    # and the log file.
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $psi.StandardErrorEncoding  = [System.Text.Encoding]::UTF8
    # Without this, Python buffers 8 KB before flushing and the log pane sits
    # empty through the whole startup, which reads as a hang.
    $psi.EnvironmentVariables['PYTHONUNBUFFERED'] = '1'

    $App.Proc = New-Object System.Diagnostics.Process
    $App.Proc.StartInfo = $psi
    $App.Proc.EnableRaisingEvents = $true

    # The queue, not a direct UI write: these handlers fire on threadpool
    # threads, and touching a WPF control from one throws. A timer on the UI
    # thread drains it.
    $App.Subs = @(
        (Register-ObjectEvent -InputObject $App.Proc -EventName OutputDataReceived -MessageData $App.Queue -Action {
            if ($null -ne $EventArgs.Data) { $Event.MessageData.Enqueue($EventArgs.Data) } }),
        (Register-ObjectEvent -InputObject $App.Proc -EventName ErrorDataReceived -MessageData $App.Queue -Action {
            if ($null -ne $EventArgs.Data) { $Event.MessageData.Enqueue($EventArgs.Data) } })
    )

    [void]$App.Proc.Start()
    $App.Proc.BeginOutputReadLine()
    $App.Proc.BeginErrorReadLine()
    Write-Log "Starting run-campus.ps1 on port $($cfg.Port) (pid $($App.Proc.Id))" 'note'
}

function Stop-Server {
    param([switch]$Quiet)
    if (-not $App.Proc -or $App.Proc.HasExited) {
        Clear-Server
        if (-not $Quiet) { Set-State 'Stopped' 'Press Start to bring the gate terminal up.' }
        return
    }
    Set-State 'Stopping' 'Closing the camera streams.'
    if (-not $Quiet) { Write-Log 'Stopping the server...' 'note' }

    # taskkill /T, not Kill(): the visible child is powershell.exe, and killing
    # it leaves python and its ffmpeg grandchildren alive holding the port and
    # the RTSP sessions. Orphaned ffmpeg is exactly what freezes the feeds on
    # the next start.
    try {
        Start-Process taskkill -ArgumentList @('/PID', $App.Proc.Id, '/T', '/F') -NoNewWindow -Wait -ErrorAction Stop
    } catch {
        try { $App.Proc.Kill() } catch { }
    }
}

function Clear-Server {
    foreach ($sub in $App.Subs) { try { Unregister-Event -SubscriptionId $sub.Id -ErrorAction SilentlyContinue } catch { } }
    $App.Subs = @()
    if ($App.LogWriter) { try { $App.LogWriter.Flush(); $App.LogWriter.Dispose() } catch { }; $App.LogWriter = $null }
    $App.Proc = $null
    $App.Origin = ''
}

# ---------------------------------------------------------------------------
#  Updates
# ---------------------------------------------------------------------------
function Test-Git { return [bool](Get-Command git.exe -ErrorAction SilentlyContinue) }

# Two things here are not decoration.
#
# -c safe.directory: the installer clones this checkout while ELEVATED, so the
# folder ends up owned by Administrators. Every later git command runs as the
# ordinary account the guard is signed in as, and git refuses to touch a
# repository it thinks belongs to someone else - "fatal: detected dubious
# ownership". Naming this one path is deliberate; safe.directory=* would switch
# the check off for every repository on the machine.
#
# ErrorActionPreference: with it set to Stop, `2>&1` on a NATIVE command turns
# each stderr line into a terminating ErrorRecord. Git writes plenty to stderr
# while succeeding, so a routine warning would take the whole launcher down.
# This is exactly how the dubious-ownership message became a crash instead of a
# handled failure.
function Invoke-Git {
    param([string[]]$GitArgs)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        # Forward slashes, not the native backslashes. Git compares
        # safe.directory against its own internal spelling of the path, which
        # on Windows uses '/'. Passing "C:\Smart Parking\app" does not match
        # "C:/Smart Parking/app" and the ownership check still refuses - which
        # is exactly what git's own hint tells you when it fails.
        $safe = 'safe.directory=' + $App.Repo.Replace('\', '/')
        $out  = & git.exe -c $safe -C $App.Repo @GitArgs 2>&1
        $code = $LASTEXITCODE
    } catch {
        return @{ Ok = $false; Out = @($_.Exception.Message) }
    } finally {
        $ErrorActionPreference = $prev
    }
    return @{ Ok = ($code -eq 0); Out = @($out) }
}

function Start-UpdateCheck {
    if (-not (Test-Git)) {
        $ui.UpdateHead.Text = 'Git is not installed'
        $ui.UpdateSub.Text  = 'Updates cannot be checked or applied on this machine.'
        return
    }
    if ($App.FetchProc -and -not $App.FetchProc.HasExited) { return }

    $ui.UpdateHead.Text = 'Checking for updates...'
    $ui.UpdateSub.Text  = ''
    # Fetch is the only network call here, so it is the only one that runs out
    # of process. rev-list afterwards reads the local object store and is
    # instant, so it can safely happen on the UI thread.
    # The whole "-c name=value" value is one quoted token. Quoting only part of
    # it, as in safe.directory="C:\path", leaves the quotes inside the value
    # where git compares them literally and never matches.
    $safeArg = '"safe.directory=' + $App.Repo.Replace('\', '/') + '"'
    $App.FetchProc = Start-Process git.exe `
        -ArgumentList @('-c', $safeArg,
                        '-C', "`"$($App.Repo)`"", 'fetch', '--quiet', 'origin', $cfg.Branch) `
        -NoNewWindow -PassThru
}

function Complete-UpdateCheck {
    $App.FetchProc = $null
    $App.LastCheck = Get-Date
    $ui.LastCheckText.Text = "last checked " + $App.LastCheck.ToString('HH:mm')

    $count = Invoke-Git @('rev-list', '--count', "HEAD..origin/$($cfg.Branch)")
    if (-not $count.Ok) {
        $ui.UpdateHead.Text = "Cannot reach origin/$($cfg.Branch)"
        $ui.UpdateSub.Text  = ($count.Out | Select-Object -Last 1) -as [string]
        $ui.BtnUpdate.Visibility = 'Collapsed'
        return
    }

    $behind = 0
    if (-not [int]::TryParse("$($count.Out)".Trim(), [ref]$behind)) { $behind = 0 }
    $App.Behind = $behind

    $head = Invoke-Git @('log', '-1', '--pretty=format:%h %s', "origin/$($cfg.Branch)")
    $App.HeadSubject = if ($head.Ok) { "$($head.Out)".Trim() } else { '' }

    $local = Invoke-Git @('log', '-1', '--pretty=format:%h')
    $short = if ($local.Ok) { "$($local.Out)".Trim() } else { '?' }
    $ui.CommitText.Text = "$($cfg.Branch) @ $short"

    if ($App.Behind -gt 0) {
        $word = if ($App.Behind -eq 1) { 'commit' } else { 'commits' }
        $ui.UpdateHead.Text = "$($App.Behind) new $word available"
        $ui.UpdateSub.Text  = $App.HeadSubject
        $ui.BtnUpdate.Visibility = 'Visible'
    } else {
        $ui.UpdateHead.Text = 'Up to date'
        $ui.UpdateSub.Text  = $App.HeadSubject
        $ui.BtnUpdate.Visibility = 'Collapsed'
    }
}

function Invoke-Update {
    $ui.BtnUpdate.IsEnabled = $false
    Write-Log "Updating from origin/$($cfg.Branch)..." 'note'

    # A dirty tree means someone edited this checkout by hand. Discarding that
    # silently to make an update succeed is never the right trade.
    $dirty = Invoke-Git @('status', '--porcelain')
    if ($dirty.Ok -and @($dirty.Out).Count -gt 0) {
        Write-Log 'This checkout has uncommitted changes - refusing to pull over them.' 'error'
        foreach ($l in @($dirty.Out | Select-Object -First 8)) { Write-Log "  $l" 'dim' }
        $ui.UpdateSub.Text = 'Local edits are in the way. Commit or discard them first.'
        $ui.BtnUpdate.IsEnabled = $true
        return
    }

    if ($App.State -eq 'Running' -or $App.State -eq 'Starting') {
        $App.Restarting = $true
        Stop-Server
        return   # picked back up in Complete-Update once the process is gone
    }
    Complete-Update
}

function Complete-Update {
    $App.Restarting = $false

    $current = Invoke-Git @('rev-parse', '--abbrev-ref', 'HEAD')
    if ($current.Ok -and "$($current.Out)".Trim() -ne $cfg.Branch) {
        Write-Log "Switching from $("$($current.Out)".Trim()) to $($cfg.Branch)" 'note'
        $co = Invoke-Git @('checkout', $cfg.Branch)
        foreach ($l in $co.Out) { Write-Log "  $l" 'dim' }
    }

    # --ff-only: this machine is a mirror of the branch, never a place work is
    # authored. A merge commit created here would exist nowhere else and would
    # make the next pull fail in a way nobody at a gate can fix.
    $pull = Invoke-Git @('pull', '--ff-only', 'origin', $cfg.Branch)
    foreach ($l in $pull.Out) { Write-Log "  $l" $(if ($pull.Ok) { 'dim' } else { 'error' }) }

    $ui.BtnUpdate.IsEnabled = $true
    if (-not $pull.Ok) {
        Write-Log 'Update failed. The previous version is untouched.' 'error'
        $ui.UpdateSub.Text = 'Pull failed - see the activity log.'
        return
    }

    Write-Log 'Updated. Restarting the server; the frontend rebuilds if its sources moved.' 'ok'
    Complete-UpdateCheck

    # The launcher is itself part of the checkout, so an update can replace the
    # file this process is running from. PowerShell read it into memory at
    # launch, so those changes only take effect on a fresh process - relaunch
    # rather than let the window quietly lag the code it is serving.
    $selfChanged = @($pull.Out) -match 'campus-launcher\.ps1|campus-config\.ps1'
    if ($selfChanged) {
        Write-Log 'The launcher itself changed - reopening it.' 'note'
        Start-Process powershell.exe -ArgumentList @(
            '-NoProfile', '-WindowStyle', 'Hidden', '-ExecutionPolicy', 'Bypass',
            '-File', "`"$PSCommandPath`""
        ) | Out-Null
        $win.Close()
        return
    }

    Start-Server
}

# ---------------------------------------------------------------------------
#  Credentials overlay
# ---------------------------------------------------------------------------
$SecretBoxes = @{}

function Show-Secrets {
    $ui.SecretsPanel.Children.Clear()
    $SecretBoxes.Clear()

    # One read of .env for the whole form. Asking Get-CampusEnvValue per key
    # would re-read and re-scan the file once for each of the nine rows.
    $envMap = Get-CampusEnvMap $App.EnvFile
    $fieldStyle = $win.FindResource('Field')

    foreach ($s in Get-CampusSecretSpec) {
        $current = if ($envMap.ContainsKey($s.Key)) { $envMap[$s.Key] } else { '' }
        $isSet   = -not (Test-CampusPlaceholder $current)

        $lbl = New-Object Windows.Controls.TextBlock
        $lbl.FontSize = 11.5
        $lbl.Margin = '0,12,0,5'
        $lbl.Foreground = $Palette.Muted
        $lbl.Text = $s.Label + $(if ($s.Required) { '' } else { '   (optional)' })
        [void]$ui.SecretsPanel.Children.Add($lbl)

        $box = New-Object Windows.Controls.TextBox
        $box.Style = $fieldStyle
        $box.FontFamily = New-Object Windows.Media.FontFamily('Consolas')
        # A saved secret is never echoed back into a box someone might
        # screenshot. Non-secret values are, because seeing the current bucket
        # name is the whole point of opening this panel.
        if ($isSet -and -not $s.Hidden) { $box.Text = $current }
        [void]$ui.SecretsPanel.Children.Add($box)

        $hint = New-Object Windows.Controls.TextBlock
        $hint.FontSize = 10.5
        $hint.TextWrapping = 'Wrap'; $hint.Margin = '0,4,0,0'
        $suffix = if ($s.Help) { ' - ' + $s.Help } else { '' }
        if ($isSet) {
            $hint.Foreground = $Palette.Success
            $hint.Text = 'saved' + $suffix
        } else {
            $hint.Foreground = if ($s.Required) { $Palette.Danger } else { $Palette.Placeholder }
            $hint.Text = 'not set' + $suffix
        }
        [void]$ui.SecretsPanel.Children.Add($hint)

        $SecretBoxes[$s.Key] = $box
    }
    $ui.SecretsOverlay.Visibility = 'Visible'
}

function Save-Secrets {
    if (-not (Test-Path $App.EnvFile)) {
        $template = Join-Path $App.Repo 'backend\.env.campus.example'
        if (Test-Path $template) { Copy-Item $template $App.EnvFile }
    }
    $saved = 0
    foreach ($key in $SecretBoxes.Keys) {
        $v = $SecretBoxes[$key].Text
        if ($v -and $v.Trim()) { Set-CampusEnvValue -EnvFile $App.EnvFile -Key $key -Value $v.Trim(); $saved++ }
    }
    $ui.SecretsOverlay.Visibility = 'Collapsed'
    Write-Log "Saved $saved value(s) to backend\.env" 'ok'

    $missing = Get-CampusMissingSecrets -EnvFile $App.EnvFile -RequiredOnly
    if ($missing.Count -gt 0) {
        Write-Log ("Still missing: " + (($missing | ForEach-Object { $_.Key }) -join ', ')) 'warn'
    } elseif ($App.State -ne 'Running') {
        Set-State 'Stopped' 'Credentials are complete. Press Start.'
    }
}

# ---------------------------------------------------------------------------
#  Wiring
# ---------------------------------------------------------------------------
$ui.TitleBar.Add_MouseLeftButtonDown({ $win.DragMove() })
$ui.BtnMin.Add_Click({ $win.WindowState = 'Minimized' })
$ui.BtnClose.Add_Click({ $win.Close() })

$ui.BtnStart.Add_Click({
    if ($App.State -eq 'Running' -or $App.State -eq 'Starting') { Stop-Server } else { Start-Server }
})
$ui.BtnGuard.Add_Click({ Open-CampusPage 'guard' })
$ui.BtnAdmin.Add_Click({ Open-CampusPage 'admin' })
$ui.BtnCopy.Add_Click({
    if ($App.Origin) { [Windows.Clipboard]::SetText($App.Origin); Write-Log "Copied $($App.Origin)" 'dim' }
})
$ui.BtnCheck.Add_Click({ Start-UpdateCheck })
$ui.BtnUpdate.Add_Click({ Invoke-Update })
$ui.BtnClear.Add_Click({ $LogItems.Clear() })
$ui.BtnLogFolder.Add_Click({ Start-Process explorer.exe $LogDir })
$ui.BtnSecrets.Add_Click({ Show-Secrets })
$ui.BtnSecretsCancel.Add_Click({ $ui.SecretsOverlay.Visibility = 'Collapsed' })
$ui.BtnSecretsSave.Add_Click({ Save-Secrets })

$ui.BtnSave.Add_Click({
    $p = 0
    if ([int]::TryParse($ui.TxtPort.Text.Trim(), [ref]$p) -and $p -gt 0 -and $p -lt 65536) {
        $cfg.Port = $p
    } else {
        Write-Log "Ignoring an invalid port: '$($ui.TxtPort.Text)'" 'warn'
        $ui.TxtPort.Text = $cfg.Port
    }
    # Branch is intentionally absent - it is fixed at install time and there is
    # no control here to read it from.
    $cfg.AutoStart   = [bool]$ui.ChkAuto.IsChecked
    $cfg.Kiosk       = [bool]$ui.ChkKiosk.IsChecked
    $cfg.OpenOnStart = "$($ui.CmbOpen.SelectedItem.Tag)"
    Save-CampusLauncherConfig $cfg
    $ui.BranchText.Text = $cfg.Branch
    Write-Log "Settings saved. Port $($cfg.Port), tracking $($cfg.Branch), kiosk $(if($cfg.Kiosk){'on'}else{'off'}), auto-open $($cfg.OpenOnStart)." 'ok'
    if ($App.State -eq 'Running') { Write-Log 'A port change takes effect on the next start.' 'dim' }
    Start-UpdateCheck
})

# ---------------------------------------------------------------------------
#  The one timer that drives everything
# ---------------------------------------------------------------------------
# A single dispatcher timer rather than one per concern: everything below has to
# run on the UI thread anyway, and one tick doing five bounded checks is easier
# to reason about than five timers racing each other. Every branch is O(1)
# except the drain, which is capped at 300 lines per tick - so a burst is spread
# over ticks rather than freezing the window in one of them.
$tick = 0
$timer = New-Object Windows.Threading.DispatcherTimer
$timer.Interval = [TimeSpan]::FromMilliseconds(250)
$timer.Add_Tick({
    $script:tick++

    # 1. drain the server's output
    $line = ''
    $n = 0
    while ($n -lt 300 -and $App.Queue.TryDequeue([ref]$line)) { Read-ServerLine $line; $n++ }
    Update-LogView

    # 2. did the server exit?
    if ($App.Proc -and $App.Proc.HasExited) {
        $code = $App.Proc.ExitCode
        Clear-Server
        if ($App.Restarting) {
            Write-Log 'Server stopped for the update.' 'dim'
            Complete-Update
        } elseif ($App.State -eq 'Stopping') {
            Set-State 'Stopped' 'Stopped. Guards cannot reach this machine while it is down.'
            Write-Log 'Server stopped.' 'note'
        } else {
            Set-State 'Failed' "The server exited on its own (code $code). The log above says why."
            Write-Log "run-campus.ps1 exited with code $code" 'error'
        }
    }

    # 3. did the background fetch finish?
    if ($App.FetchProc -and $App.FetchProc.HasExited) { Complete-UpdateCheck }

    # 4. time for another check?
    $every = [int]$cfg.UpdatePollMinutes
    if ($every -lt 1) { $every = 1 }
    if ($script:tick % ($every * 240) -eq 0) { Start-UpdateCheck }

    # 5. keep the pulse on the state dot honest while starting
    if ($App.State -eq 'Starting') {
        $ui.Dot.Opacity = 0.35 + 0.65 * [math]::Abs([math]::Sin($script:tick / 6.0))
    } elseif ($ui.Dot.Opacity -ne 1) {
        $ui.Dot.Opacity = 1
    }
})

# ---------------------------------------------------------------------------
#  Boot
# ---------------------------------------------------------------------------
# DecodePixelWidth is the whole reason the mark is legible. Without it WPF
# decodes the 394px source at full size and then squeezes it into 36 logical
# pixels with a box filter, which is what made the seal look chewed. Decoding
# straight to the device size lets it use a proper filter, and Freeze() stops
# WPF re-evaluating a bitmap that will never change.
function Set-LogoImage {
    param([int]$LogicalSize = 36)

    $logo = Join-Path $repo 'frontend\src\assets\slclogo.jpg'
    if (-not (Test-Path $logo)) { return }
    try {
        # Read the real device scale rather than assuming 96 DPI. This runs
        # after the window has a PresentationSource, which is the only point at
        # which that number exists; at 150% the mark needs 54 physical pixels,
        # and decoding 36 would look exactly as soft as decoding 394 did.
        $dpi = 1.0
        $src = [Windows.PresentationSource]::FromVisual($win)
        if ($src -and $src.CompositionTarget) { $dpi = $src.CompositionTarget.TransformToDevice.M11 }
        if ($dpi -le 0) { $dpi = 1.0 }

        $bmp = New-Object Windows.Media.Imaging.BitmapImage
        $bmp.BeginInit()
        $bmp.UriSource = New-Object Uri($logo)
        # Decode at 2x the device size and let the HighQuality (Fant) filter in
        # the Image do the last halving. The seal is fine line art; giving the
        # good filter some headroom is what keeps its lettering from breaking up.
        $bmp.DecodePixelWidth = [int][math]::Ceiling($LogicalSize * $dpi * 2)
        $bmp.CacheOption = 'OnLoad'
        $bmp.EndInit()
        $bmp.Freeze()

        # UniformToFill on the brush, so a source that is not perfectly square
        # is cropped to the circle rather than squashed into it.
        $brush = New-Object Windows.Media.ImageBrush($bmp)
        $brush.Stretch = 'UniformToFill'
        $brush.Freeze()
        $ui.Logo.Fill = $brush
        $ui.LogoFallback.Visibility = 'Collapsed'
    } catch {
        # The lettermark under it stays visible; a missing or unreadable logo is
        # not a reason to keep the gate terminal from starting.
    }
}

$ui.TxtPort.Text    = $cfg.Port
# BranchText is the read-only chip on the UPDATES card - the only place the
# branch is shown now that it cannot be edited.
$ui.BranchText.Text = $cfg.Branch
$ui.ChkAuto.IsChecked  = [bool]$cfg.AutoStart
$ui.ChkKiosk.IsChecked = [bool]$cfg.Kiosk
$ui.CmbOpen.SelectedIndex = switch ("$($cfg.OpenOnStart)") { 'guard' { 1 } 'admin' { 2 } default { 0 } }
$ui.RepoText.Text   = $App.Repo
$ui.CommitText.Text = "$($cfg.Branch) @ ..."

$win.Add_ContentRendered({
    Set-LogoImage 36
    Set-State 'Stopped'
    Write-Log 'Smart Parking and Vehicle Verification System - campus launcher' 'note'
    Write-Log "Repository: $($App.Repo)" 'dim'

    $missing = Get-CampusMissingSecrets -EnvFile $App.EnvFile -RequiredOnly
    if ($missing.Count -gt 0) {
        Write-Log ("Shared credentials not set yet: " + (($missing | ForEach-Object { $_.Key }) -join ', ')) 'warn'
        Set-State 'Stopped' 'This machine is not configured yet.'
        Show-Secrets
    } elseif ($cfg.AutoStart -and -not $NoAutoStart) {
        Start-Server
    }
    Update-LogView
    Start-UpdateCheck
})

# Closing the window must take the server with it. Leaving daphne running
# headless would hold port 8000 and the RTSP sessions, and the next start would
# fail with an address already in use that has no visible cause.
$win.Add_Closing({
    $timer.Stop()
    if ($App.Proc -and -not $App.Proc.HasExited) { Stop-Server -Quiet }
    Clear-Server

    # Take the kiosk window with us. A full-screen browser with no address bar,
    # left pointing at a server that just stopped, is the worst thing to leave
    # on a gate terminal: nothing to navigate away with and nothing behind it.
    [void](Close-KioskBrowser)
})

$timer.Start()
[void]$win.ShowDialog()

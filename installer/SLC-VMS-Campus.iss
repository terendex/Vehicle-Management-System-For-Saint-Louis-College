; ============================================================================
;  SLC Vehicle Management System - campus installer
;
;  Build it with installer\build.ps1, which fetches Inno Setup if it is not
;  already on the machine and generates the icon and wizard artwork first.
;
;  This installer is small on purpose. It does not carry the application: it
;  installs a launcher folder and then runs bootstrap.ps1, which fetches the
;  prerequisites and clones the repository into {app}\app. That is what lets
;  the installed copy update itself afterwards - the application half is a git
;  checkout, so a `git pull` is the whole update mechanism, and the installer
;  never has to be reissued for an ordinary code change.
;
;  Consequences worth knowing:
;    * the setup .exe stays around 2 MB instead of several hundred
;    * the machine needs internet the first time it runs, and only then
;    * {app} is granted Users:modify so updates never need elevation
; ============================================================================

#define AppName        "SLC Vehicle Management"
#define AppShortName   "SLC VMS"
#define AppVersion     "1.0.0"
#define AppPublisher   "Saint Louis College"
#define AppURL         "https://github.com/terendex/Vehicle-Management-System-For-Saint-Louis-College"
#define DefaultBranch  "jonas"
#define DefaultPort    "8000"
#define RegKey         "Software\Saint Louis College\SLC VMS"

[Setup]
; The AppId is the upgrade identity. Never change it: a new one makes Windows
; treat the next release as a separate product, so two entries appear in
; Add/Remove Programs and the old files are never replaced.
AppId={{7F3C1B84-2E5A-4E1C-9C2D-5B7A9E4D1A60}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
VersionInfoVersion={#AppVersion}
VersionInfoDescription={#AppName} campus installer
VersionInfoCompany={#AppPublisher}

DefaultGroupName={#AppName}
AllowNoIcons=yes

#ifdef UITEST
; build.ps1 -UiTest only. Produces a throwaway build that runs unelevated so
; the wizard pages can be walked through and checked on a machine where a UAC
; prompt cannot be answered. It installs nowhere useful and must never be
; shipped - the real settings are in the #else branch, and UITEST is only ever
; defined on the ISCC command line.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\SLC-VMS-UITest
OutputBaseFilename=SLC-VMS-Campus-Setup-UITEST
#else
; Not Program Files. The application half is a git checkout that updates itself,
; and a checkout under Program Files can only be written by an elevated process
; - which would mean a UAC prompt on every routine update at the gate.
DefaultDirName={sd}\SLC-VMS
; The bootstrap installs Python, Node and Git and adds a firewall rule.
PrivilegesRequired=admin
OutputBaseFilename=SLC-VMS-Campus-Setup
#endif

; Windows 10 1809 is where winget became available; without it the prerequisite
; steps have nothing to install with. 64-bit only - torch has no 32-bit wheels.
MinVersion=10.0.17763
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; The installer copies about 2 MB. Everything else arrives afterwards: a 300 MB
; clone, a virtualenv that reaches ~5.7 GB once torch is in it, and ~255 MB of
; node modules. Reporting only the copied bytes would let someone install onto
; a disk that cannot possibly finish the job.
ExtraDiskSpaceRequired=6400000000

LicenseFile=LICENSE.txt
OutputDir=out
SetupIconFile=assets\slc-vms.ico
WizardStyle=modern
WizardImageFile=assets\wizard-large.bmp
WizardSmallImageFile=assets\wizard-small.bmp
WizardImageStretch=yes
Compression=lzma2/max
SolidCompression=yes
UninstallDisplayIcon={app}\launcher\slc-vms.ico
UninstallDisplayName={#AppName} (campus)
CloseApplications=no
; So Windows broadcasts WM_SETTINGCHANGE for the optional PATH entry instead of
; leaving it invisible until the next sign-out.
ChangesEnvironment=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Types]
Name: "full";   Description: "Full installation - everything this machine needs"
Name: "custom"; Description: "Custom installation"; Flags: iscustom

[Components]
Name: "core";          Description: "Campus launcher and application files"; \
  Types: full custom; Flags: fixed
Name: "prereq";        Description: "Prerequisites"; Types: full custom
Name: "prereq\git";    Description: "Git - required for updates"; Types: full custom
Name: "prereq\python"; Description: "Python 3.11 - runs the server and the detector"; Types: full custom
Name: "prereq\node";   Description: "Node.js LTS - builds the web interface"; Types: full custom
Name: "firewall";      Description: "Windows Firewall rule, so guards can reach this machine"; \
  Types: full custom

[Tasks]
Name: "desktopicon"; Description: "Create a shortcut on the desktop"; GroupDescription: "Shortcuts:"
Name: "startup";     Description: "Open the launcher when this computer starts"; \
  GroupDescription: "Shortcuts:"; Flags: unchecked
Name: "addtopath";   Description: "Add the launcher folder to the system PATH"; \
  GroupDescription: "Other:"; Flags: unchecked

[Dirs]
; The point of installing outside Program Files: an ordinary account has to be
; able to `git pull` into {app}\app without being elevated first.
Name: "{app}";        Permissions: users-modify
Name: "{app}\launcher"

[Files]
Source: "bootstrap.ps1";      DestDir: "{app}\launcher"; Flags: ignoreversion
Source: "start-campus.ps1";   DestDir: "{app}\launcher"; Flags: ignoreversion
Source: "start-campus.vbs";   DestDir: "{app}\launcher"; Flags: ignoreversion
Source: "LICENSE.txt";        DestDir: "{app}\launcher"; Flags: ignoreversion
Source: "assets\slc-vms.ico"; DestDir: "{app}\launcher"; Flags: ignoreversion
Source: "assets\slclogo.jpg"; DestDir: "{app}\launcher"; Flags: ignoreversion

[Registry]
; This is what a later install reads to work out fresh / upgrade / reinstall,
; and what tells a support call which version is actually on the machine.
Root: HKLM; Subkey: "{#RegKey}"; Flags: uninsdeletekeyifempty
Root: HKLM; Subkey: "{#RegKey}"; ValueType: string; ValueName: "Version";     ValueData: "{#AppVersion}"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "{#RegKey}"; ValueType: string; ValueName: "InstallPath"; ValueData: "{app}";         Flags: uninsdeletevalue
Root: HKLM; Subkey: "{#RegKey}"; ValueType: string; ValueName: "Branch";      ValueData: "{code:GetBranch}"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "{#RegKey}"; ValueType: string; ValueName: "Port";        ValueData: "{code:GetPort}";   Flags: uninsdeletevalue
Root: HKLM; Subkey: "{#RegKey}"; ValueType: string; ValueName: "InstalledOn"; ValueData: "{code:GetToday}";  Flags: uninsdeletevalue

; Optional PATH entry. Appended rather than prepended - this folder holds two
; scripts, and putting it in front of the system directories to reach them is a
; poor trade. Removed again by RemoveFromPath on uninstall.
Root: HKLM; Subkey: "SYSTEM\CurrentControlSet\Control\Session Manager\Environment"; \
  ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}\launcher"; \
  Tasks: addtopath; Check: NeedsAddPath(ExpandConstant('{app}\launcher'))

[Icons]
; Pointed at the .vbs rather than at powershell.exe so starting the launcher
; does not flash a console window on the guard's screen.
Name: "{group}\{#AppName}"; Filename: "{sys}\wscript.exe"; \
  Parameters: """{app}\launcher\start-campus.vbs"""; \
  WorkingDir: "{app}\launcher"; IconFilename: "{app}\launcher\slc-vms.ico"; \
  Comment: "Start the campus gate terminal"

Name: "{group}\Repair installation"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\launcher\start-campus.ps1"" -Repair"; \
  WorkingDir: "{app}\launcher"; IconFilename: "{app}\launcher\slc-vms.ico"; \
  Comment: "Re-check the prerequisites and re-download the application"

Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"

Name: "{autodesktop}\{#AppName}"; Filename: "{sys}\wscript.exe"; \
  Parameters: """{app}\launcher\start-campus.vbs"""; \
  WorkingDir: "{app}\launcher"; IconFilename: "{app}\launcher\slc-vms.ico"; Tasks: desktopicon

Name: "{userstartup}\{#AppName}"; Filename: "{sys}\wscript.exe"; \
  Parameters: """{app}\launcher\start-campus.vbs"""; \
  WorkingDir: "{app}\launcher"; IconFilename: "{app}\launcher\slc-vms.ico"; Tasks: startup

[Run]
; The long part of the install. bootstrap.ps1 draws its own progress window, so
; the wizard just waits behind it.
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\launcher\bootstrap.ps1"" -InstallDir ""{app}"" -Branch ""{code:GetBranch}"" -Port {code:GetPort} -Steps ""{code:GetSteps}"""; \
  StatusMsg: "Installing prerequisites and downloading the application..."; \
  Flags: waituntilterminated

Filename: "{sys}\wscript.exe"; Parameters: """{app}\launcher\start-campus.vbs"""; \
  Description: "Open the campus launcher now"; \
  Flags: postinstall nowait skipifsilent

[Code]
var
  OptPage:   TInputQueryWizardPage;
  PrereqPage: TOutputMsgMemoWizardPage;
  PrevVersion: String;
  InstallMode: String;   // fresh | upgrade | reinstall

// ---------------------------------------------------------------------------
//  Prerequisite detection
// ---------------------------------------------------------------------------
// Repeated in bootstrap.ps1, which is the one that actually installs them. This
// copy exists only so the wizard can tell the operator what is about to happen
// BEFORE they commit - a summary that appears after the download has started is
// not a check, it is a report.
function IsGitInstalled(): Boolean;
var S: String;
begin
  Result := FileExists(ExpandConstant('{pf}\Git\cmd\git.exe')) or
            FileExists(ExpandConstant('{pf32}\Git\cmd\git.exe')) or
            FileExists(ExpandConstant('{localappdata}\Programs\Git\cmd\git.exe'));
  if not Result then
    Result := RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\git.exe', '', S) or
              RegQueryStringValue(HKCU, 'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\git.exe', '', S);
end;

function IsPython311Installed(): Boolean;
var S: String;
begin
  Result := FileExists(ExpandConstant('{localappdata}\Programs\Python\Python311\python.exe')) or
            FileExists(ExpandConstant('{pf}\Python311\python.exe')) or
            FileExists('C:\Python311\python.exe');
  if not Result then
    Result := RegQueryStringValue(HKLM, 'SOFTWARE\Python\PythonCore\3.11\InstallPath', '', S) or
              RegQueryStringValue(HKCU, 'SOFTWARE\Python\PythonCore\3.11\InstallPath', '', S);
end;

function IsNodeInstalled(): Boolean;
begin
  Result := FileExists(ExpandConstant('{pf}\nodejs\node.exe')) or
            FileExists(ExpandConstant('{pf32}\nodejs\node.exe'));
end;

function IsWingetAvailable(): Boolean;
begin
  Result := FileExists(ExpandConstant('{localappdata}\Microsoft\WindowsApps\winget.exe'));
end;

function StatusLine(Name: String; Present: Boolean): String;
begin
  if Present then
    Result := '  [installed]  ' + Name + #13#10
  else
    Result := '  [will install]  ' + Name + #13#10;
end;

// ---------------------------------------------------------------------------
//  Version handling
// ---------------------------------------------------------------------------
function GetPreviousVersion(): String;
begin
  Result := '';
  if not RegQueryStringValue(HKLM, '{#RegKey}', 'Version', Result) then
    Result := '';
end;

function InitializeSetup(): Boolean;
var
  PrevPacked, ThisPacked: Int64;
begin
  Result := True;

  if not IsWin64 then
  begin
    MsgBox('This system needs 64-bit Windows.' + #13#10 + #13#10 +
           'The detector depends on PyTorch, which publishes no 32-bit builds.',
           mbCriticalError, MB_OK);
    Result := False;
    Exit;
  end;

  PrevVersion := GetPreviousVersion();
  if PrevVersion = '' then
  begin
    InstallMode := 'fresh';
    Exit;
  end;

  // A packed comparison, not a string one: '1.10.0' sorts before '1.9.0'
  // alphabetically, which would call a real upgrade a downgrade and block it.
  if StrToVersion(PrevVersion, PrevPacked) and StrToVersion('{#AppVersion}', ThisPacked) then
  begin
    if ComparePackedVersion(PrevPacked, ThisPacked) > 0 then
    begin
      MsgBox('A newer version is already installed.' + #13#10 + #13#10 +
             'Installed: ' + PrevVersion + #13#10 +
             'This setup: {#AppVersion}' + #13#10 + #13#10 +
             'Installing over it would replace working software with older software. ' +
             'Uninstall the newer version first if you genuinely mean to go back.',
             mbError, MB_OK);
      Result := False;
      Exit;
    end;
    if ComparePackedVersion(PrevPacked, ThisPacked) = 0 then
      InstallMode := 'reinstall'
    else
      InstallMode := 'upgrade';
  end
  else
    InstallMode := 'upgrade';   // unreadable stored version: treat as older

  // In-place, deliberately. The uninstaller would offer to delete {app}\app,
  // and an upgrade must never put that question - or a 300 MB re-download - in
  // front of someone who only wanted a newer launcher.
  if InstallMode = 'upgrade' then
    MsgBox('Upgrading the existing installation.' + #13#10 + #13#10 +
           'Installed: ' + PrevVersion + '   ->   New: {#AppVersion}' + #13#10 + #13#10 +
           'Your credentials, settings and downloaded application are kept. ' +
           'The application itself updates from git, not from this installer.',
           mbInformation, MB_OK);
end;

// ---------------------------------------------------------------------------
//  PATH
// ---------------------------------------------------------------------------
function NeedsAddPath(Param: String): Boolean;
var
  OrigPath: String;
begin
  if not WizardIsTaskSelected('addtopath') then
  begin
    Result := False;
    Exit;
  end;
  if not RegQueryStringValue(HKLM, 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment',
                             'Path', OrigPath) then
  begin
    Result := True;
    Exit;
  end;
  // Semicolons around both sides so "C:\SLC-VMS\launcher" does not match
  // "C:\SLC-VMS\launcher-old".
  Result := Pos(';' + Uppercase(Param) + ';', ';' + Uppercase(OrigPath) + ';') = 0;
end;

procedure RemoveFromPath(const Dir: String);
var
  OrigPath, NewPath: String;
  P: Integer;
begin
  if not RegQueryStringValue(HKLM, 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment',
                             'Path', OrigPath) then
    Exit;
  P := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(OrigPath) + ';');
  if P = 0 then
    Exit;
  NewPath := Copy(OrigPath, 1, P - 1) + Copy(OrigPath, P + Length(Dir) + 1, Length(OrigPath));
  // A leading or doubled semicolon left behind is harmless but untidy, and
  // tends to get blamed for unrelated PATH problems later.
  StringChangeEx(NewPath, ';;', ';', True);
  if (Length(NewPath) > 0) and (NewPath[1] = ';') then
    Delete(NewPath, 1, 1);
  RegWriteExpandStringValue(HKLM, 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment',
                            'Path', NewPath);
end;

// ---------------------------------------------------------------------------
//  Wizard
// ---------------------------------------------------------------------------
procedure InitializeWizard;
begin
  OptPage := CreateInputQueryPage(wpSelectComponents,
    'Deployment options',
    'Which code should this machine run, and where should guards reach it?',
    'The branch is what the launcher watches for updates. It checks every few minutes and offers the update; ' +
    'it never restarts the server on its own, because that would drop the camera feeds mid-shift.');
  OptPage.Add('Git branch to track:', False);
  OptPage.Add('Port to serve on:', False);
  OptPage.Values[0] := '{#DefaultBranch}';
  OptPage.Values[1] := '{#DefaultPort}';

  PrereqPage := CreateOutputMsgMemoPage(OptPage.ID,
    'Prerequisites', 'What this machine already has',
    'Anything marked "will install" is fetched through winget during setup. Deselect it on the ' +
    'previous page if your IT department manages it centrally.',
    '');
end;

procedure CurPageChanged(CurPageID: Integer);
var
  S: String;
begin
  if CurPageID <> PrereqPage.ID then
    Exit;

  S := StatusLine('Git', IsGitInstalled()) +
       StatusLine('Python 3.11', IsPython311Installed()) +
       StatusLine('Node.js', IsNodeInstalled()) + #13#10;

  if IsWingetAvailable() then
    S := S + 'winget is available, so anything missing can be installed automatically.' + #13#10
  else
    S := S + 'WARNING: winget was not found on this machine. Missing prerequisites' + #13#10 +
             'cannot be installed automatically - setup will name them and carry on,' + #13#10 +
             'and you will have to install them by hand before the server can start.' + #13#10;

  S := S + #13#10 +
       'After setup, the first launch builds a Python environment of roughly 5.7 GB' + #13#10 +
       '(PyTorch and the detector) and downloads about 300 MB of application code.' + #13#10 +
       'That happens in the launcher window, where you can watch it.';

  PrereqPage.RichEditViewer.Text := S;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  P: Integer;
  FreeBytes, TotalBytes: Int64;
  NeededGB: Integer;
  Drive: String;
begin
  Result := True;

  if CurPageID = wpSelectDir then
  begin
    // Windows caps most paths at 260 characters. The application half is a git
    // checkout whose deepest file sits about 60 characters below its root, and
    // git reports running out of room as "fatal: '$GIT_DIR' too big" - an error
    // that names neither the path nor the limit. Refuse the directory here
    // instead, where it can still be changed.
    if Length(WizardDirValue) > 110 then
    begin
      MsgBox('That folder path is too long (' + IntToStr(Length(WizardDirValue)) + ' characters).' + #13#10 + #13#10 +
             'The application is downloaded as a git checkout, and Windows'' 260-character path limit ' +
             'will break it partway through. Choose something shorter, such as C:\SLC-VMS.',
             mbError, MB_OK);
      Result := False;
      Exit;
    end;

    // Inno's own space check only counts the 2 MB it copies. The install is not
    // finished until a ~5.7 GB virtualenv and a ~300 MB checkout exist, and a
    // disk that cannot hold those fails hours later, inside pip.
    NeededGB := 8;
    Drive := ExtractFileDrive(WizardDirValue) + '\';
    if GetSpaceOnDisk64(Drive, FreeBytes, TotalBytes) then
    begin
      if FreeBytes < Int64(NeededGB) * 1073741824 then
      begin
        if MsgBox('There may not be enough room on ' + Drive + '.' + #13#10 + #13#10 +
                  'Free now:    ' + IntToStr(FreeBytes div 1073741824) + ' GB' + #13#10 +
                  'Recommended: ' + IntToStr(NeededGB) + ' GB' + #13#10 + #13#10 +
                  'Most of that is the Python environment built on first run - PyTorch alone is ' +
                  'several gigabytes. Setup itself will succeed; the first launch is what would fail.' + #13#10 + #13#10 +
                  'Continue anyway?', mbConfirmation, MB_YESNO) = IDNO then
        begin
          Result := False;
          Exit;
        end;
      end;
    end;
  end;

  if CurPageID = OptPage.ID then
  begin
    if Trim(OptPage.Values[0]) = '' then
    begin
      MsgBox('Enter the branch this machine should track, for example "main".', mbError, MB_OK);
      Result := False;
      Exit;
    end;
    P := StrToIntDef(Trim(OptPage.Values[1]), 0);
    if (P <= 0) or (P > 65535) then
    begin
      MsgBox('Enter a port between 1 and 65535. The default is 8000.', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

function GetBranch(Param: String): String;
begin
  Result := Trim(OptPage.Values[0]);
end;

function GetPort(Param: String): String;
begin
  Result := Trim(OptPage.Values[1]);
end;

function GetToday(Param: String): String;
begin
  Result := GetDateTimeString('yyyy-mm-dd', '-', ':');
end;

// The component page drives what bootstrap.ps1 does. 'app' is not optional -
// it comes from the fixed 'core' component - but it is passed explicitly so
// the script has one consistent input rather than two sources of truth.
function GetSteps(Param: String): String;
var
  S: String;
begin
  S := 'app';
  if WizardIsComponentSelected('prereq\git')    then S := S + ',git';
  if WizardIsComponentSelected('prereq\python') then S := S + ',python';
  if WizardIsComponentSelected('prereq\node')   then S := S + ',node';
  if WizardIsComponentSelected('firewall')      then S := S + ',firewall';
  Result := S;
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo,
  MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
var
  S: String;
begin
  if InstallMode = 'upgrade' then
    S := 'Upgrade from ' + PrevVersion + ' to {#AppVersion}' + NewLine + NewLine
  else if InstallMode = 'reinstall' then
    S := 'Reinstall of version {#AppVersion}' + NewLine + NewLine
  else
    S := 'New installation of version {#AppVersion}' + NewLine + NewLine;

  S := S + MemoDirInfo + NewLine + NewLine;
  S := S + MemoTypeInfo + NewLine + NewLine;
  S := S + MemoComponentsInfo + NewLine;
  S := S + MemoGroupInfo + NewLine + NewLine;
  if MemoTasksInfo <> '' then
    S := S + MemoTasksInfo + NewLine + NewLine;

  S := S + 'Deployment:' + NewLine;
  S := S + Space + 'Tracking branch ' + GetBranch('') + ', serving on port ' + GetPort('') + NewLine;
  Result := S;
end;

// ---------------------------------------------------------------------------
//  Uninstall
// ---------------------------------------------------------------------------
// Three things outlive a plain uninstall, and they are not equally disposable:
//
//   {app}\app                 the checkout - backend\.env with the shared
//                             Railway secret key and Neon URL, the virtualenv,
//                             and any model weights trained on this machine
//   %LOCALAPPDATA%\SLC-VMS    launcher settings, kiosk browser profile, logs
//   the "SLC VMS" firewall rule and the PATH entry
//
// The last two are ours and nothing else uses them, so they go without asking.
// The other two are the operator's data, and deleting a working configuration
// because someone reinstalled is not a decision an uninstaller should make on
// its own - so it is one clear question, asked once, defaulting to keeping it.

// The launcher holds the checkout open - a running daphne locks files under it
// and the uninstall would fail halfway. Targeted by window title so this cannot
// take out an unrelated PowerShell session.
procedure CloseLauncher();
var
  Rc: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'),
       '/F /T /FI "WINDOWTITLE eq SLC Vehicle Management - Campus*"',
       '', SW_HIDE, ewWaitUntilTerminated, Rc);
end;

function InitializeUninstall(): Boolean;
begin
  Result := True;
  CloseLauncher();
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  AppDir, DataDir: String;
  Rc: Integer;
  KeepData: Boolean;
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;

  AppDir  := ExpandConstant('{app}\app');
  DataDir := ExpandConstant('{localappdata}\SLC-VMS');

  // Ours to remove. Leaving an allow rule behind for a port nothing serves is
  // an open hole with no owner.
  Exec(ExpandConstant('{sys}\netsh.exe'),
       'advfirewall firewall delete rule name="SLC VMS"',
       '', SW_HIDE, ewWaitUntilTerminated, Rc);

  RemoveFromPath(ExpandConstant('{app}\launcher'));

  if not (DirExists(AppDir) or DirExists(DataDir)) then
    Exit;

  KeepData := MsgBox(
    'Keep this machine''s data?' + #13#10 + #13#10 +
    'KEEP  (Yes)  leaves the downloaded application, the shared credentials in' + #13#10 +
    '             backend\.env, the Python environment, and the launcher''s' + #13#10 +
    '             settings and logs in place. Reinstalling later then needs no' + #13#10 +
    '             download and no reconfiguration.' + #13#10 + #13#10 +
    'DELETE (No)  removes all of it, including the saved Railway secret key and' + #13#10 +
    '             database URL. This cannot be undone.' + #13#10 + #13#10 +
    'Neither choice touches the shared database - every record lives in Neon,' + #13#10 +
    'which the cloud deployment is still serving.' + #13#10 + #13#10 +
    AppDir + #13#10 + DataDir,
    mbConfirmation, MB_YESNO) = IDYES;

  if KeepData then
    Exit;

  if DirExists(AppDir) then
  begin
    if not DelTree(AppDir, True, True, True) then
      MsgBox('Some files under' + #13#10 + AppDir + #13#10 +
             'could not be removed - something is probably still using them. ' +
             'Delete the folder by hand after the next restart.', mbInformation, MB_OK);
  end;
  if DirExists(DataDir) then
    DelTree(DataDir, True, True, True);
end;

# Campus installer

Builds `SLC-Smart-Parking-Campus-Setup.exe`, the setup program for the on-site half of the
hybrid deployment. What it installs and why the campus half exists at all is in
[CAMPUS_SETUP.md](../CAMPUS_SETUP.md); this file is about the build.

```powershell
powershell -ExecutionPolicy Bypass -File installer\build.ps1
```

The first run fetches the Inno Setup compiler through winget. The output lands
in `installer\out\`.

## The shape of it

The installer does not contain the application. It installs a small, stable
launcher folder and then clones the repository:

```
C:\SLC-VMS\                     chosen in the wizard; Users:modify
├── unins000.exe
├── launcher\                   shipped by the installer, never updated by git
│   ├── start-campus.vbs        what the shortcut runs
│   ├── start-campus.ps1        finds the checkout, or repairs it, then hands off
│   ├── bootstrap.ps1           prerequisites, clone, firewall
│   ├── slc-vms.ico
│   └── slclogo.jpg
└── app\                        the git checkout - this is what updates itself
    └── scripts\campus-launcher.ps1
```

Two consequences worth keeping in mind:

**The setup .exe is ~2 MB and almost never needs reissuing.** An ordinary code
change reaches installed machines through `git pull`, which the launcher offers
as a button. Rebuild this only when something in `installer\` changes.

**The clone is ~182 MB**, measured against the real remote for one branch. That
is a plain full clone on purpose: `--filter=blob:none` would save about 60 MB of
historical blobs at the cost of a lazy fetch on every later checkout, and
`--depth 1` grafts a new root on each fetch, which breaks the `pull --ff-only`
the update button depends on. Neither trade is worth 60 MB.

**The tracked branch has to carry `scripts\campus-launcher.ps1`.** The installer
clones from GitHub; it cannot install code that has not been pushed. When the
file is absent the setup window names it rather than reporting a vague download
failure, and **Repair installation** re-runs the download once it is there.

**`launcher\` is the part that cannot update itself**, so it is kept as small as
possible and everything interesting lives in `app\`. `start-campus.ps1` exists
only to bridge the two — that indirection is what lets `campus-launcher.ps1` be
updated by a pull like any other file.

## Files

| | |
|---|---|
| `SLC-VMS-Campus.iss` | the Inno script: wizard pages, shortcuts, uninstall |
| `bootstrap.ps1` | the setup window - winget prerequisites, clone, firewall rule |
| `start-campus.ps1` | stage 0: repair if the checkout is missing, otherwise hand off |
| `start-campus.vbs` | runs the above with no console window, so launching does not flash |
| `make-assets.ps1` | generates the icon and wizard bitmaps from the app's own logo |
| `build.ps1` | fetches Inno if needed, regenerates artwork, compiles |
| `assets/`, `out/` | generated - both gitignored |

## What the wizard asks

Welcome → **License** (accept required; `LICENSE.txt`) → **Install location** →
**Components** → **Deployment options** (branch, port) → **Prerequisites**
(what is already installed, checked *before* committing) → **Start Menu folder**
→ **Additional tasks** → **Ready** → install → **Launch now**.

Components are real, not decoration: the selection becomes `-Steps git,python,
node,app,firewall` on the bootstrap command line, and a deselected prerequisite
is left alone and shown as *not selected* rather than silently skipped. An IT
department that manages Python centrally can turn it off.

Additional tasks are desktop shortcut, open-on-startup, and add-to-PATH. There
is **no file association** — this application owns no file type, and inventing
one to fill a checklist item would put a meaningless entry in the shell.

## Version handling

The install writes `HKLM\Software\Saint Louis College\SLC VMS`: `Version`,
`InstallPath`, `Branch`, `Port`, `InstalledOn`. On the next run setup reads
`Version` back and decides:

| | |
|---|---|
| no key | fresh install |
| same version | reinstall / repair |
| older version | upgrade, in place — says what it is keeping |
| **newer version** | **blocked**, with both versions named |

The comparison uses `StrToVersion` + `ComparePackedVersion`, not string
ordering — `1.10.0` sorts before `1.9.0` alphabetically, which would call a real
upgrade a downgrade and refuse it.

Upgrades install **in place** rather than uninstalling first. The uninstaller
asks whether to delete `{app}\app`, and an upgrade must never put that question
— or a 300 MB re-download — in front of someone who only wanted a newer
launcher. The `AppId` is the upgrade identity and must never change; a new one
makes Windows treat the next release as a separate product.

## Checks before anything is written

- **Architecture** — `ArchitecturesAllowed=x64compatible`, plus an explicit
  `IsWin64` message, because PyTorch publishes no 32-bit builds.
- **OS version** — `MinVersion=10.0.17763`; winget, which installs every
  prerequisite, arrived in Windows 10 1809.
- **Disk space** — `ExtraDiskSpaceRequired` reports ~6 GB on the components
  page, and the directory page checks free space against 8 GB. Inno's own
  figure would only count the 2 MB it copies; the install is not finished until
  a ~5.7 GB virtualenv exists, and a disk that cannot hold it fails hours later
  inside pip.
- **Path length** — refuses a folder over 110 characters. Git reports blowing
  Windows' 260-character limit as `fatal: '$GIT_DIR' too big`, which names
  neither the path nor the limit.
- **Prerequisites** — detected and shown on their own page before the Ready
  page, so "will install" is visible while it can still be changed.

## Config migration

`campus-config.ps1` carries `ConfigVersion` and a `Convert-CampusLauncherConfig`
step that runs on the raw JSON before defaults are merged. Adding a key needs no
migration — the merge supplies its default, verified by round-tripping a
pre-versioning config. Bump the version only when an existing key's *meaning*
changes and old values have to be rewritten.

## Testing the wizard

```powershell
powershell -ExecutionPolicy Bypass -File installer\build.ps1 -UiTest
```

Builds `SLC-Smart-Parking-Campus-Setup-UITEST.exe`, which runs unelevated and installs to
a throwaway folder, so the pages can be walked through on a machine where a UAC
prompt cannot be answered. `UITEST` is only ever defined on the ISCC command
line; the shipped build is the `#else` branch. Never distribute the UITEST exe.

## Notes

**The artwork is a build output.** `make-assets.ps1` derives the icon and both
wizard bitmaps from `frontend\src\assets\slclogo.jpg` and the brand navy the web
app already uses. Replace the logo there and the installer follows on the next
build, instead of quietly shipping last year's mark.

**The .ico is written by hand, not by `Icon.Save`.** System.Drawing can only
round-trip the single frame an `Icon` was constructed from, so an icon made that
way is soft on the desktop and blocky in the taskbar. `make-assets.ps1` writes
the container itself: DIB frames at 16-128 (GDI+ and the Inno compiler both
still expect DIBs and render a PNG-only icon as noise) and a PNG frame at 256,
where a DIB would be a megabyte.

**The setup .exe is unsigned.** SmartScreen will warn the first several hundred
people who run it, which is survivable for a file handed to one campus IT
office and not worth a yearly code-signing certificate. If you get one,
`build.ps1 -Sign` runs signtool with whatever is in `SIGNTOOL_ARGS`.

**winget is required on the target machine** for the prerequisite steps. It ships
with Windows 11. On an older image the bootstrap says so per missing tool and
carries on with the rest rather than failing the whole install.

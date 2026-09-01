<#
    run-campus.ps1 — start the on-site half of the hybrid deployment.

    Goal: a freshly cloned repo plus this one command ends up serving.
    Everything the script can work out for itself, it works out — you only ever
    supply the values that are genuinely secret.

    It is idempotent. Run it again after a `git pull`, after a reboot, or after
    a DHCP lease moves this machine to a new address, and it does the right
    thing with no hand-editing.

        powershell -ExecutionPolicy Bypass -File scripts\run-campus.ps1

    What it handles for you
      * creates backend\venv and installs requirements when they are missing
      * creates backend\.env from the campus template on first run, then asks
        once for the values that cannot be derived (SECRET_KEY, DATABASE_URL, R2)
      * detects this machine's LAN address on every run and serves on it. The
        addresses go through the environment, which python-dotenv lets win over
        .env, so a changed IP never means editing a file
      * pins RUN_MIGRATIONS=false so this half can never migrate the shared
        database — Railway owns the schema
      * rebuilds the React bundle only when the sources are newer than the
        last build, and reinstalls node modules only when the lockfile moved
      * pre-compresses that bundle to .gz/.br. That is the only thing that
        makes WhiteNoise serve it compressed - there is no edge proxy on
        this half to do it the way Railway's does
      * pings the cameras actually registered in the database

    Flags
      -Port <n>        serve on a different port (default 8000)
      -SkipFrontend    never rebuild, even if the sources changed
      -Rebuild         force a rebuild even if the bundle looks current
      -Reconfigure     re-ask for the secret values and rewrite them into .env
      -NonInteractive  never prompt; fail instead. This is how the desktop
                       launcher runs it - a Read-Host with no console attached
                       would hang forever with nothing on screen to say why.

    NOTE: scripts\campus-launcher.ps1 reads this script's output to drive its
    status pills - "Serving on", "reachable :", "NO ROUTE  :", "no cameras
    registered yet" and "could not read the camera list" are all matched there.
    Reword one and reword the pattern in Read-ServerLine with it.
#>

[CmdletBinding()]
param(
    [int]$Port = 8000,
    [switch]$SkipFrontend,
    [switch]$Rebuild,
    [switch]$Reconfigure,
    [switch]$NonInteractive
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

# The secret list and the .env read/write helpers are shared with the launcher
# and the installer, so they live in one file rather than being copied into each.
. (Join-Path $PSScriptRoot 'campus-config.ps1')

function Say($msg, $colour = 'Cyan') { Write-Host "[campus] $msg" -ForegroundColor $colour }

# ── 0. Prerequisites ─────────────────────────────────────────────────────────
# The installer installs Git, Python and Node before the first run, so normally
# every one of these is already here and this loop is three cheap PATH lookups.
# It exists for the machines the installer cannot speak for: one where winget
# was unavailable at install time, where the prerequisite component was
# deselected, where someone uninstalled Node later, or a plain `git clone` with
# no installer involved at all. Without this the failure surfaces much further
# down as a bare "npm is not recognized", which names the symptom and not the
# cause.
#
# Not fatal when it cannot fix things: it says exactly what is missing and what
# to run, then carries on so the steps that do not need that tool still happen.
function Install-MissingTool {
    param([string]$Exe, [string]$WingetId, [string]$Human)

    if (Get-Command $Exe -ErrorAction SilentlyContinue) { return $true }

    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) {
        Say "$Human is not installed and winget is unavailable - install $Human by hand." 'Yellow'
        return $false
    }

    Say "$Human is not installed - installing it now (one time)..." 'Yellow'

    # EAP guard: winget writes progress to stderr, and with EAP=Stop a native
    # command's stderr becomes a terminating NativeCommandError - the install
    # would be killed by its own progress output.
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $winget.Source install --id $WingetId -e --source winget `
            --accept-package-agreements --accept-source-agreements --silent 2>&1 |
            ForEach-Object { Say "  $_" 'DarkGray' }
    } catch {
        Say "  $($_.Exception.Message)" 'DarkGray'
    } finally { $ErrorActionPreference = $prevEAP }

    # winget puts new tools on the machine PATH, which this already-running
    # process does not see. Rebuild it from the registry rather than telling
    # someone to reboot before the app will start.
    $env:PATH = ([Environment]::GetEnvironmentVariable('PATH', 'Machine') + ';' +
                 [Environment]::GetEnvironmentVariable('PATH', 'User'))

    if (Get-Command $Exe -ErrorAction SilentlyContinue) {
        Say "$Human installed." 'Green'
        return $true
    }
    Say ("$Human could not be installed automatically. Run the installer again as " +
         "administrator, or install $Human by hand, then start the server.") 'Yellow'
    return $false
}

[void](Install-MissingTool -Exe 'git.exe'  -WingetId 'Git.Git'           -Human 'Git')
[void](Install-MissingTool -Exe 'py.exe'   -WingetId 'Python.Python.3.12' -Human 'Python 3.12')
[void](Install-MissingTool -Exe 'node.exe' -WingetId 'OpenJS.NodeJS.LTS'  -Human 'Node.js')

# ── 1. Python environment ────────────────────────────────────────────────────
$python = Join-Path $repo 'backend\venv\Scripts\python.exe'
$script:FreshVenv = $false
if (-not (Test-Path $python)) {
    Say 'No virtualenv found - creating backend\venv (one-time, a few minutes)...'

    # 3.12 first, and that is not arbitrary. requirements.txt pins packages that
    # are 3.12-only - tifffile==2026.5.15 declares requires_python >=3.12 - so a
    # 3.11 virtualenv cannot resolve them at all. pip reports that as "no
    # matching distribution", listing only the older versions 3.11 can take,
    # which reads like a bad pin rather than a wrong interpreter.
    #
    # The README says "use 3.11 specifically" for EasyOCR and OpenCV. That note
    # is older than these pins; the environment this project actually runs on is
    # 3.12. If 3.11 ever becomes right again, the pins have to move with it.
    $created = $false
    if (Get-Command py.exe -ErrorAction SilentlyContinue) {
        foreach ($want in @('-3.12', '-3.11')) {
            & py $want -m venv (Join-Path $repo 'backend\venv') 2>$null
            if (Test-Path $python) { $created = $true; break }
        }
    }
    if (-not $created) {
        Say 'No suitable Python found via the launcher - falling back to the default python.' 'Yellow'
        & python -m venv (Join-Path $repo 'backend\venv')
    }
    if (-not (Test-Path $python)) {
        Write-Error 'Could not create the virtualenv. Is Python installed and on PATH?'
    }

    $ver = (& $python -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null)
    if ($ver -and $ver.Trim() -ne '3.12') {
        Say "WARNING: the virtualenv is on Python $($ver.Trim()); requirements.txt needs 3.12." 'Yellow'
    }
    $script:FreshVenv = $true
}

# Packages, whenever requirements.txt is newer than the last successful install.
#
# This used to sit inside the "no virtualenv" branch above, so pip ran exactly
# once in the life of a machine. The update button pulls whatever landed on
# main, and a commit that adds a dependency - requirements.txt has changed
# several times already - left every installed machine with a virtualenv that
# no longer matched it. The next start then died on ModuleNotFoundError for a
# package nobody had been asked to install.
#
# Same stamp-file approach the frontend uses for package-lock.json below, and
# for the same reason: a full pip run every start would add a minute to every
# start, while comparing two timestamps costs nothing. The stamp is written
# only after pip succeeds, so a failed install is retried on the next start
# rather than being remembered as done.
$reqFile  = Join-Path $repo 'requirements.txt'
$reqStamp = Join-Path $repo 'backend\venv\.requirements-stamp'
$needPip  = $script:FreshVenv -or (-not (Test-Path $reqStamp))
if (-not $needPip -and (Test-Path $reqFile)) {
    $needPip = (Get-Item $reqFile).LastWriteTime -gt (Get-Item $reqStamp).LastWriteTime
}

if ($needPip) {
    if (-not $script:FreshVenv) {
        Say 'requirements.txt changed since the last run - updating dependencies...' 'Yellow'
    }
    & $python -m pip install --upgrade pip --quiet
    & $python -m pip install -r $reqFile

    # pip's exit code, checked. Without this the script announced "Dependencies
    # installed." whether or not pip had just failed, and the first sign of
    # trouble was Django throwing an import traceback two steps later - which
    # points at the wrong thing entirely. A half-built environment is not
    # something to carry on from.
    if ($LASTEXITCODE -ne 0) {
        Write-Error ("pip failed (exit $LASTEXITCODE). The virtualenv is incomplete, so the server " +
                     "cannot start. The pip output above says which package could not be installed - " +
                     "a 'no matching distribution' there usually means the virtualenv is on the wrong " +
                     "Python version for the pins in requirements.txt.")
    }
    Set-Content -Path $reqStamp -Value (Get-Date -Format 'o') -Encoding utf8
    Say 'Dependencies installed.' 'Green'
}

# ── 1a. FFmpeg — every camera in the system depends on it ────────────────────
# vehicles/ffmpeg_capture.py reads ALL RTSP through an ffmpeg subprocess. With
# no ffmpeg it logs "no ffmpeg binary available" once and every feed stays
# black - the server still starts and serves, so this looks like broken cameras
# rather than a missing program, and nobody thinks to go looking for a codec.
#
# The installer now installs it as a prerequisite, but that only helps machines
# installed after this change. This check also covers the ones it cannot: a box
# where winget was unavailable, where the component was deselected, where
# someone removed ffmpeg later, or a plain `git clone` with no installer at all.
#
# imageio-ffmpeg rather than winget as the repair: it is a pip install into the
# virtualenv that already exists, so it needs no administrator, no UAC prompt
# from a background script, and no PATH refresh to take effect. A system ffmpeg
# is still preferred when present - ffmpeg_binary() checks PATH first.
$haveFfmpeg = [bool](Get-Command ffmpeg.exe -ErrorAction SilentlyContinue)
if (-not $haveFfmpeg) {
    # Same order ffmpeg_binary() uses, so this agrees with what the app will do.
    foreach ($p in @("$env:LOCALAPPDATA\Microsoft\WinGet\Links\ffmpeg.exe",
                     "$env:ProgramFiles\ffmpeg\bin\ffmpeg.exe",
                     "$env:ProgramData\chocolatey\bin\ffmpeg.exe")) {
        if (Test-Path $p) {
            # Found but not on PATH - shutil.which() would miss it too, so put
            # its folder on PATH for the server we are about to start.
            $env:PATH = (Split-Path -Parent $p) + ';' + $env:PATH
            $haveFfmpeg = $true
            Say "Found ffmpeg at $p - added to PATH for this run."
            break
        }
    }
}
if (-not $haveFfmpeg) {
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try   { $bundled = (& $python -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())" 2>&1 | Select-Object -Last 1) }
    catch { $bundled = '' }
    finally { $ErrorActionPreference = $prevEAP }

    if ("$bundled" -notmatch 'Error|Traceback' -and "$bundled".Trim() -and (Test-Path "$bundled".Trim())) {
        Say 'ffmpeg is not installed system-wide, but the bundled build is present.' 'Yellow'
        $haveFfmpeg = $true
    } else {
        Say 'No ffmpeg on this machine - cameras cannot be read. Installing it now (about 30 MB, one time)...' 'Yellow'
        & $python -m pip install --quiet imageio-ffmpeg
        if ($LASTEXITCODE -eq 0) {
            Say 'ffmpeg installed. Cameras can be read.' 'Green'
            $haveFfmpeg = $true
        } else {
            # Not fatal. Everything except the camera feeds still works, and a
            # server that starts and says why is more use than one that refuses.
            Say ('Could not install ffmpeg automatically. The system will run, but camera feeds ' +
                 'will stay black until ffmpeg is installed (winget install Gyan.FFmpeg).') 'Yellow'
        }
    }
}

# ── 1b. CUDA wheels, when there is a card to use them ────────────────────────
# requirements.txt pins plain `torch==2.12.0`, which resolves to the CPU wheels.
# That is correct for Railway, which has no GPU, and wrong for a gate terminal
# with an RTX in it: the plate and vehicle detectors then run on the CPU and the
# feed visibly lags. The note in requirements.txt describes the swap; nothing
# was doing it, so every installed machine was CPU-bound.
#
# Guarded by a marker rather than re-checked every start: importing torch costs
# a few seconds, and this only needs deciding once per virtualenv.
$gpuMarker = Join-Path $repo 'backend\venv\.gpu-checked'
if (-not (Test-Path $gpuMarker)) {
    $nvidia = @(Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -match 'NVIDIA' })
    if ($nvidia.Count -eq 0) {
        Say 'No NVIDIA GPU found - detection will run on the CPU.' 'Yellow'
    } else {
        Say "Found $($nvidia[0].Name) - checking whether torch can use it..."

        # The probe is guarded because `import torch` does not merely return
        # False on a broken install - it raises. A pip run interrupted partway
        # leaves site-packages\torch without torch.dll, and the import dies with
        # "WinError 126: The specified module could not be found". Treating that
        # as "no CUDA" is right: the reinstall below rewrites the package and
        # repairs it as a side effect.
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try   { $cuda = (& $python -c "import torch; print(torch.cuda.is_available())" 2>&1 | Select-Object -Last 1) }
        catch { $cuda = '' }
        finally { $ErrorActionPreference = $prevEAP }

        if ("$cuda" -match 'WinError|Traceback|Error loading') {
            Say 'torch is present but will not load - the install is incomplete. Reinstalling it.' 'Yellow'
            $cuda = ''
        }
        if ("$cuda".Trim() -eq 'True') {
            Say 'torch already has CUDA. Detection will run on the GPU.' 'Green'
        } else {
            Say 'torch is the CPU build - installing the CUDA wheels (about 2 GB, one time)...' 'Yellow'
            & $python -m pip install --force-reinstall --no-deps `
                torch==2.12.0 torchvision==0.27.0 `
                --index-url https://download.pytorch.org/whl/cu130
            if ($LASTEXITCODE -ne 0) {
                # Not fatal: the CPU build still works, just slower. Saying so is
                # better than failing a machine that would otherwise serve.
                Say 'CUDA install failed - carrying on with the CPU build. Detection will be slower.' 'Yellow'
            } else {
                $prevEAP = $ErrorActionPreference
                $ErrorActionPreference = 'Continue'
                try   { $cuda = (& $python -c "import torch; print(torch.cuda.is_available())" 2>&1 | Select-Object -Last 1) }
                catch { $cuda = '' }
                finally { $ErrorActionPreference = $prevEAP }

                if ("$cuda".Trim() -eq 'True') { Say 'CUDA is available. Detection will run on the GPU.' 'Green' }
                else { Say "torch still will not report CUDA ($cuda) - check the NVIDIA driver." 'Yellow' }
            }
        }
    }
    New-Item -ItemType File -Path $gpuMarker -Force | Out-Null
}

# ── 2. .env — created from the template, secrets asked for once ──────────────
$envFile  = Join-Path $repo 'backend\.env'
$template = Join-Path $repo 'backend\.env.campus.example'

if (-not (Test-Path $envFile)) {
    if (-not (Test-Path $template)) { Write-Error "Missing $template - cannot bootstrap .env." }
    Copy-Item $template $envFile
    Say 'Created backend\.env from the campus template.' 'Green'
    $Reconfigure = $true
}

# Values this machine cannot derive - defined once in campus-config.ps1 so the
# launcher and the installer ask for exactly the same set. SECRET_KEY and
# DATABASE_URL must match Railway: the key signs the JWTs both halves accept,
# and the database string is what makes the two halves one system, not two.
$secrets = Get-CampusSecretSpec
$missing = Get-CampusMissingSecrets -EnvFile $envFile -RequiredOnly

if ($NonInteractive) {
    # Prompting with no console attached is an invisible hang, so say what is
    # wrong and let the caller show it.
    if ($missing.Count -gt 0) {
        Write-Error ("Not configured: " + (($missing | ForEach-Object { $_.Key }) -join ', ') +
                     " missing from backend\.env. Run without -NonInteractive, or set them in the launcher.")
    }
} elseif ($Reconfigure -or $missing.Count -gt 0) {
    Say 'Values needed before this half can join the shared system.' 'Yellow'
    Write-Host '        Press Enter to keep what is already in .env.' -ForegroundColor DarkGray
    foreach ($s in $secrets) {
        $current = Get-CampusEnvValue $envFile $s.Key
        if (Test-CampusPlaceholder $current) { $shown = '(not set)' }
        elseif ($s.Hidden)                   { $shown = '(set - hidden)' }
        else                                 { $shown = $current }
        $entered = Read-Host "  $($s.Prompt) [$shown]"
        if ($entered -and $entered.Trim()) {
            Set-CampusEnvValue -EnvFile $envFile -Key $s.Key -Value $entered.Trim()
        }
    }
    Say 'Saved to backend\.env.' 'Green'
}

# ── 3. This machine's address, recomputed every run ──────────────────────────
# Passed through the environment rather than written into .env: python-dotenv
# does not override variables already set in the process, so these win. A new
# DHCP lease therefore needs no file edit at all.
$lan = Get-CampusLanAddress
if ($lan -eq '127.0.0.1') { Say 'No LAN address found - serving on localhost only.' 'Yellow' }
$origin = Set-CampusRuntimeEnvironment -Lan $lan -Port $Port

# ── 4. Camera reachability, read from the database ───────────────────────────
# These addresses used to be hardcoded here and drifted out of date, so the
# check reported NO ROUTE for cameras that were not the configured ones.
#
# Via a management command, not `python -c "..."`: PowerShell strips the quotes
# out of a multi-line string when handing it to a native exe, and the inline
# version died with a SyntaxError. Errors are shown rather than swallowed —
# hiding them is what let that failure masquerade as "no cameras registered".
Say 'Checking the cameras registered in the database...'
Push-Location (Join-Path $repo 'backend')
# ErrorActionPreference is dropped to Continue for exactly this call. With it
# at Stop, `2>&1` on a NATIVE command turns every stderr line into a
# terminating ErrorRecord - so a Django warning, or a database it cannot reach,
# killed this whole script at line 151 with a NativeCommandError instead of
# falling through to the "could not read the camera list" branch three lines
# below that exists to handle precisely that. The server then never started.
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    $camOut = & $python manage.py camera_hosts 2>&1
    $camOk  = ($LASTEXITCODE -eq 0)
} catch {
    $camOut = @($_.Exception.Message)
    $camOk  = $false
} finally {
    $ErrorActionPreference = $prevEAP
}
Pop-Location

$cams = @()
if ($camOk) {
    $cams = @($camOut | Where-Object { $_ -match "`t" })
} else {
    Write-Host '  could not read the camera list:' -ForegroundColor Yellow
    $camOut | Select-Object -Last 3 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
}

if ($cams.Count -eq 0 -and $camOk) {
    Write-Host '  no cameras registered yet - add them in Device Management' -ForegroundColor DarkGray
} elseif ($cams.Count -gt 0) {
    foreach ($row in $cams) {
        $name, $ip = $row -split "`t", 2
        if (Test-Connection -ComputerName $ip -Count 1 -Quiet -ErrorAction SilentlyContinue) {
            Write-Host "  reachable : $name ($ip)" -ForegroundColor Green
        } else {
            Write-Host "  NO ROUTE  : $name ($ip) - live scanning will not work from this machine" -ForegroundColor Yellow
        }
    }
}

# ── 5. Frontend bundle, rebuilt only when stale ──────────────────────────────
$buildDir  = Join-Path $repo 'backend\frontend_build'
$needBuild = $false
if (-not $SkipFrontend) {
    if ($Rebuild -or -not (Test-Path $buildDir)) {
        $needBuild = $true
    } else {
        $built = (Get-Item $buildDir).LastWriteTime
        $newest = Get-ChildItem (Join-Path $repo 'frontend\src'), (Join-Path $repo 'frontend\package.json') `
                      -Recurse -File -ErrorAction SilentlyContinue |
                  Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($newest -and $newest.LastWriteTime -gt $built) { $needBuild = $true }
    }
}

if ($needBuild) {
    # `npm ci` wipes and reinstalls node_modules — the slow part. Only when the
    # lockfile actually moved.
    $stamp = Join-Path $repo 'frontend\node_modules\.campus-lock-stamp'
    $lock  = Join-Path $repo 'frontend\package-lock.json'
    $needInstall = (-not (Test-Path (Join-Path $repo 'frontend\node_modules'))) -or (-not (Test-Path $stamp))
    if (-not $needInstall -and (Test-Path $lock)) {
        $needInstall = (Get-Item $lock).LastWriteTime -gt (Get-Item $stamp).LastWriteTime
    }
    if ($needInstall) {
        Say 'Installing frontend dependencies (lockfile changed)...'
        npm --prefix frontend ci
        New-Item -ItemType File -Path $stamp -Force | Out-Null
    }

    Say 'Building the React bundle...'
    npm --prefix frontend run build
    # Vite empties frontend\dist before it writes, so a build that fails partway
    # leaves a partial bundle behind. Swapping that in loses chunks, and a page
    # whose chunk went missing comes up blank with nothing in the console to
    # explain it. Keep the bundle that is already serving unless the new one
    # actually built.
    $distDir = Join-Path $repo 'frontend\dist'
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path (Join-Path $distDir 'index.html'))) {
        if (Test-Path (Join-Path $buildDir 'index.html')) {
            Say 'Frontend build FAILED - keeping the previous bundle. Fix the build, then re-run with -Rebuild.' 'Red'
        } else {
            Say 'Frontend build FAILED and there is no previous bundle to fall back on.' 'Red'
            exit 1
        }
    } else {
        if (Test-Path $buildDir) { Remove-Item -Recurse -Force $buildDir }
        Copy-Item -Recurse $distDir $buildDir
        Say 'Bundle placed in backend\frontend_build' 'Green'
    }
} elseif (-not $SkipFrontend) {
    Say 'Bundle is current - skipping the rebuild.' 'DarkGray'
}

# ── 5b. Pre-compress the bundle for WhiteNoise ───────────────────────────────
# WhiteNoise never compresses on the fly. It serves a .gz or .br only when one
# is already sitting next to the original on disk, and nothing else here puts
# them there: CompressedStaticFilesStorage runs inside collectstatic, so it
# only ever reaches STATIC_ROOT, while the bundle is served from
# WHITENOISE_ROOT (backend\frontend_build). GZipMiddleware cannot cover for it
# either - it sits BELOW WhiteNoise in MIDDLEWARE, and WhiteNoise answers a
# static request in process_request, so that response is returned without ever
# travelling back down through the gzip layer.
#
# Railway hides this, because its edge proxy gzips on the way out. This half
# has no proxy in front of it. Without this step the gate serves the React
# bundle raw - a measured 2.8 MB of JS+CSS where 830 KB would do, on every
# cold load, over campus wifi.
#
# It runs on the bundle-is-current path too, not just after a rebuild. An
# install that has never been compressed would otherwise stay raw until
# something forced a rebuild, which on a stable gate machine is never.
#
# And it must finish BEFORE daphne starts: WhiteNoise indexes the directory
# once at startup, so a .gz written after that is invisible until the next run.
if (Test-Path $buildDir) {
    $haveGz = @(Get-ChildItem $buildDir -Recurse -File -Filter *.gz -ErrorAction SilentlyContinue)
    if ($needBuild -or $haveGz.Count -eq 0) {
        Say 'Pre-compressing the bundle (gzip + brotli)...'
        # -q because the default is one line per file, which would bury
        # everything else this script prints into the launcher's log pane.
        & $python -m whitenoise.compress -q $buildDir
        if ($LASTEXITCODE -ne 0) {
            # Not fatal, and deliberately so: an uncompressed bundle is slow,
            # not broken. A gate that serves pages slowly beats one that
            # refuses to start. "WARNING" is what the launcher's severity
            # pattern matches on - see $severityPattern in campus-launcher.ps1.
            Say 'WARNING: could not pre-compress the bundle - it will be served uncompressed.' 'Yellow'
        } else {
            $plain = @(Get-ChildItem $buildDir -Recurse -File -Include *.js, *.css)
            $rawKb = [math]::Round((($plain | Measure-Object Length -Sum).Sum) / 1KB)
            $gzKb  = [math]::Round(((@($plain | ForEach-Object {
                         Get-Item ($_.FullName + '.gz') -ErrorAction SilentlyContinue
                     }) | Measure-Object Length -Sum).Sum) / 1KB)
            if ($rawKb -gt 0 -and $gzKb -gt 0) {
                Say "Bundle compressed: $rawKb KB of JS/CSS is $gzKb KB over the wire." 'Green'
            } else {
                Say 'Bundle compressed.' 'Green'
            }
        }
    } else {
        Say 'Bundle is already compressed - skipping.' 'DarkGray'
    }
}

# ── 6. Serve ─────────────────────────────────────────────────────────────────
Set-Location (Join-Path $repo 'backend')
Say 'Collecting static files...'
& $python manage.py collectstatic --noinput --clear | Select-Object -Last 1

# Deliberately no `migrate` — RUN_MIGRATIONS=false above, and Railway owns the
# schema for the shared database.

# The two audiences need different entry points, and handing a guard the plain
# origin sends them to the account login instead of the gate terminal.
Write-Host ''
Say "Serving on $origin" 'Green'
Write-Host ''
Write-Host '  Admin / CDSO / vehicle owners' -ForegroundColor White
Write-Host "    $origin/login" -ForegroundColor Cyan
Write-Host ''
Write-Host '  Guards at the gate' -ForegroundColor White
Write-Host "    $origin/security/guard-login" -ForegroundColor Cyan
Write-Host "    $origin/security/guard-login/main    (pre-selects the gate)" -ForegroundColor DarkGray
Write-Host ''
Say 'Ctrl+C to stop.' 'Green'
Write-Host ''

& $python -m daphne -b 0.0.0.0 -p $Port config.asgi:application

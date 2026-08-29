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

# ── 1. Python environment ────────────────────────────────────────────────────
$python = Join-Path $repo 'backend\venv\Scripts\python.exe'
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
    & $python -m pip install --upgrade pip --quiet
    & $python -m pip install -r (Join-Path $repo 'requirements.txt')

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
    Say 'Dependencies installed.' 'Green'
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

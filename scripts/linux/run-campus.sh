#!/usr/bin/env bash
# run-campus.sh - start the on-site half of the hybrid deployment on Linux.
#
# The Linux port of scripts/run-campus.ps1: the same steps in the same order,
# and the same lines printed ("Serving on", "reachable :", "NO ROUTE  :"), so
# CAMPUS_SETUP_LINUX.md and a log read the same as on Windows. When one of the
# two scripts gains a step, the other needs it too.
#
# Idempotent. Run it again after a `git pull`, a reboot or a new DHCP lease
# and it does the right thing with no hand-editing. The slc-vms systemd
# service (install.sh) runs it with --non-interactive on every boot.
#
#     scripts/linux/run-campus.sh
#
# Flags
#   --port N           serve on a different port (default 8000, or campus.conf)
#   --tls-port N       HTTPS port for camera use from other devices (default
#                      8443; 0 turns HTTPS off)
#   --skip-frontend    never rebuild, even if the sources changed
#   --rebuild          force a rebuild even if the bundle looks current
#   --reconfigure      re-ask for the secret values and rewrite them into .env
#   --non-interactive  never prompt; fail instead. How the service runs it: a
#                      prompt with no terminal would wait forever, unseen.
#   --setup-only       do everything up to serving, then stop. install.sh runs
#                      this once in the terminal, so the secrets are asked for
#                      and the slow first install happens where someone sees it.
set -euo pipefail

# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

SKIP_FRONTEND=0 REBUILD=0 RECONFIGURE=0 NON_INTERACTIVE=0 SETUP_ONLY=0
while [ $# -gt 0 ]; do
    case "$1" in
        --port)            PORT="$2"; shift ;;
        --tls-port)        TLS_PORT="$2"; shift ;;
        --skip-frontend)   SKIP_FRONTEND=1 ;;
        --rebuild)         REBUILD=1 ;;
        --reconfigure)     RECONFIGURE=1 ;;
        --non-interactive) NON_INTERACTIVE=1 ;;
        --setup-only)      SETUP_ONLY=1 ;;
        -h|--help)         awk 'NR > 1 && !/^#/ { exit } NR > 1' "$0"; exit 0 ;;
        *)                 die "Unknown option: $1 (see --help)" ;;
    esac
    shift
done

cd "$SLC_REPO"
# The CUPS tools are parsed below, and only speak English under C.
export LC_ALL=C.UTF-8

# ── 0. Prerequisites ─────────────────────────────────────────────────────────
# install.sh puts all of these on the machine. This only says clearly what is
# missing - installing needs sudo, which a boot-time service does not have -
# and carries on, so the steps that do not need that tool still happen.
for tool in git node npm; do
    command -v "$tool" >/dev/null 2>&1 ||
        warn "$tool is not installed. Run: sudo scripts/linux/install.sh"
done

# ── 1. Python environment ────────────────────────────────────────────────────
# 3.12 first, as on Windows: requirements.txt pins packages that are 3.12-only
# (tifffile declares requires_python >=3.12), and pip reports a wrong
# interpreter as "no matching distribution", which reads like a bad pin.
find_python() {
    local c
    for c in python3.12 python3; do
        if command -v "$c" >/dev/null 2>&1 &&
           "$c" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 12))' 2>/dev/null; then
            echo "$c"; return
        fi
    done
    command -v python3 || true
}

has_nvidia() {
    if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then return 0; fi
    lspci 2>/dev/null | grep -qi 'vga.*nvidia\|3d.*nvidia'
}

FRESH_VENV=0
if [ ! -x "$SLC_PY" ]; then
    say 'No virtualenv found - creating backend/venv (one-time, a few minutes)...'
    sys_py="$(find_python)"
    [ -n "$sys_py" ] || die 'Python 3.12 is not installed. Run: sudo scripts/linux/install.sh'
    # Ubuntu ships venv without ensurepip unless python3.12-venv is installed,
    # and the error it gives then does not say so.
    "$sys_py" -m venv "$SLC_BACKEND/venv" ||
        die 'Could not create the virtualenv. Run: sudo apt install python3.12-venv'
    ver="$("$SLC_PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    [ "$ver" = '3.12' ] || warn "the virtualenv is on Python $ver; requirements.txt needs 3.12."
    FRESH_VENV=1
fi

# Packages, whenever requirements.txt is newer than the last successful
# install. The stamp is written only after pip succeeds, so a failed install
# is retried on the next start rather than remembered as done.
REQ_FILE="$SLC_REPO/requirements.txt"
REQ_STAMP="$SLC_BACKEND/venv/.requirements-stamp"
if [ "$FRESH_VENV" = 1 ] || [ ! -f "$REQ_STAMP" ] || [ "$REQ_FILE" -nt "$REQ_STAMP" ]; then
    [ "$FRESH_VENV" = 1 ] || say 'requirements.txt changed since the last run - updating dependencies...'
    "$SLC_PY" -m pip install --upgrade pip --quiet

    # torch from the right index BEFORE requirements.txt, which then finds it
    # already satisfied. Linux differs from Windows here: PyPI's plain Linux
    # torch drags in ~2.5 GB of CUDA libraries whether or not there is a card,
    # so a machine without one takes the CPU build (as docker/Dockerfile does)
    # and a machine with one takes the CUDA build straight away rather than
    # reinstalling over the CPU one. The pins come from requirements.txt, so
    # there is no second copy here to fall out of step.
    #
    # Run on every dependency update, not only when torch is missing: when a
    # pull bumps the torch pin, the old torch still imports, and skipping this
    # would let the plain `-r` below fetch the new pin from PyPI - CUDA
    # libraries and all. When the pins already match, pip does nothing.
    mapfile -t torch_pins < <(grep -E '^torch(vision)?==' "$REQ_FILE")
    if [ "${#torch_pins[@]}" -gt 0 ]; then
        if has_nvidia; then
            index='https://download.pytorch.org/whl/cu130'
            "$SLC_PY" -c 'import torch' >/dev/null 2>&1 ||
                say 'NVIDIA GPU found - installing the CUDA build of torch (about 3 GB, one time)...'
        else
            index='https://download.pytorch.org/whl/cpu'
            "$SLC_PY" -c 'import torch' >/dev/null 2>&1 ||
                say 'No NVIDIA GPU found - installing the CPU build of torch. Detection will run on the CPU.'
        fi
        "$SLC_PY" -m pip install "${torch_pins[@]}" --index-url "$index" ||
            warn 'torch could not be installed from its index - pip will try the default one next.'
    fi

    # PaddlePaddle is not in requirements.txt (the pin differs per platform),
    # so each builder installs it. Same version as Docker, Railpack and
    # run-campus.ps1. Not fatal: detection, QR and manual entry still work.
    "$SLC_PY" -m pip install paddlepaddle==3.0.0 ||
        say 'paddlepaddle install failed - OCR is disabled: plates will be detected but not read.'

    "$SLC_PY" -m pip install -r "$REQ_FILE" ||
        die "pip failed. The virtualenv is incomplete, so the server cannot start. The pip output
         above says which package could not be installed - a 'no matching distribution' there
         usually means the virtualenv is on the wrong Python version for the pins."
    date -Iseconds > "$REQ_STAMP"
    ok 'Dependencies installed.'
fi

# ── 1a. FFmpeg - every camera in the system depends on it ────────────────────
# vehicles/ffmpeg_capture.py reads ALL RTSP through ffmpeg. Without it the
# server still serves and every feed stays black, which looks like broken
# cameras. A system ffmpeg (apt) is preferred; requirements.txt pins
# imageio-ffmpeg, whose bundled build ffmpeg_binary() falls back to, so there
# is nothing to install here - only something to say when even that is broken.
if ! command -v ffmpeg >/dev/null 2>&1; then
    if "$SLC_PY" -c 'import imageio_ffmpeg, os; assert os.path.exists(imageio_ffmpeg.get_ffmpeg_exe())' 2>/dev/null; then
        say 'ffmpeg is not installed system-wide, but the bundled build is present.'
    else
        warn 'No ffmpeg on this machine - camera feeds will stay black until it is installed (sudo apt install ffmpeg).'
    fi
fi

# ── 1b. CUDA, when there is a card to use it ─────────────────────────────────
# Checked once per virtualenv (importing torch costs seconds): a card that
# torch cannot use means a driver problem, and saying so beats a feed that
# just lags.
GPU_MARKER="$SLC_BACKEND/venv/.gpu-checked"
if [ ! -f "$GPU_MARKER" ]; then
    if has_nvidia; then
        cuda="$("$SLC_PY" -c 'import torch; print(torch.cuda.is_available())' 2>&1 | tail -n1 || true)"
        if [ "$cuda" = 'True' ]; then
            ok 'CUDA is available. Detection will run on the GPU.'
        else
            say "torch will not report CUDA ($cuda) - check the NVIDIA driver (nvidia-smi). Detection will run on the CPU."
        fi
    fi
    touch "$GPU_MARKER"
fi

# ── 2. .env - created from the template, secrets asked for once ──────────────
ENV_FILE="$SLC_BACKEND/.env"
TEMPLATE="$SLC_BACKEND/.env.campus.example"
CAMPUS_ENV=("$SLC_PY" "$SLC_REPO/scripts/campus-env.py" "$ENV_FILE")

if [ ! -f "$ENV_FILE" ]; then
    [ -f "$TEMPLATE" ] || die "Missing $TEMPLATE - cannot bootstrap .env."
    cp "$TEMPLATE" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    ok 'Created backend/.env from the campus template.'
    RECONFIGURE=1
fi

# The values a machine cannot derive. SECRET_KEY and DATABASE_URL must match
# Railway: the key signs the JWTs both halves accept, and the database string
# is what makes the two halves one system, not two.
missing="$("${CAMPUS_ENV[@]}" missing --required-only || true)"
if [ "$NON_INTERACTIVE" = 1 ] || [ ! -t 0 ]; then
    [ -z "$missing" ] || die "Not configured: $(echo "$missing" | paste -sd, -) missing from backend/.env.
         Run scripts/linux/run-campus.sh once in a terminal, or with --reconfigure."
elif [ "$RECONFIGURE" = 1 ] || [ -n "$missing" ]; then
    say 'Values needed before this half can join the shared system.'
    "${CAMPUS_ENV[@]}" prompt
    ok 'Saved to backend/.env.'
fi

# ── 3. This machine's address, recomputed every run ──────────────────────────
LAN="$(lan_address)"
[ "$LAN" != '127.0.0.1' ] || say 'No LAN address found - serving on localhost only.'

# ── 3b. HTTPS for the camera on other devices ────────────────────────────────
# Browsers give a page the camera only on https:// or http://localhost, so
# another gate PC or a phone opening http://<ip>:8000 can never scan. A
# self-signed certificate for this LAN address, and daphne serves https on a
# second port. Never fatal: the plain port still serves everything else, and
# the port is turned OFF rather than left advertising a link that goes nowhere.
TLS_ARGS=()
if [ "$TLS_PORT" -gt 0 ]; then
    tls_dir="$SLC_DATA_DIR/tls"
    # Checks the behaviour, not that pyOpenSSL imports: an incompatible pair
    # listens and then drops every handshake (campus-tls-check.py).
    tls_libs="$("$SLC_PY" "$SLC_REPO/scripts/campus-tls-check.py" 2>&1 | tail -n1 || true)"
    if [ "$tls_libs" != 'ok' ]; then
        warn "HTTPS is off - $tls_libs. Other devices will not get the camera."
        TLS_PORT=0
    elif made="$("$SLC_PY" "$SLC_REPO/scripts/campus-tls-cert.py" "$tls_dir" "$LAN" 2>&1 | tail -n1)"; then
        # Twisted endpoint strings treat ':' and '\' as syntax.
        esc() { local p="${1//\\/\\\\}"; printf '%s' "${p//:/\\:}"; }
        TLS_ARGS=(-e "ssl:${TLS_PORT}:privateKey=$(esc "$tls_dir/key.pem"):certKey=$(esc "$tls_dir/cert.pem")")
        note "HTTPS certificate $made for $LAN."
    else
        warn "HTTPS is off - $made. Other devices will not get the camera."
        TLS_PORT=0
    fi
fi

# The environment handed to Django - Set-CampusRuntimeEnvironment on Windows.
# Through the environment, not .env: python-dotenv does not override variables
# already set, so a new DHCP lease never means editing a file.
ORIGIN="http://${LAN}:${PORT}"
export ALLOWED_HOSTS="localhost,127.0.0.1,$LAN"
export FRONTEND_URL="$ORIGIN" BACKEND_URL="$ORIGIN" CSRF_TRUSTED_ORIGINS="$ORIGIN"
export SECURE_SSL_REDIRECT=false                 # plain HTTP on the LAN; the redirect would loop
if [ "$TLS_PORT" -gt 0 ]; then
    # The plain port keeps serving too, so HSTS must stay off.
    export CSRF_TRUSTED_ORIGINS="$ORIGIN,https://${LAN}:${TLS_PORT}"
    export CAMPUS_HTTPS_PORT="$TLS_PORT" SECURE_HSTS_SECONDS=0
else
    export CAMPUS_HTTPS_PORT=''
fi
export RUN_MIGRATIONS=false                      # Railway owns the schema for the shared DB
export PYTHONUNBUFFERED=1

# ── 4. Camera reachability, read from the database ───────────────────────────
say 'Checking the cameras registered in the database...'
bash "$SLC_REPO/scripts/linux/check-cameras.sh"

# ── 4b. Thermal slip printer ─────────────────────────────────────────────────
# Visitor slips print from the server straight to the printer, with no
# dialog (scanning/slip_printer.py). Checked on each start because a printer
# that jammed or was unplugged leaves its CUPS queue disabled. Never fatal.
check_slip_printer() {
    local wanted line queue uri
    wanted="$("${CAMPUS_ENV[@]}" get SLIP_PRINTER)"
    if [[ "${wanted,,}" =~ $SLC_PRINTER_OFF_RE ]]; then
        note 'Slip printer: off (SLIP_PRINTER) - slips print through the browser.'; return
    fi
    if [[ "$wanted" == /* ]]; then
        if [ -w "$wanted" ]; then ok "Slip printer: $wanted"
        else warn "Slip printer $wanted is missing or not writable - is it plugged in, and is this user in the lp group?"; fi
        return
    fi
    if ! command -v lpstat >/dev/null 2>&1; then
        note 'CUPS is not installed - slips print through the browser dialog.'; return
    fi
    if ! lpstat -r 2>/dev/null | grep -q 'scheduler is running'; then
        warn 'The CUPS print service is not running - slips cannot print (sudo systemctl start cups).'; return
    fi
    if [ -n "$wanted" ]; then
        line="$(lpstat -v 2>/dev/null | grep -i "^device for ${wanted}:" || true)"
    else
        line="$(lpstat -v 2>/dev/null | grep -Ei "$SLC_THERMAL_RE" | head -n1 || true)"
    fi
    if [ -z "$line" ] && [ -n "$wanted" ]; then
        warn "SLIP_PRINTER=$wanted in backend/.env, but CUPS has no queue by that name - slips print through the browser. Check: lpstat -v"
        return
    elif [ -z "$line" ]; then
        note 'No thermal slip printer set up - slips print through the browser. To add one: sudo scripts/linux/slip-printer-setup.sh'
        return
    fi
    queue="$(sed -E 's/^device for ([^:]+):.*/\1/' <<<"$line")"
    uri="$(sed -E 's/^device for [^:]+: *//' <<<"$line")"
    if lpstat -p "$queue" 2>/dev/null | grep -q disabled; then
        # A jam or an unplug stops the queue, and CUPS never restarts it.
        if cupsenable "$queue" 2>/dev/null; then
            ok "Slip printer $queue was stopped - restarted it."
        else
            warn "Slip printer $queue is stopped. Run: cupsenable $queue"
            return
        fi
    fi
    ok "Slip printer: $queue ($uri)"
}
check_slip_printer || warn 'Slip printer check failed.'

# ── 5. Frontend bundle, rebuilt only when stale ──────────────────────────────
BUILD_DIR="$SLC_BACKEND/frontend_build"
FRONTEND="$SLC_REPO/frontend"
need_build=0
if [ "$SKIP_FRONTEND" = 0 ]; then
    if [ "$REBUILD" = 1 ] || [ ! -d "$BUILD_DIR" ]; then
        need_build=1
    elif [ -n "$(find "$FRONTEND/src" "$FRONTEND/package.json" -newer "$BUILD_DIR" -print -quit 2>/dev/null)" ]; then
        need_build=1
    fi
fi

if [ "$need_build" = 1 ]; then
    # `npm ci` wipes and reinstalls node_modules - the slow part. Only when
    # the lockfile actually moved.
    stamp="$FRONTEND/node_modules/.campus-lock-stamp"
    if [ ! -d "$FRONTEND/node_modules" ] || [ ! -f "$stamp" ] || [ "$FRONTEND/package-lock.json" -nt "$stamp" ]; then
        say 'Installing frontend dependencies (lockfile changed)...'
        npm --prefix "$FRONTEND" ci && touch "$stamp"
    fi

    say 'Building the React bundle...'
    # Vite empties dist before it writes, so a failed build leaves a partial
    # bundle. Keep the one already serving unless the new one actually built.
    if npm --prefix "$FRONTEND" run build && [ -f "$FRONTEND/dist/index.html" ]; then
        rm -rf "$BUILD_DIR"
        cp -r "$FRONTEND/dist" "$BUILD_DIR"
        ok 'Bundle placed in backend/frontend_build'
    elif [ -f "$BUILD_DIR/index.html" ]; then
        printf '%s\n' '[campus] Frontend build FAILED - keeping the previous bundle. Fix the build, then re-run with --rebuild.' >&2
    else
        die 'Frontend build FAILED and there is no previous bundle to fall back on.'
    fi
elif [ "$SKIP_FRONTEND" = 0 ]; then
    note 'Bundle is current - skipping the rebuild.'
fi

# ── 5b. Pre-compress the bundle for WhiteNoise ───────────────────────────────
# WhiteNoise serves a .gz/.br only when one already sits next to the file, and
# there is no proxy in front of this half to compress on the way out. Must
# finish BEFORE daphne starts: WhiteNoise indexes the folder once at startup.
if [ -d "$BUILD_DIR" ]; then
    if [ "$need_build" = 1 ] || [ -z "$(find "$BUILD_DIR" -name '*.gz' -print -quit)" ]; then
        say 'Pre-compressing the bundle (gzip + brotli)...'
        if "$SLC_PY" -m whitenoise.compress -q "$BUILD_DIR"; then
            kb() { find "$BUILD_DIR" -type f \( -name "*.js$1" -o -name "*.css$1" \) -printf '%s\n' |
                   awk '{ s += $1 } END { print int(s / 1024) }'; }
            ok "Bundle compressed: $(kb '') KB of JS/CSS is $(kb .gz) KB over the wire."
        else
            warn 'could not pre-compress the bundle - it will be served uncompressed.'
        fi
    else
        note 'Bundle is already compressed - skipping.'
    fi
fi

# ── 6. Serve ─────────────────────────────────────────────────────────────────
cd "$SLC_BACKEND"
say 'Collecting static files...'
"$SLC_PY" manage.py collectstatic --noinput --clear | tail -n1

if [ "$SETUP_ONLY" = 1 ]; then
    ok 'Setup complete. The server is ready to start.'
    exit 0
fi

# Deliberately no `migrate` - RUN_MIGRATIONS=false above, and Railway owns the
# schema for the shared database.
echo
ok "Serving on $ORIGIN"
echo
echo '  Admin / CDSO / vehicle owners'
echo "    $ORIGIN/login"
echo
echo '  Guards at the gate'
echo "    $ORIGIN/security/guard-login"
echo "    $ORIGIN/security/guard-login/main    (pre-selects the gate)"
if [ "$TLS_PORT" -gt 0 ]; then
    echo
    echo '  Guards on another PC or a phone (camera QR scanning needs https)'
    echo "    https://${LAN}:${TLS_PORT}/security/guard-login"
    echo '    First visit shows a certificate warning: Advanced -> Proceed. Once per browser.'
fi
echo
ok 'Ctrl+C to stop.'
echo

# exec: daphne replaces this shell, so systemd's stop signal reaches it directly.
exec "$SLC_PY" -m daphne -b 0.0.0.0 -p "$PORT" "${TLS_ARGS[@]}" config.asgi:application

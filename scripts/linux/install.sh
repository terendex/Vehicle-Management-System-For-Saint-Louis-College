#!/usr/bin/env bash
# install.sh - set an Ubuntu 24.04 PC up as the campus server, once. Run it
# with sudo from the cloned repo, as the desktop user the server will run as:
#
#     sudo scripts/linux/install.sh --open guard
#
# The Linux counterpart of the Windows installer (installer/bootstrap.ps1).
# Safe to run again: every step checks before it changes anything.
#
#   1. the system packages: Git, Python 3.12, ffmpeg, CUPS, and the libraries
#      OpenCV and torch load; Node 22 from NodeSource (Ubuntu's own Node is
#      too old for Vite)
#   2. the server's user into the lp and lpadmin groups, for the slip printer
#   3. campus.conf, this machine's settings (~/.config/slc-vms/)
#   4. the firewall (ufw, when it is on): the web port and the HTTPS port
#   5. the first start, in this terminal: asks for the secrets, builds the
#      virtualenv and the web bundle (a long wait, once)
#   6. the slip printer, when one is plugged in
#   7. the slc-vms service: starts the server on boot, restarts it on a crash
#   8. the kiosk: opens the gate page full screen at desktop login
#
# Options
#   --user NAME      the account the server runs as (default: whoever ran sudo)
#   --port N         web port (default 8000)
#   --tls-port N     HTTPS port for webcams on other devices (default 8443, 0 = off)
#   --branch NAME    the branch update.sh follows (default main)
#   --open WHICH     page to open full screen at login: guard, admin or none
#   --no-kiosk       open that page in a normal window instead
#   --kiosk          full screen again (the default; undoes an earlier --no-kiosk)
#   --skip-packages  skip step 1 (no apt, for a machine set up by hand)
set -euo pipefail

RUN_USER="${SUDO_USER:-}"
opt_port='' opt_tls='' opt_branch='' opt_open='' opt_kiosk='' skip_packages=0
while [ $# -gt 0 ]; do
    case "$1" in
        --user)          RUN_USER="$2"; shift ;;
        --port)          opt_port="$2"; shift ;;
        --tls-port)      opt_tls="$2"; shift ;;
        --branch)        opt_branch="$2"; shift ;;
        --open)          opt_open="$2"; shift ;;
        --kiosk)         opt_kiosk=true ;;
        --no-kiosk)      opt_kiosk=false ;;
        --skip-packages) skip_packages=1 ;;
        -h|--help)       awk 'NR > 1 && !/^#/ { exit } NR > 1' "$0"; exit 0 ;;
        *)               echo "Unknown option: $1 (see --help)" >&2; exit 1 ;;
    esac
    shift
done
case "$opt_open" in ''|guard|admin|none) ;; *) echo '--open takes guard, admin or none' >&2; exit 1 ;; esac
for n in "$opt_port" "$opt_tls"; do
    case "$n" in ''|*[!0-9]*) [ -z "$n" ] || { echo "Not a port number: $n" >&2; exit 1; } ;; esac
done
[ "$(id -u)" = 0 ] || { echo 'Run with sudo: sudo scripts/linux/install.sh' >&2; exit 1; }
[ -n "$RUN_USER" ] && [ "$RUN_USER" != root ] ||
    { echo 'Say which account runs the server: sudo scripts/linux/install.sh --user NAME' >&2; exit 1; }
id "$RUN_USER" >/dev/null 2>&1 || { echo "No such user: $RUN_USER" >&2; exit 1; }

# lib.sh reads the settings from the server user's home, not root's.
HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)"
export HOME
unset XDG_CONFIG_HOME XDG_DATA_HOME
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

as_user() { sudo -u "$RUN_USER" -H "$@"; }

# ── 1. System packages ──────────────────────────────────────────────────────
if [ "$skip_packages" = 0 ]; then
    if ! grep -q 'VERSION_ID="24.04"' /etc/os-release 2>/dev/null; then
        warn 'This is not Ubuntu 24.04, which is what this script is written for. Carrying on.'
    fi
    say 'Installing system packages...'
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -q
    # libgl1 + libglib2.0-0: OpenCV. libgomp1: torch and YOLO. pciutils: the
    # GPU check. fonts-liberation: Courier New's metrics for the slip.
    apt-get install -y -q git curl ca-certificates iproute2 iputils-ping pciutils \
        python3.12 python3.12-venv python3.12-dev build-essential \
        ffmpeg libgl1 libglib2.0-0 libgomp1 cups fonts-liberation

    # Vite 8 needs Node 20.19 or newer; Ubuntu 24.04's own nodejs is 18.
    node_ok() {
        command -v node >/dev/null 2>&1 &&
            node -e 'const [a,b]=process.versions.node.split(".").map(Number);process.exit(a>22||(a===22&&b>=12)||(a===20&&b>=19)?0:1)'
    }
    if ! node_ok; then
        say 'Installing Node.js 22 from NodeSource...'
        curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
        apt-get install -y -q nodejs
    fi
    node_ok || die 'Node.js 22 could not be installed. Install it by hand, then run this again.'
    ok "System packages ready (Python $(python3.12 -V | cut -d' ' -f2), Node $(node -v))."
fi

# ── 2. Groups for the slip printer ──────────────────────────────────────────
# lp: write to a USB printer's device file. lpadmin: re-enable the CUPS queue
# after a paper-out, which the server does by itself (slip_printer.py).
for g in lp lpadmin; do
    if getent group "$g" >/dev/null && ! id -nG "$RUN_USER" | tr ' ' '\n' | grep -qx "$g"; then
        usermod -aG "$g" "$RUN_USER"
        say "Added $RUN_USER to the $g group."
    fi
done
systemctl enable --now cups >/dev/null 2>&1 || warn 'Could not start the CUPS print service.'

# ── 3. campus.conf ──────────────────────────────────────────────────────────
# Existing values are kept unless an option above changes them.
PORT="${opt_port:-$PORT}"
TLS_PORT="${opt_tls:-$TLS_PORT}"
BRANCH="${opt_branch:-$BRANCH}"
OPEN_ON_START="${opt_open:-$OPEN_ON_START}"
KIOSK="${opt_kiosk:-$KIOSK}"
as_user mkdir -p "$SLC_CONFIG_DIR"
as_user tee "$SLC_CONFIG" >/dev/null <<EOF
# This machine's campus server settings. Read by scripts/linux/*.sh.
# Change a value, then: sudo systemctl restart slc-vms
PORT=$PORT
TLS_PORT=$TLS_PORT
BRANCH=$BRANCH
KIOSK=$KIOSK
OPEN_ON_START=$OPEN_ON_START
EOF
ok "Settings saved to $SLC_CONFIG"

# ── 4. Firewall ─────────────────────────────────────────────────────────────
# Both ports: guards on other PCs and phones use the HTTPS one for the camera.
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q 'Status: active'; then
    ufw allow "$PORT/tcp" comment 'SLC VMS' >/dev/null
    [ "$TLS_PORT" -gt 0 ] && ufw allow "$TLS_PORT/tcp" comment 'SLC VMS https' >/dev/null
    ok "Firewall: opened port $PORT$([ "$TLS_PORT" -gt 0 ] && echo " and $TLS_PORT")."
fi

# ── 5. First start, in this terminal ────────────────────────────────────────
# Stops the running service first, if this is a re-run: it would hold the
# port, and pip must not replace packages under a running server.
systemctl stop slc-vms 2>/dev/null || true
say 'First start: setting up the server (the first time takes several minutes)...'
as_user bash "$SLC_REPO/scripts/linux/run-campus.sh" --setup-only

# ── 6. Slip printer ─────────────────────────────────────────────────────────
# Before the service starts: the SLIP_PRINTER this writes into .env is read
# only when the server starts.
# A printer already named in .env counts as set up whatever it is called: a
# queue made with --name/--uri need not look thermal by name or address.
wanted="$(as_user python3 "$SLC_REPO/scripts/campus-env.py" "$SLC_BACKEND/.env" get SLIP_PRINTER)"
if [[ "${wanted,,}" =~ $SLC_PRINTER_OFF_RE ]]; then
    say 'Slip printer: off (SLIP_PRINTER in backend/.env) - slips print through the browser.'
elif [ -n "$wanted" ] && { [[ "$wanted" == /* ]] || lpstat -v 2>/dev/null | grep -qi "^device for ${wanted}:"; }; then
    ok "Slip printer already set up: $wanted"
elif lpstat -v 2>/dev/null | grep -Eqi "$SLC_THERMAL_RE"; then
    ok 'A slip printer queue is already set up.'
else
    bash "$SLC_REPO/scripts/linux/slip-printer-setup.sh" ||
        say 'No slip printer set up. Slips print through the browser until one is (sudo scripts/linux/slip-printer-setup.sh).'
fi

# ── 7. The service ──────────────────────────────────────────────────────────
# sed_escape keeps a & | or \ in the path from being read as sed syntax, and
# % is systemd syntax, so the unit gets it doubled.
sed_escape() { printf '%s' "$1" | sed -e 's/[\\&|]/\\&/g'; }
repo_sed="$(sed_escape "$SLC_REPO")"
sed -e "s|@USER@|$RUN_USER|g" -e "s|@REPO@|$(sed_escape "${SLC_REPO//%/%%}")|g" \
    "$SLC_REPO/scripts/linux/slc-vms.service.in" > /etc/systemd/system/slc-vms.service
systemctl daemon-reload
systemctl enable slc-vms
systemctl restart slc-vms
ok 'The slc-vms service is running and starts on every boot (logs: journalctl -u slc-vms -f).'

# ── 8. The kiosk ────────────────────────────────────────────────────────────
autostart="$HOME/.config/autostart/slc-vms-kiosk.desktop"
if [ "$OPEN_ON_START" = none ]; then
    rm -f "$autostart"
else
    if ! command -v google-chrome >/dev/null 2>&1 && ! command -v chromium >/dev/null 2>&1 &&
       ! command -v chromium-browser >/dev/null 2>&1; then
        warn 'No Chrome or Chromium found. The kiosk needs one for the webcam: install Google Chrome (or: sudo snap install chromium).'
    fi
    as_user mkdir -p "$(dirname "$autostart")"
    sed -e "s|@REPO@|$repo_sed|g" "$SLC_REPO/scripts/linux/slc-vms-kiosk.desktop.in" | as_user tee "$autostart" >/dev/null
    ok "The $OPEN_ON_START page opens$([ "$KIOSK" = true ] && echo ' full screen') at desktop login."
fi

echo
ok 'Done. The campus server is installed.'
say "Log out and back in once, so the new printer groups apply to $RUN_USER."

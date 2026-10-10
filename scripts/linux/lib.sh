# shellcheck shell=bash
# shellcheck disable=SC2034  # the variables here are read by the scripts that source it
# lib.sh - what every Linux campus script needs to know. Sourced, never run.
#
# The Linux counterpart of campus-config.ps1: where the checkout, the
# virtualenv and the per-machine settings live, the settings' defaults, and
# this machine's LAN address. Nothing here has side effects beyond defining
# variables and functions.

SLC_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SLC_BACKEND="$SLC_REPO/backend"
SLC_PY="$SLC_BACKEND/venv/bin/python"

# Machine settings and generated files live outside the checkout, so a
# `git pull` or a fresh clone never resets them. Under systemd, User= sets
# HOME, so the service finds the same files the person who installed it did.
SLC_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/slc-vms"
SLC_CONFIG="$SLC_CONFIG_DIR/campus.conf"
SLC_DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/slc-vms"

# Defaults, the same as Get-CampusLauncherConfig on Windows. campus.conf
# (written by install.sh) overrides any of them.
PORT=8000
TLS_PORT=8443
BRANCH=main
KIOSK=true
OPEN_ON_START=none          # none | guard | admin
# shellcheck source=/dev/null
[ -f "$SLC_CONFIG" ] && . "$SLC_CONFIG"

# What counts as the thermal slip printer (by CUPS queue name or device URI),
# and the SLIP_PRINTER values that turn server printing off. The bash copies
# of THERMAL_PATTERN and _OFF in backend/scanning/slip_printer.py: change
# them together, or the startup check disagrees with what the server prints to.
SLC_THERMAL_RE='pos-?[[:space:]]?58|jp-?[[:space:]]?58|58[[:space:]]?mm|thermal|receipt'
SLC_PRINTER_OFF_RE='^(off|none|browser|0|false)$'

# Colour only on a terminal: under systemd every line goes to the journal,
# where escape codes are noise.
if [ -t 1 ]; then
    _c() { printf '\033[%sm' "$1"; }
else
    _c() { :; }
fi
say()  { printf '%s[campus]%s %s\n' "$(_c 36)" "$(_c 0)" "$*"; }
ok()   { printf '%s[campus] %s%s\n' "$(_c 32)" "$*" "$(_c 0)"; }
note() { printf '%s[campus] %s%s\n' "$(_c 90)" "$*" "$(_c 0)"; }
# No [campus] prefix: "WARNING" leads the line, as on Windows.
warn() { printf '%sWARNING: %s%s\n' "$(_c 33)" "$*" "$(_c 0)"; }
die()  { printf '%s[campus] %s%s\n' "$(_c 31)" "$*" "$(_c 0)" >&2; exit 1; }

# This machine's LAN address, recomputed on every run rather than stored.
# 192.168.* first, as on Windows: the cameras live there, and the first
# address on a box running Docker or a VM is often a bridge no guard's
# browser can reach - so bridge-style interfaces are skipped outright.
lan_address() {
    local addrs pick
    if command -v ip >/dev/null 2>&1; then
        addrs="$(ip -4 -o addr show scope global 2>/dev/null |
                 awk '$2 !~ /^(docker|br-|veth|virbr|lxc|lxd|cni|flannel|podman)/ {
                          split($4, a, "/"); print a[1] }')"
    else
        addrs="$(hostname -I 2>/dev/null | tr ' ' '\n')"
    fi
    addrs="$(printf '%s\n' "$addrs" | grep -Ev '^(127\.|169\.254\.|$)' || true)"
    pick="$(printf '%s\n' "$addrs" | grep -m1 '^192\.168\.' || true)"
    [ -n "$pick" ] || pick="$(printf '%s\n' "$addrs" | head -n1)"
    printf '%s\n' "${pick:-127.0.0.1}"
}

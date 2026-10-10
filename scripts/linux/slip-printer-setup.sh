#!/usr/bin/env bash
# slip-printer-setup.sh - set up the thermal slip printer (POS58 / JP-58H) as a
# raw CUPS queue. The Linux counterpart of slip-printer-port.ps1. Run with sudo.
#
#     sudo scripts/linux/slip-printer-setup.sh                 find it on USB
#     sudo scripts/linux/slip-printer-setup.sh --uri usb://...  use this device
#     sudo scripts/linux/slip-printer-setup.sh --name GATE     queue name (default POS58)
#
# Raw, because the server sends finished ESC/POS bytes (scanning/slip_printer.py)
# and no driver should touch them: paper sizes, margins and scaling are what
# made the browser dialog unreliable in the first place. CUPS 2.4 prints a
# "raw queues are deprecated" warning; they still work.
#
# It also writes SLIP_PRINTER=<queue> into backend/.env, so the server prints
# to this queue by name and, if CUPS is ever stopped, says so instead of
# quietly falling back to the browser.
set -euo pipefail

# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

[ "$(id -u)" = 0 ] || die 'Run with sudo: sudo scripts/linux/slip-printer-setup.sh'

name=POS58
uri=''
while [ $# -gt 0 ]; do
    case "$1" in
        --name) name="$2"; shift ;;
        --uri)  uri="$2"; shift ;;
        *)      die "Unknown option: $1" ;;
    esac
    shift
done

export LC_ALL=C.UTF-8
command -v lpadmin >/dev/null 2>&1 || die 'CUPS is not installed. Run: sudo apt install cups'

if ! lpstat -r 2>/dev/null | grep -q 'scheduler is running'; then
    say 'The CUPS print service was stopped - starting it.'
    systemctl enable --now cups
fi

if [ -z "$uri" ]; then
    say 'Looking for the thermal printer on USB (a few seconds)...'
    usb="$(lpinfo -v 2>/dev/null | awk '$2 ~ /^usb:/ { print $2 }' || true)"
    uri="$(printf '%s\n' "$usb" | grep -Ei "$SLC_THERMAL_RE" | head -n1 || true)"
    if [ -z "$uri" ]; then
        if [ -n "$usb" ]; then
            say 'No USB printer looks like a thermal one. These are connected:'
            printf '%s\n' "$usb" | sed 's/^/    /'
            say "Run again with --uri <one of them>."
        else
            say 'No USB printer found. Check it is plugged in and switched on, then run this again.'
        fi
        exit 1
    fi
fi

lpadmin -p "$name" -E -v "$uri" -m raw -o printer-is-shared=false
cupsenable "$name"
cupsaccept "$name"
ok "Slip printer queue $name -> $uri"

# Recorded as the user who owns the checkout, so .env keeps its owner.
owner="$(stat -c %U "$SLC_BACKEND")"
if [ -f "$SLC_BACKEND/.env" ]; then
    sudo -u "$owner" python3 "$SLC_REPO/scripts/campus-env.py" "$SLC_BACKEND/.env" set SLIP_PRINTER "$name"
    ok "SLIP_PRINTER=$name written to backend/.env. Restart the server to use it: sudo systemctl restart slc-vms"
else
    say "backend/.env does not exist yet. The server will find $name by its device name anyway."
fi

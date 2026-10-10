#!/usr/bin/env bash
# open-kiosk.sh - open the gate (or admin) page in a full-screen browser once
# the campus server answers. The Linux version of the Windows launcher's
# Open-CampusPage.
#
#     scripts/linux/open-kiosk.sh            the page campus.conf's OPEN_ON_START names
#     scripts/linux/open-kiosk.sh guard      the guard login
#     scripts/linux/open-kiosk.sh admin      the account login
#
# install.sh adds it to the desktop session's autostart when OPEN_ON_START is
# guard or admin, so a gate terminal that logs in by itself ends up on the
# scanner with nobody at the keyboard. Alt+F4 closes the kiosk.
set -uo pipefail

# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

which="${1:-$OPEN_ON_START}"
case "$which" in
    guard) path='/security/guard-login' ;;
    admin) path='/login' ;;
    none)  exit 0 ;;
    *)     die "Unknown page: $which (guard, admin or none)" ;;
esac

# Wait for the server. On boot the desktop is usually up well before it is,
# and a kiosk showing "This site can't be reached" stays that way.
for _ in $(seq 1 300); do
    curl -fsS -o /dev/null --max-time 2 "http://127.0.0.1:${PORT}/healthz" && break
    sleep 2
done

# The same origin the server announces, not localhost: the server only
# trusts its LAN origin for CSRF and ALLOWED_HOSTS, and the camera flag below
# names it. Worked out AFTER the wait: the desktop can log in before DHCP has
# handed out the address, and the server only starts once it has one.
ORIGIN="http://$(lan_address):${PORT}"
url="$ORIGIN$path"

browser=''
for b in google-chrome-stable google-chrome chromium chromium-browser microsoft-edge-stable; do
    if command -v "$b" >/dev/null 2>&1; then browser="$(command -v "$b")"; break; fi
done
if [ -z "$browser" ]; then
    # A plain browser refuses the camera on http://, so the QR scanners will not
    # start - but the page itself still works.
    warn 'Chrome or Chromium was not found - opening the default browser. Kiosk mode and the webcam QR scanner need one of them.'
    exec xdg-open "$url"
fi

# A profile of its own: a running browser would otherwise take the --kiosk
# over and open an ordinary window, and the camera flag below only applies
# with a --user-data-dir. Ubuntu's Chromium is a snap, which may only write
# under ~/snap/chromium/.
case "$browser" in
    /snap/*) profile="$HOME/snap/chromium/common/slc-vms-browser" ;;
    *)       profile="$SLC_DATA_DIR/browser" ;;
esac
mkdir -p "$profile"

# The flags, and why, are the Windows launcher's (campus-launcher.ps1,
# Open-CampusPage): the LAN origin made a secure context so the webcam works,
# the camera prompt answered for an unattended terminal, and no first-run or
# crash bubbles over the scanner. --password-store=basic is Linux's own: a
# fresh profile otherwise asks to unlock the desktop keyring, a dialog a
# kiosk with no keyboard user cannot get past.
args=(
    "--user-data-dir=$profile"
    "--unsafely-treat-insecure-origin-as-secure=$ORIGIN"
    --use-fake-ui-for-media-stream
    --no-first-run
    --disable-session-crashed-bubble
    --disable-infobars
    --test-type
    --password-store=basic
)
if [ "$KIOSK" = true ]; then
    args+=(--kiosk "$url")
    case "$browser" in *edge*) args+=(--edge-kiosk-type=fullscreen --no-first-run-experience) ;; esac
else
    args+=(--new-window "$url")
fi
exec "$browser" "${args[@]}"

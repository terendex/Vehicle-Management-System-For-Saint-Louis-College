#!/usr/bin/env bash
# check-cameras.sh - can this machine reach each camera registered in the database?
#
# The Linux port of check-cameras.ps1, with the same output lines:
#     reachable : <name> (<host>)
#     NO ROUTE  : <name> (<host>) - live scanning will not work from this machine
# or "no cameras registered yet", or "could not read the camera list:".
set -uo pipefail

# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# Errors are shown rather than swallowed: hiding them once let a database the
# server could not reach masquerade as "no cameras registered".
if ! cam_out="$(cd "$SLC_BACKEND" && "$SLC_PY" manage.py camera_hosts 2>&1)"; then
    echo '  could not read the camera list:'
    printf '%s\n' "$cam_out" | tail -n3 | sed 's/^/    /'
    exit 0
fi

rows="$(printf '%s\n' "$cam_out" | grep $'\t' || true)"
if [ -z "$rows" ]; then
    echo '  no cameras registered yet - add them in Device Management'
    exit 0
fi

while IFS=$'\t' read -r name host; do
    if ping -c1 -W1 "$host" >/dev/null 2>&1; then
        echo "  reachable : $name ($host)"
    else
        echo "  NO ROUTE  : $name ($host) - live scanning will not work from this machine"
    fi
done <<<"$rows"

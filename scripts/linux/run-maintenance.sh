#!/usr/bin/env bash
# run-maintenance.sh - the daily maintenance job, for cron. The Linux port of
# run-maintenance.cmd.
#
# The server already runs these jobs itself every day (vehicles/scheduler.py),
# so this is an optional backstop for a machine that is often switched off at
# the time they would run. `manage.py run_maintenance` runs them in-process
# with no broker. A crontab line (crontab -e) for 06:30 every day:
#
#     30 6 * * * /bin/bash "/path/to/repo/scripts/linux/run-maintenance.sh"
#
# Output is appended to backend/maintenance.log (gitignored): cron has no
# console, so without it a failure would leave no trace. Extra arguments pass
# through, so it can be run by hand for a dry check:
#     scripts/linux/run-maintenance.sh --skip-purge
set -uo pipefail

# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

LOG="$SLC_BACKEND/maintenance.log"
stamp() { date '+%Y-%m-%d %H:%M:%S'; }

if [ ! -x "$SLC_PY" ]; then
    echo "[$(stamp)] FAILED: no virtualenv at $SLC_PY" >> "$LOG"
    exit 1
fi

cd "$SLC_BACKEND" || exit 1
{ echo; echo "[$(stamp)] run_maintenance starting"; } >> "$LOG"
"$SLC_PY" manage.py run_maintenance "$@" >> "$LOG" 2>&1
rc=$?
echo "[$(stamp)] run_maintenance exited with $rc" >> "$LOG"
exit "$rc"

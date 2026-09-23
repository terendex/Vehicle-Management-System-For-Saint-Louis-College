# Bash counterpart of demo-env.ps1, for driving the capture from a POSIX shell:
#
#     . docs/user-manual/capture/demo-env.sh
#
# Same contract: the database is pinned inside demo_settings.py, and only the
# credentials and the media root come from here. See demo-env.ps1 for why the
# database cannot be passed as an environment variable.

_capture="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_repo="$(cd "$_capture/../../.." && pwd)"

_envval() {
  grep -E "^\s*$1\s*=" "$_repo/backend/.env" | head -1 | sed 's/^[^=]*=//' | tr -d '"'"'"'\r' | sed 's/^ *//;s/ *$//'
}

export DEMO_PG_USER="$(_envval DB_USER)"
export DEMO_PG_PASSWORD="$(_envval DB_PASSWORD)"
export PGPASSWORD="$DEMO_PG_PASSWORD"
export DEMO_MEDIA_ROOT="$_capture/demo_media"
export PYTHONPATH="$_capture"
export DJANGO_SETTINGS_MODULE=demo_settings
export DISABLE_DAILY_SCHEDULER=true
export DISABLE_PARKING_AUTODETECT=true

# Ports the capture expects: lib.mjs points Playwright at 5199, and Vite
# proxies /api and /ws to the backend on 8765. Both are deliberately off the
# usual 5173/8000 so a capture run cannot collide with a dev server someone
# already has open against the real database.
export DEMO_BACKEND_PORT=8765
export DEMO_FRONTEND_PORT=5199
export BACKEND_URL="http://127.0.0.1:$DEMO_BACKEND_PORT"

export DEMO_PY="$_repo/backend/venv/Scripts/python.exe"
export DEMO_REPO="$_repo"
export DEMO_CAPTURE="$_capture"

echo "demo env ready - slc_manual_demo on 127.0.0.1, backend :$DEMO_BACKEND_PORT, vite :$DEMO_FRONTEND_PORT"

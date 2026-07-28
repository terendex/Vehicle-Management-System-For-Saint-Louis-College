#!/bin/sh
# Container entrypoint for the Railway deployment.
set -e

# Railway assigns the listening port at runtime and routes its edge to it.
# Binding a hardcoded 8000 makes the service unreachable and fails the healthcheck.
PORT="${PORT:-8000}"

# Migrations run here rather than in the Docker build: the build has no access
# to the production database, and running them at boot keeps schema and code
# moving together on every deploy.
if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
    echo "[start] Applying database migrations..."
    python manage.py migrate --noinput
fi

echo "[start] Starting Daphne on 0.0.0.0:${PORT}"
# Daphne (ASGI) rather than gunicorn/WSGI — the live scan and updates feeds are
# WebSocket consumers and would not work behind a WSGI server.
exec daphne -b 0.0.0.0 -p "${PORT}" --proxy-headers config.asgi:application

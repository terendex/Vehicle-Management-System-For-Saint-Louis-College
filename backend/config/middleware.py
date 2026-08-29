"""Project-wide middleware."""

import logging
import os
import time

from django.db import connection

# How long a pooled connection may sit unused before we bother verifying it.
# Neon's pgbouncer (and the serverless compute behind it) can drop a connection
# that has been idle for a while; a connection used seconds ago is never stale.
IDLE_BEFORE_HEALTH_CHECK_SECONDS = 60


class IdleConnectionHealthCheckMiddleware:
    """Verify a reused DB connection only when it has actually been idle.

    We keep persistent connections (CONN_MAX_AGE=600), which saves ~307ms of
    TCP+TLS setup to Neon on every request. The risk is that Django hands a
    view a connection the server has already closed, which surfaces as an
    OperationalError instead of a page.

    Django's built-in answer is CONN_HEALTH_CHECKS=True, but that runs a
    `SELECT 1` before *every* request — measured at ~48ms against Singapore,
    which would roughly double the latency of our sub-100ms endpoints.

    So we run the same check, but only when the gap since the last request
    exceeds IDLE_BEFORE_HEALTH_CHECK_SECONDS. Back-to-back requests (the
    normal case, and every request on the scanning hot path) pay nothing;
    the first request after a long idle pays 48ms to avoid a hard failure.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # `connection` is thread-local, so this attribute tracks the worker
        # thread's own connection — which is exactly the one being reused.
        last_used = getattr(connection, '_last_used_at', None)
        if (
            connection.connection is not None
            and last_used is not None
            and (time.monotonic() - last_used) > IDLE_BEFORE_HEALTH_CHECK_SECONDS
            and not connection.is_usable()
        ):
            # Dead socket. Closing it makes the next query reconnect cleanly.
            connection.close()

        try:
            return self.get_response(request)
        finally:
            connection._last_used_at = time.monotonic()


# ── Slow request logging ──────────────────────────────────────────────────────
#
# Added because "submitting is slow" kept being diagnosed by reading code and
# guessing, which was wrong twice. This makes the server say where the time
# went instead: total wall time, how much of it was the database, and how many
# queries it took. Anything left over is time spent outside Postgres — the
# channel-layer fan-out on every write, an R2 upload, an email handed to a
# thread that turned out not to be one.
#
# Only slow requests are logged, so a healthy server stays quiet.

SLOW_REQUEST_SECONDS = float(os.getenv('SLOW_REQUEST_SECONDS', '3'))

log = logging.getLogger(__name__)


class SlowRequestLogMiddleware:
    """Log any request that takes longer than SLOW_REQUEST_SECONDS.

    Query timing comes from `connection.execute_wrapper`, not `connection.queries`
    — the latter only records anything when DEBUG is on, which is exactly when
    nobody is looking at a production stall.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        stats = {'count': 0, 'seconds': 0.0}

        def timer(execute, sql, params, many, context):
            started = time.monotonic()
            try:
                return execute(sql, params, many, context)
            finally:
                stats['count'] += 1
                stats['seconds'] += time.monotonic() - started

        started = time.monotonic()
        with connection.execute_wrapper(timer):
            response = self.get_response(request)
        total = time.monotonic() - started

        if total >= SLOW_REQUEST_SECONDS:
            log.warning(
                "[slow] %s %s -> %s | total=%.2fs db=%.2fs (%d queries) "
                "| non-db=%.2fs",
                request.method, request.path,
                getattr(response, 'status_code', '?'),
                total, stats['seconds'], stats['count'],
                total - stats['seconds'],
            )
        return response

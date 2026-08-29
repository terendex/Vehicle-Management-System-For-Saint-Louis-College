"""Build the S3 signing client at server start instead of on a page load.

Signing a document URL needs a botocore S3 client, and building the first one in
a process costs about a second — almost all of it botocore loading its service
model. django-storages caches that client per *thread*, so before this the cost
landed on whichever reviewer's request happened to arrive on a fresh worker
thread; see the comment in document_urls.py for why one shared client is safe.

The build still has to happen once. Doing it here means it happens while the
server is starting, in a thread nobody is waiting on, rather than in front of
someone opening Vehicle Registration.

Guarded the same way as vehicles/scheduler.py: only the server process warms up,
so `manage.py migrate` and the test runner do not pay for a client they will
never use.
"""
from __future__ import annotations

import logging
import os
import sys
import threading

log = logging.getLogger(__name__)

_started = False
_lock = threading.Lock()


def _warm():
    try:
        from .document_urls import warm_signing_client
        warm_signing_client()
    except Exception:
        # A failed warm-up costs a slow first request, nothing more — the
        # signing path builds its own client on demand either way.
        log.warning('[documents] signing client warm-up failed', exc_info=True)


def start():
    """Warm the signing client once per server process, off the request path."""
    global _started

    # Migrations, tests, shells and one-shot commands have no reviewer waiting
    # on a page, so they gain nothing from a client they will never use.
    argv = ' '.join(sys.argv)
    is_server = (
        'daphne' in argv or 'uvicorn' in argv or 'gunicorn' in argv
        or 'runserver' in argv
    )
    if not is_server:
        return

    # runserver's autoreloader runs the app twice; only the reloaded child
    # (RUN_MAIN=true) needs to warm anything.
    if 'runserver' in argv and os.environ.get('RUN_MAIN') != 'true':
        return

    with _lock:
        if _started:
            return
        _started = True

    threading.Thread(target=_warm, name='document-signing-warmup', daemon=True).start()

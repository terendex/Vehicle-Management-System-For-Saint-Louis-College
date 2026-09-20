"""Debug settings: the project's normal settings, plus readable logging.

WHY THIS FILE EXISTS
    The normal settings (config/settings.py) do not set up logging. Python's
    fallback then prints only warnings and errors, with no time or source, and
    silently drops every `logger.info(...)` message the app writes. This file
    loads the normal settings unchanged and adds a logging setup on top, so
    that while you are debugging you can see what the app is doing.

    Production never uses this file. It only takes effect when you choose it
    with the DJANGO_SETTINGS_MODULE environment variable.

HOW TO USE IT (PowerShell, from the backend/ folder)
    $env:DJANGO_SETTINGS_MODULE = 'debug_settings'
    venv\\Scripts\\python.exe -m daphne -b 127.0.0.1 -p 8000 config.asgi:application
    # or any manage.py command, e.g.  venv\\Scripts\\python.exe manage.py doctor
    Remove-Item Env:DJANGO_SETTINGS_MODULE    # back to the normal settings

    Optional environment variables:
    LOG_LEVEL=DEBUG   show more detail from the app (default: INFO)
    LOG_SQL=1         also print every database query (only works while DEBUG
                      is on, because Django only records queries then)

WHERE THE LOGS GO
    To the console, and to a file in backend/logs/ named after the program
    that is running (server.log, celery.log or manage.log). Each file is
    capped at 5 MB with 3 older copies kept; git ignores the whole folder.

CAUTION
    Everything else is exactly the normal settings, including the database and
    file storage named in backend/.env. If .env points at the shared Neon
    database, actions you take while debugging change real data. Run
    `manage.py doctor` to see which database and storage are in use.
"""

# ── Load the normal settings ─────────────────────────────────────────────────
# Copy every setting from the real settings file into this one, unchanged, so
# the app behaves exactly as it normally does apart from the logging below.
from config.settings import *  # noqa: F401,F403  (a deliberate "copy everything")

# Also name BASE_DIR explicitly, so it is clear where the backend folder comes from.
from config.settings import BASE_DIR  # noqa: E402

# Standard-library tools: reading environment variables and the command line.
import os  # noqa: E402
import sys  # noqa: E402

# ── Read the two optional switches ───────────────────────────────────────────
# How much detail to show from the app's own code. INFO is a sensible default;
# DEBUG shows everything; WARNING shows only problems.
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()

# Whether to print every database query. Off unless LOG_SQL is exactly "1",
# because query logs are very noisy.
LOG_SQL = os.getenv('LOG_SQL', '') == '1'

# ── Decide where the log file goes ───────────────────────────────────────────
# Keep log files in backend/logs/, and create that folder if it is missing.
LOG_DIR = BASE_DIR / 'logs'
LOG_DIR.mkdir(exist_ok=True)

# Give each kind of program its own file. On Windows, two programs writing to
# the same rotating file can crash when it is time to start a new file, so the
# web server, the Celery worker and one-off manage.py commands stay separate.
_command_line = ' '.join(sys.argv).lower()
if 'daphne' in _command_line or 'runserver' in _command_line:
    _log_name = 'server.log'        # the web server
elif 'celery' in _command_line:
    _log_name = 'celery.log'        # the background-job worker
else:
    _log_name = 'manage.log'        # everything else (manage.py commands, shells)

# ── The logging setup itself ─────────────────────────────────────────────────
# Django reads this LOGGING dictionary when it starts. It says how each message
# should look ("formatters"), where messages go ("handlers") and which parts
# of the program to listen to, at what level of detail ("loggers").
LOGGING = {
    # Version 1 is the only format Python's logging configuration understands.
    'version': 1,
    # Keep any loggers that libraries already set up, instead of silencing them.
    'disable_existing_loggers': False,

    # How each line looks.
    'formatters': {
        # Console: time, level, which part of the app, then the message.
        'console': {
            'format': '%(asctime)s %(levelname)-7s %(name)s: %(message)s',
            'datefmt': '%H:%M:%S',
        },
        # File: the same, but with the full date so old lines stay meaningful.
        'file': {
            'format': '%(asctime)s %(levelname)-7s %(name)s: %(message)s',
            'datefmt': '%Y-%m-%d %H:%M:%S',
        },
    },

    # Where lines are sent.
    'handlers': {
        # The terminal window you started the program from.
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'console',
        },
        # A file that starts over when it reaches 5 MB, keeping 3 older copies.
        'file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': str(LOG_DIR / _log_name),
            'maxBytes': 5 * 1024 * 1024,
            'backupCount': 3,
            'encoding': 'utf-8',
            'formatter': 'file',
        },
    },

    # Anything not listed below (mostly third-party libraries) only reports
    # warnings and errors, so their routine chatter does not bury the app's.
    'root': {
        'handlers': ['console', 'file'],
        'level': 'WARNING',
    },

    # The app's own code, one entry per top-level package. Every module in them
    # uses logging.getLogger(__name__), so "vehicles" also covers
    # "vehicles.views", "scanning" also covers "scanning.ml.reader", and so on.
    # propagate=False stops each line being printed a second time by 'root'.
    'loggers': {
        **{
            package: {'handlers': ['console', 'file'], 'level': LOG_LEVEL, 'propagate': False}
            for package in ('accounts', 'config', 'realtime', 'scanning', 'vehicles', 'violations')
        },
        # Django's request errors (every 4xx/5xx response, with the traceback
        # for 500s). Shown at WARNING so normal successful requests stay quiet.
        'django.request': {'handlers': ['console', 'file'], 'level': 'WARNING', 'propagate': False},
        # Database queries: printed only when LOG_SQL=1; otherwise kept quiet.
        'django.db.backends': {
            'handlers': ['console', 'file'],
            'level': 'DEBUG' if LOG_SQL else 'WARNING',
            'propagate': False,
        },
    },
}

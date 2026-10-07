"""Settings for the instructor demo: a local copy of the system on a clock you
can move. Started by `.\\dev.ps1 -SimClock` (see sim_clock.py).

The project's normal settings with readable logging (debug_settings), pinned
to a throwaway local PostgreSQL database, slc_sim_demo, so nothing done in the
demo can reach the live database whatever backend/.env says. The database is
pinned here rather than through an environment variable: in PowerShell an
env var set to '' is deleted, and load_dotenv then refills it from .env, which
points at Neon.

What is real in the demo:
  * the clock the server lives by (sim_clock), and every rule that reads it;
  * the scheduler: it runs, so jobs fire by themselves when their time comes;
  * email: it goes out through the normal transport, but every message is
    delivered to SIM_EMAIL_TO (your own inbox, from backend/.env), never to
    the address on the record. With SIM_EMAIL_TO unset it is printed in the
    backend window instead of sent.

Files go to backend/sim_media and backups to backend/sim_backups; Redis, R2
and Celery are replaced by in-process stand-ins, as in the manual-capture
settings.
"""
import os

os.environ['USE_R2'] = 'false'

from debug_settings import *  # noqa: E401,F401,F403
from config.settings import BASE_DIR, EMAIL_TRANSPORT_BACKEND as _REAL_TRANSPORT

# A fresh clone's backend/.env may have no key; the demo signs only its own
# local logins, so a fixed one is enough there.
SECRET_KEY = SECRET_KEY or 'sim-demo-only-not-a-secret'  # noqa: F405

SIM_DB_NAME = os.environ.get('SIM_DB_NAME', 'slc_sim_demo')
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': SIM_DB_NAME,
        'USER': os.environ.get('SIM_PG_USER') or os.environ.get('DB_USER', 'postgres'),
        'PASSWORD': os.environ.get('SIM_PG_PASSWORD') or os.environ.get('DB_PASSWORD', ''),
        'HOST': '127.0.0.1',
        'PORT': os.environ.get('SIM_PG_PORT', '5432'),
    }
}
assert SIM_DB_NAME.startswith('slc_sim'), 'the simulated clock only runs on an slc_sim* database'

CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
REDIS_URL = ''
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}
MEDIA_ROOT = os.path.join(BASE_DIR, 'sim_media')
MEDIA_URL = '/media/'
CELERY_BROKER_URL = 'memory://'
CELERY_RESULT_BACKEND = 'cache+memory://'
CELERY_TASK_ALWAYS_EAGER = True
DEBUG = True
ALLOWED_HOSTS = ['*']
# The demo's page (dev.ps1 -SimClock serves it on 5174, never the 5173/8000
# a normal run or the campus app may be using), for the links in emails.
FRONTEND_URL = 'http://127.0.0.1:5174'
PUBLIC_SITE_URL = 'http://127.0.0.1:5174'

# Email: the real transport, every message redirected to SIM_EMAIL_TO.
SIM_EMAIL_TO = (os.environ.get('SIM_EMAIL_TO') or '').strip()
SIM_REAL_EMAIL_BACKEND = _REAL_TRANSPORT
EMAIL_TRANSPORT_BACKEND = 'sim_clock.RedirectEmailBackend'
if EMAIL_BACKEND == _REAL_TRANSPORT:          # outbox switched off: send through the redirect directly
    EMAIL_BACKEND = EMAIL_TRANSPORT_BACKEND

# Backups run on the demo's date like every other job. Automatic, manual and
# pre-restore backups go to backend/sim_backups (git-ignored: the data may be a
# copy of the live database). Scheduled ones go to the folder picked in System
# Settings (Browse...), backend/sim_backups when none is, under their own name,
# so a demo backup saved beside real ones is never mistaken for one of them
# (accounts/backup_utils.py scheduled_prefix). sim_setup clears the folder a
# live copy brings along, which is the live one on the campus PC.
BACKUP_DIR = os.path.join(BASE_DIR, 'sim_backups')
SCHEDULED_BACKUP_PREFIX = 'demo-scheduled-backup-'

# Sign in on the password alone: no authenticator code at login, whatever the
# date (accounts/twofa.py). Sensitive actions still ask for a code.
SIM_SKIP_2FA_LOGIN = True

# Cameras open in the demo like on campus, so the gate scan and parking
# detection run on real video. SIM_CAMERAS=0 (dev.ps1 -NoCameras) keeps every
# camera closed, e.g. when a live copy's cameras must not get a second viewer.
SIM_CAMERAS = os.environ.get('SIM_CAMERAS', '').strip().lower() not in ('0', 'false', 'no')

# The clock itself. Installed now, before any app or model module is imported,
# so even a field's default=timezone.now picks up the simulated one.
SIM_CLOCK_ENABLED = True
SIM_CLOCK_FILE = os.path.join(BASE_DIR, 'sim_clock.json')

import sim_clock  # noqa: E402
sim_clock.install(SIM_CLOCK_FILE, DATABASES)

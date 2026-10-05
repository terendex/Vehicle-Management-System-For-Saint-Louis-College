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

Files go to backend/sim_media; Redis, R2 and Celery are replaced by in-process
stand-ins, as in the manual-capture settings.
"""
import os

os.environ['USE_R2'] = 'false'

from debug_settings import *  # noqa: E401,F401,F403
from config.settings import BASE_DIR, EMAIL_TRANSPORT_BACKEND as _REAL_TRANSPORT

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
FRONTEND_URL = 'http://127.0.0.1:5173'
PUBLIC_SITE_URL = 'http://127.0.0.1:5173'

# Email: the real transport, every message redirected to SIM_EMAIL_TO.
SIM_EMAIL_TO = (os.environ.get('SIM_EMAIL_TO') or '').strip()
SIM_REAL_EMAIL_BACKEND = _REAL_TRANSPORT
EMAIL_TRANSPORT_BACKEND = 'sim_clock.RedirectEmailBackend'
if EMAIL_BACKEND == _REAL_TRANSPORT:          # outbox switched off: send through the redirect directly
    EMAIL_BACKEND = EMAIL_TRANSPORT_BACKEND

# The clock itself. Installed now, before any app or model module is imported,
# so even a field's default=timezone.now picks up the simulated one.
SIM_CLOCK_ENABLED = True
SIM_CLOCK_FILE = os.path.join(BASE_DIR, 'sim_clock.json')

import sim_clock  # noqa: E402
sim_clock.install(SIM_CLOCK_FILE, DATABASES)

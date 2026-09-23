"""Manual-screenshot settings: the real settings, pinned to a throwaway local
database with every outward-facing service switched off. Nothing here can
reach Neon, R2, Redis or a mail server whatever backend/.env contains."""
import os

os.environ['USE_R2'] = 'false'
from config.settings import *  # noqa

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'slc_manual_demo',
        'USER': os.environ['DEMO_PG_USER'],
        'PASSWORD': os.environ['DEMO_PG_PASSWORD'],
        'HOST': '127.0.0.1',
        'PORT': '5432',
    }
}
assert DATABASES['default']['NAME'] == 'slc_manual_demo'

CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
REDIS_URL = ''
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}
MEDIA_ROOT = os.environ['DEMO_MEDIA_ROOT']
MEDIA_URL = '/media/'
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
BREVO_API_KEY = ''
RESEND_API_KEY = ''
CELERY_BROKER_URL = 'memory://'
CELERY_RESULT_BACKEND = 'cache+memory://'
CELERY_TASK_ALWAYS_EAGER = True
DEBUG = True
ALLOWED_HOSTS = ['*']
FRONTEND_URL = 'http://127.0.0.1:5173'
PUBLIC_SITE_URL = 'http://127.0.0.1:5173'

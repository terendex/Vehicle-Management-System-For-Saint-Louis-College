import os
from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("config")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# On Windows, force solo pool by default — prefork uses os.fork() which is unavailable,
# and billiard's custom semaphores can hit PermissionError [WinError 5] on this platform.
app.conf.update(
    worker_pool=os.getenv("CELERY_WORKER_POOL", "solo"),
)

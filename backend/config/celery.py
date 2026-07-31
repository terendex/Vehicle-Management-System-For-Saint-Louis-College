import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("config")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# On Windows, force solo pool by default — prefork uses os.fork() which is unavailable,
# and billiard's custom semaphores can hit PermissionError [WinError 5] on this platform.
app.conf.update(
    worker_pool=os.getenv("CELERY_WORKER_POOL", "solo"),
)

# Periodic tasks — run by `celery -A config beat`
app.conf.beat_schedule = {
    # Purge AccessLog and Violation records beyond the configured retention window.
    # Runs at 02:00 server time every day.
    "purge-old-records-daily": {
        "task":     "vehicles.purge_old_records",
        "schedule": crontab(hour=2, minute=0),
    },
    # Activate events whose date is today; archive events whose date has passed.
    # Runs at 00:01 server time every day.
    "auto-manage-events-daily": {
        "task":     "vehicles.auto_manage_events",
        "schedule": crontab(hour=0, minute=1),
    },
    # Archive vehicle-owner accounts whose expiration date has passed.
    # Runs at 00:05 server time every day.
    "auto-archive-expired-accounts-daily": {
        "task":     "vehicles.auto_archive_expired_accounts",
        "schedule": crontab(hour=0, minute=5),
    },
}

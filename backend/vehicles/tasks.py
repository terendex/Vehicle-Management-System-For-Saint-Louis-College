from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

log = logging.getLogger(__name__)


@shared_task(name="vehicles.purge_old_records")
def purge_old_records():
    """Delete AccessLog and Violation rows older than the configured retention period."""
    from .models import SystemSettings
    from scanning.models import AccessLog
    from violations.models import Violation

    cfg = SystemSettings.get()
    cutoff = timezone.now() - timedelta(days=cfg.retention_years * 365)

    deleted_logs, _       = AccessLog.objects.filter(scanned_at__lt=cutoff).delete()
    deleted_violations, _ = Violation.objects.filter(issued_at__lt=cutoff).delete()

    log.info(
        "[purge_old_records] Removed %d AccessLog + %d Violation records older than %d year(s)",
        deleted_logs, deleted_violations, cfg.retention_years,
    )
    return {"deleted_logs": deleted_logs, "deleted_violations": deleted_violations}

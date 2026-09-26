"""Gate-side reactions to a logged scan.

One receiver: an authorized entry ticks off any visit the CDSO scheduled for
that plate today. It hangs off AccessLog itself rather than off the views
because an entry is written from half a dozen places — the camera consumer,
manual entry, QR scan, overrides, supplier and event branches, the visitor
slip — and a rule copied into each would miss the next one added.
"""
import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import AccessLog

logger = logging.getLogger(__name__)


@receiver(post_save, sender=AccessLog, dispatch_uid='scheduled_visit_arrival')
def tick_off_scheduled_visit(sender, instance, created, **kwargs):
    # Entries only: a refusal is not an arrival, and an exit row is EXITED.
    if not created or instance.status != AccessLog.Status.AUTHORIZED or not instance.plate_number:
        return
    from vehicles.scheduled_visits import mark_arrived_by_plate
    try:
        mark_arrived_by_plate(instance.plate_number, instance.scanned_at)
    except Exception:
        # Bookkeeping for the CDSO's list; never worth failing the gate over.
        logger.exception('[scheduled visits] could not mark %s arrived', instance.plate_number)

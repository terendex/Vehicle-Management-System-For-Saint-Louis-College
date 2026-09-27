"""Gate-side reactions to a logged scan.

One receiver: an authorized entry ticks off any visit the CDSO scheduled for
that plate today, and records that booking on the entry row itself. It hangs off AccessLog itself rather than off the views
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
    from vehicles.scheduled_visits import mark_arrived_by_plate, visit_expected_today
    try:
        mark_arrived_by_plate(instance.plate_number, instance.scanned_at)
        # Record on the entry which booking it came in under, so the log reads
        # "Scheduled Entry" from now on. A row created with one already (the
        # visitor slip, checked in from the booking) keeps it. update(), not
        # save(): saving again would re-fire this receiver.
        if instance.scheduled_visit_id is None:
            vehicle = instance.vehicle
            identifiers = (instance.plate_number,) + (
                (vehicle.plate_number, vehicle.conduction_number) if vehicle else ())
            visit = visit_expected_today(*identifiers)
            if visit is not None:
                AccessLog.objects.filter(pk=instance.pk).update(scheduled_visit=visit)
                instance.scheduled_visit = visit
    except Exception:
        # Bookkeeping for the CDSO's list; never worth failing the gate over.
        logger.exception('[scheduled visits] could not mark %s arrived', instance.plate_number)

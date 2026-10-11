# -*- coding: utf-8 -*-
"""Top up the demo database with what the September 2026 figures need.

seed_demo.py builds the whole fictional campus and is the thing to run against
an empty database. This is the smaller, repeatable follow-up: the handful of
rows the screens gained after the first edition of the manual was shot. It is
idempotent, so it can be re-run before every capture.

    . docs/user-manual/capture/demo-env.ps1
    & $py docs\\user-manual\\capture\\seed_figures.py

What it sets, and why the figure needs it:

  * An empty-lot baseline on both parking zones. A zone without one is "Not
    monitored yet" and its bay colours are not a reading, so the main parking
    figure would document the unfinished state rather than the working one.
  * A named report approver, with the preparer left blank. That is the
    documented default - the preparer is whoever exported the report - and the
    signatories figure has to show both halves behaving differently.
  * A few cars inside the gates, so the parking screens' "On Campus" tile is
    not zero. Zero next to seven parked bays reads as a broken counter, and
    the manual says in the same breath that On Campus is normally the HIGHER
    of the two - drop-offs and vehicles still circling.
  * The demo events and the demo confiscation re-dated around today. Both
    were seeded relative to the day seed_demo.py ran and expire with time,
    which empties the Events list and every Confiscated accounts panel.
  * One unpaid application submitted today with a fixed payment link, for
    the payment-page figure: the page opens only from the emailed link, and
    only while the application's 3 working days to pay are running.
"""
import os
import sys

import django

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'demo_settings')
django.setup()

from django.core.files.base import ContentFile          # noqa: E402
from django.core.files.storage import default_storage   # noqa: E402
from django.db import connection                        # noqa: E402
from django.utils import timezone                       # noqa: E402

from vehicles.models import ParkingZone, SystemSettings  # noqa: E402

if connection.settings_dict['NAME'] != 'slc_manual_demo':
    sys.exit('refusing to run: not the demo database (%s)' % connection.settings_dict['NAME'])


def set_baselines():
    """Copy each zone's reference image into its baseline, as the view does."""
    for zone in ParkingZone.objects.all():
        if not zone.reference_image:
            print('  zone %s: no reference image, skipped' % zone.name)
            continue
        if zone.baseline_image:
            print('  zone %s: baseline already set' % zone.name)
            continue
        with default_storage.open(zone.reference_image.name, 'rb') as fh:
            data = fh.read()
        # A COPY, exactly as set_baseline does it, so editing the reference
        # later cannot silently change what "empty" means for these bays.
        zone.baseline_image.save('zone_%s_baseline.jpg' % zone.id, ContentFile(data), save=False)
        zone.baseline_captured_at = timezone.now()
        zone.save(update_fields=['baseline_image', 'baseline_captured_at'])
        print('  zone %s: baseline set' % zone.name)


def set_signatories():
    cfg = SystemSettings.get_solo() if hasattr(SystemSettings, 'get_solo') else SystemSettings.objects.first()
    if cfg is None:
        cfg = SystemSettings.objects.create()
    cfg.report_approver_name = 'MARIA CLARA D. REYES'
    cfg.report_approver_position = 'Head, Campus Development and Security Office'
    # Deliberately blank: a blank preparer is the default, and it is what makes
    # the figure show the report naming whoever exported it.
    cfg.report_preparer_name = ''
    cfg.report_preparer_position = ''
    cfg.report_prepared_by_label = 'Prepared by'
    cfg.report_approved_by_label = 'Approved by'
    cfg.save()
    print('  signatories: approver %s' % cfg.report_approver_name)


def attach_receipt_photos():
    """Give the paid pending applications a photograph of their receipt.

    The photograph was re-introduced after the demo data was built, and it is
    the point of the review screen's payment block: without one the figure
    shows "No receipt photo on file" beside a callout that describes checking
    the number against the picture. Drawn here rather than shipped as a fixture
    so it stays obviously fictional.
    """
    from PIL import Image, ImageDraw               # noqa: PLC0415
    from django.core.files.base import ContentFile  # noqa: PLC0415
    from io import BytesIO                          # noqa: PLC0415

    from vehicles.models import VehicleRegistration  # noqa: PLC0415

    rows = VehicleRegistration.objects.filter(status='pending', payment_status='paid').exclude(or_number='')
    for reg in rows:
        if reg.or_receipt_image:
            print('  %s: receipt already on file' % reg.full_name)
            continue
        img = Image.new('RGB', (620, 820), '#FDFCF7')
        d = ImageDraw.Draw(img)
        d.rectangle([12, 12, 607, 807], outline='#C9BFA8', width=2)
        lines = [
            (40, 'SAINT LOUIS COLLEGE'),
            (68, 'City of San Fernando, La Union'),
            (112, 'OFFICIAL RECEIPT'),
            (168, 'No.  %s' % reg.or_number),
            (214, 'Received from:'),
            (240, reg.full_name),
            (292, 'In payment of:'),
            (318, 'Vehicle Pass Fee  —  A.Y. 2026-2027'),
            (378, 'Amount:  PHP %s' % (reg.amount_paid or '150.00')),
            (438, 'Date:  %s' % timezone.localdate().strftime('%d %B %Y')),
            (520, 'Cashier / Accounting Office'),
            (556, '__________________________'),
            (700, 'SAMPLE — demonstration data only'),
        ]
        for y, txt in lines:
            d.text((44, y), txt, fill='#2B2A26')
        buf = BytesIO()
        img.save(buf, 'JPEG', quality=88)
        reg.or_receipt_image.save('or_%s.jpg' % reg.or_number, ContentFile(buf.getvalue()), save=False)
        reg.save(update_fields=['or_receipt_image'])
        print('  %s: receipt photo attached' % reg.full_name)


def put_cars_on_campus(target=9):
    """Leave `target` cars inside the gates, as unpaired entries dated today.

    inside_counts() only looks at today's authorized entries with no exit row
    pointing back at them, and drops anything older than STALE_ENTRY_HOURS, so
    yesterday's demo rows count for nothing. Re-running is safe: the rows this
    made are cleared first, and nothing else is touched.
    """
    from datetime import timedelta                    # noqa: PLC0415
    from scanning.models import AccessLog             # noqa: PLC0415
    from vehicles.models import Vehicle               # noqa: PLC0415

    marker = 'manual figure: on campus'
    AccessLog.objects.filter(entry_note=marker).delete()

    # NBC1234 is the plate the "Checking a plate" figure looks up; left
    # inside, the check would log it out instead of showing an entry decision.
    cars = list(Vehicle.objects.filter(vehicle_type__iexact='car').exclude(plate_number=PLATE_CHECKED)[:target])
    if not cars:
        print('  no car vehicles in the demo data, skipped')
        return

    now = timezone.now()
    for i, vehicle in enumerate(cars):
        AccessLog.objects.create(
            vehicle=vehicle,
            plate_number=vehicle.plate_number or '',
            status=AccessLog.Status.AUTHORIZED,
            # A Gate key, as the scanner writes it: a bare '1' matches no
            # gate, so the Gate columns would print '1' instead of its label.
            gate_id='gate1',
            entry_note=marker,
            # Spread across the morning, and well inside STALE_ENTRY_HOURS so
            # none of them is dropped as an entry whose exit scan was missed.
            scanned_at=now - timedelta(minutes=35 * (i + 1)),
        )
    print('  %d cars left inside the gates' % len(cars))


def keep_events_current():
    """Re-date the demo events around today, as seed_demo.py first placed them.

    seed_demo.py dated them relative to the day it ran, and an event archives
    itself once its date passes, so a month later the Events figure showed
    "No upcoming events". Two upcoming, one past, as originally seeded.
    """
    from datetime import timedelta                    # noqa: PLC0415
    from vehicles.models import Event                 # noqa: PLC0415

    today = timezone.localdate()
    for name, days, archived in [('Research Congress 2026', 2, False),
                                 ('Foundation Day Parade', 12, False),
                                 ('Parents-Teachers Conference', -9, True)]:
        n = Event.objects.filter(name=name).update(date=today + timedelta(days=days), archived=archived)
        print('  %s: %s' % (name, ('%+d days' % days) if n else 'not in the demo data, skipped'))


def keep_confiscation_current():
    """Keep the demo 2nd-offense owner confiscated, ending two weeks out.

    The seeded confiscation ran out, which emptied every "Confiscated accounts"
    panel and left the Lift button the gate-records figure points at with
    nothing to point at.
    """
    from datetime import timedelta                    # noqa: PLC0415
    from accounts.models import User                  # noqa: PLC0415

    # Only the seeded 2nd-offense owner: a 2nd offense is two weeks, and other
    # levels run for other lengths (a 3rd has no end date at all).
    until = timezone.localdate() + timedelta(days=13)
    n = User.objects.filter(email=CONFISCATED_OWNER, confiscation_level=2).update(
        confiscated_at=timezone.now() - timedelta(days=1), confiscated_until=until)
    print('  %s: %s' % (CONFISCATED_OWNER, ('confiscated until %s' % until) if n else 'not at 2nd offense, skipped'))


def open_payment_link():
    """Give one unpaid applicant a fixed payment link that is still open.

    The payment page is reached only through the emailed link, and an unpaid
    application expires after 3 working days, so the payment-page figure needs
    an application submitted today with a token it can put in the URL.
    """
    from datetime import timedelta                    # noqa: PLC0415
    from vehicles.models import VehicleRegistration   # noqa: PLC0415

    # The hourly expiry job moves the row to 'expired' once its 3 working days
    # pass between captures, so bring an expired one back to pending too,
    # dropping the reason expire_overdue() wrote on it.
    n = VehicleRegistration.objects.filter(
        email=PAYMENT_APPLICANT, status__in=['pending', 'expired'], payment_status='unpaid',
    ).update(status='pending', rejection_reason='', payment_token=PAYMENT_TOKEN,
             created_at=timezone.now() - timedelta(hours=2))
    print('  %s: %s' % (PAYMENT_APPLICANT, 'link open, submitted 2 hours ago' if n else 'not an unpaid application, skipped'))


PLATE_CHECKED = 'NBC1234'
CONFISCATED_OWNER = '20221187@slc-sflu.edu.ph'   # MARK VILLANUEVA, 2nd offense in seed_demo.py
# Must match the payment-page figure's path in shots/shots.mjs.
PAYMENT_APPLICANT = 'sophia.navarro@example.com'
PAYMENT_TOKEN = '5eed0000-0000-4000-8000-0000000000a1'

print('baselines:')
set_baselines()
print('report signatories:')
set_signatories()
print('receipt photos:')
attach_receipt_photos()
print('on campus:')
put_cars_on_campus()
print('events:')
keep_events_current()
print('confiscations:')
keep_confiscation_current()
print('payment link:')
open_payment_link()
print('done')

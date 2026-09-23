"""Fictional demo data for the user-manual screenshots.

Run with: python manage.py shell -c "exec(open(r'<path>').read())"
Every name, plate, email and ID below is invented.
"""
import io
import random
from datetime import timedelta, time, date
from decimal import Decimal

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import connection
from django.utils import timezone

assert connection.settings_dict['NAME'] == 'slc_manual_demo', 'refusing to seed a non-demo database'

from accounts.models import User, AuditLog, Notification, TwoFactorDevice
from scanning.models import Gate, AccessLog, GuardShift, VisitorPass, Office
from vehicles.models import (
    Vehicle, VehicleRegistration, ReferenceItem, RuleConstraint, ParkingZone,
    ParkingSpace, SystemSettings, RegistrationPeriod, Event, ParkingNotice,
    Supplier, SupplierPlate, ScheduledVisit, Camera, RegistrationChangeRequest,
)
from violations.models import Violation

random.seed(20260913)
now = timezone.now()
today = timezone.localdate()
PASSWORD = 'Demo@2026!'

# Known TOTP secrets so the capture script can compute codes.
SECRETS = {
    'cdso.demo@slc-sflu.edu.ph': 'JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP',
    '20231001@slc-sflu.edu.ph':  'KRSXG5CTMVRXEZLUKRSXG5CTMVRXEZLU',
}

def dt(days_ago=0, hh=8, mm=0):
    d = timezone.localtime(now) - timedelta(days=days_ago)
    return d.replace(hour=hh, minute=mm, second=random.randint(0, 59), microsecond=0)

# ── Settings & period ─────────────────────────────────────────────────────
s = SystemSettings.get()
s.registration_start = today - timedelta(days=40)
s.registration_end = today + timedelta(days=60)
s.auto_backup_frequency = 'daily'
s.save()
RegistrationPeriod.objects.get_or_create(
    label='1st Semester AY 2026-2027',
    defaults=dict(start_date=today - timedelta(days=40), end_date=today + timedelta(days=60), is_active=True),
)

# ── Staff ─────────────────────────────────────────────────────────────────
admin = User.objects.filter(email='admin@slc.edu.ph').first()
if admin:
    admin.email = 'cdso.demo@slc-sflu.edu.ph'
    admin.full_name = 'MARIA DELA CRUZ'
    admin.set_password(PASSWORD)
    admin.must_change_password = False
    admin.save()
else:
    admin = User.objects.create_superuser('cdso.demo@slc-sflu.edu.ph', 'MARIA DELA CRUZ', PASSWORD)

guards = []
for name, email, gate, agency in [
    ('JUAN SANTOS', 'guard.santos@slc-sflu.edu.ph', 'gate1', 'Northstar Security Agency'),
    ('PEDRO REYES', 'guard.reyes@slc-sflu.edu.ph', 'gate4', 'Northstar Security Agency'),
    ('ANA GARCIA', 'guard.garcia@slc-sflu.edu.ph', 'gate1', 'Northstar Security Agency'),
]:
    g, _ = User.objects.get_or_create(email=email, defaults=dict(full_name=name, role='security'))
    g.full_name = name; g.role = 'security'; g.gate_assignment = gate; g.agency = agency
    g.set_password(PASSWORD); g.must_change_password = False
    import uuid; g.guard_qr_secret = g.guard_qr_secret or uuid.uuid4()
    g.save()
    guards.append(g)

for g in guards:
    g.last_login = now - timedelta(hours=2)
    g.save(update_fields=['last_login'])

# Past shifts + two open shifts
GuardShift.objects.all().delete()
for d in range(6, 0, -1):
    for g, gate in [(guards[0], 'gate1'), (guards[1], 'gate4')]:
        sh = GuardShift.objects.create(guard=g, gate=gate)
        GuardShift.objects.filter(pk=sh.pk).update(clocked_in_at=dt(d, 6, 0), clocked_out_at=dt(d, 18, 5), clocked_out_by=g)
for g, gate in [(guards[0], 'gate1'), (guards[1], 'gate4')]:
    sh = GuardShift.objects.create(guard=g, gate=gate)
    GuardShift.objects.filter(pk=sh.pk).update(clocked_in_at=dt(0, 6, 2))

# ── Reference items ───────────────────────────────────────────────────────
programs = list(ReferenceItem.objects.filter(category='program', is_active=True)[:10])
departments = {d.name: d for d in ReferenceItem.objects.filter(category='department')}
dept_list = list(departments.values())

# ── Owners / registrations ────────────────────────────────────────────────
people = [
    # name, type, id, plate, vtype, color, model, schedule, status, pay
    ('CARLO MENDOZA',      'student',  '20231001', 'NBC 1234', 'car',        'White',  'Toyota Vios',     'MWF',  'accepted', 'paid'),
    ('ANGELA RAMOS',       'student',  '20231002', 'ABK 5521', 'motorcycle', 'Red',    'Honda Click 125', 'TTHF', 'accepted', 'paid'),
    ('MARK VILLANUEVA',    'student',  '20221187', '123 QWE',  'motorcycle', 'Black',  'Yamaha NMAX',     'MWF',  'accepted', 'paid'),
    ('JOSEPHINE AQUINO',   'employee', 'EMP-0412', 'NDA 7788', 'car',        'Silver', 'Honda City',      'ANY',  'accepted', 'paid'),
    ('RICARDO BAUTISTA',   'employee', 'EMP-0077', 'NCR 4410', 'car',        'Gray',   'Mitsubishi Xpander', 'ANY', 'accepted', 'exempt'),
    ('LIZA FERNANDEZ',     'fetcher',  '',         'AAQ 9012', 'van',        'White',  'Toyota HiAce',    'ANY',  'accepted', 'paid'),
    ('KEVIN DOMINGO',      'student',  '20241045', 'BEE 3307', 'car',        'Blue',   'Suzuki Swift',    'TTHF', 'pending',  'paid'),
    ('PATRICIA LIM',       'student',  '20241098', '456 RTY',  'motorcycle', 'Blue',   'Suzuki Raider',   'MWF',  'pending',  'unpaid'),
    ('GABRIEL TORRES',     'employee', 'EMP-0533', 'NGH 2215', 'car',        'Black',  'Ford Ranger',     'ANY',  'pending',  'paid'),
    ('SOPHIA NAVARRO',     'fetcher',  '',         'CAB 6640', 'car',        'Red',    'Kia Picanto',     'ANY',  'pending',  'unpaid'),
    ('DANIEL CASTRO',      'student',  '20221302', 'BXY 1180', 'car',        'White',  'Nissan Almera',   'MWF',  'rejected', 'unpaid'),
    ('ISABEL SORIANO',     'student',  '20231277', '789 UIO',  'motorcycle', 'Gray',   'Honda Beat',      'TTHF', 'accepted', 'paid'),
]
owners = {}
for i, (name, rtype, idno, plate, vtype, color, model, sched, status, pay) in enumerate(people, start=1):
    first, last = name.title().split(' ', 1)
    email = f'{idno}@slc-sflu.edu.ph' if rtype == 'student' else f'{first.lower()}.{last.lower()}@example.com'
    days = {'MWF': ['Monday', 'Wednesday', 'Friday'], 'TTHF': ['Tuesday', 'Thursday', 'Friday']}.get(
        sched, ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'])
    reg = VehicleRegistration.objects.filter(email=email).first() or VehicleRegistration(email=email)
    reg.registrant_type = rtype; reg.full_name = name
    reg.address = f'{random.randint(10, 250)} Rizal St., San Fernando, La Union'
    reg.contact_number = f'09{random.randint(100000000, 999999999)}'
    reg.age = random.randint(19, 52)
    reg.drivers_license = f'A{random.randint(10, 99)}-{random.randint(10, 99)}-{random.randint(100000, 999999)}'
    reg.campus_days = days; reg.schedule = sched
    reg.plate_number = plate; reg.vehicle_type = vtype; reg.vehicle_color = color
    reg.status = status; reg.payment_status = pay
    reg.source = 'public' if i % 3 else 'direct'
    if rtype == 'student':
        reg.student_id = idno; reg.student_level = 'college'
        reg.program = programs[i % len(programs)] if programs else None
        reg.program_year = f'{(i % 4) + 1}'
    elif rtype == 'employee':
        reg.employee_id = idno
        reg.department = dept_list[i % len(dept_list)] if dept_list else None
        reg.department_type = 'cleaning_services' if pay == 'exempt' else 'teaching'
    else:
        reg.fetcher_type = 'drop_and_go' if i % 2 else 'standby'
        reg.fetcher_students = [{'full_name': 'MIGUEL FERNANDEZ', 'student_id': '20260311',
                                 'student_level': 'elementary', 'program_year': 'Grade 4'}]
    if pay == 'paid':
        reg.or_number = f'OR-{random.randint(100000, 999999)}'
        reg.amount_paid = reg.pass_fee(s); reg.paid_at = now - timedelta(days=20 - i)
    if status == 'rejected':
        reg.rejection_reason = 'Uploaded assessment form is from a previous semester.'
    reg.save()
    VehicleRegistration.objects.filter(pk=reg.pk).update(created_at=now - timedelta(days=30 - i, hours=i))

    if status == 'accepted':
        u = User.objects.filter(email=email, is_archived=False).first()
        if not u:
            u = User.objects.create_user(email, name, PASSWORD, role='vehicle_owner')
        u.owner_type = rtype; u.schedule = sched; u.campus_days = days
        u.contact = reg.contact_number; u.address = reg.address; u.must_change_password = False
        u.save()
        v, _ = Vehicle.objects.get_or_create(plate_number=plate.replace(' ', ''), defaults=dict(
            vehicle_type=vtype, model=model, color=color, is_authorized=True, user=u))
        v.user = u; v.is_authorized = True; v.save()
        reg.user = u; reg.vehicle = v; reg.reviewed_at = now - timedelta(days=25 - i)
        if rtype == 'student':
            reg.system_student_id = f'SLC-STU-{str(reg.pk).zfill(6)}'
        else:
            reg.system_employee_id = f"{'SLC-FET' if rtype == 'fetcher' else 'SLC-EMP'}-{str(reg.pk).zfill(6)}"
        reg.save()
        owners[plate.replace(' ', '')] = (u, v, reg)

for email, secret in SECRETS.items():
    u = User.objects.get(email=email, is_archived=False)
    TwoFactorDevice.objects.update_or_create(user=u, defaults=dict(
        secret=secret, confirmed_at=now - timedelta(days=30), last_used_step=0))
    u.last_login = now - timedelta(days=1)
    u.save(update_fields=['last_login'])

# One pending correction request from an owner
carlo_reg = owners['NBC1234'][2]
RegistrationChangeRequest.objects.filter(registration=carlo_reg).delete()
RegistrationChangeRequest.objects.create(
    registration=carlo_reg, requested_by=carlo_reg.user,
    changes={'vehicle_color': 'Pearl White', 'contact_number': '09171234567'},
    previous={'vehicle_color': carlo_reg.vehicle_color, 'contact_number': carlo_reg.contact_number},
)

# ── Suppliers / visits ────────────────────────────────────────────────────
for company, cat, plates in [
    ('Ilocos Fresh Produce Trading', 'delivery', ['WTX 4521', 'WTX 4522']),
    ('La Union Aircon Services', 'maintenance', ['LAS 1090']),
    ('Coastal Office Supplies Inc.', 'vendor', ['COS 3318']),
]:
    sup, _ = Supplier.objects.get_or_create(company_name=company, defaults=dict(category=cat))
    for p in plates:
        SupplierPlate.objects.get_or_create(plate_number=p.replace(' ', ''), defaults=dict(supplier=sup))
ScheduledVisit.objects.all().delete()
ScheduledVisit.objects.create(visitor_name='ENGR. ROBERTO SALAZAR', category='contractor', plate_number='RSZ 8801',
                              purpose='Roof inspection, Main Building', expected_date=today)
ScheduledVisit.objects.create(visitor_name='Ilocos Fresh Produce Trading', category='delivery',
                              supplier=Supplier.objects.get(company_name='Ilocos Fresh Produce Trading'),
                              plate_number='WTX 4521', purpose='Cafeteria delivery', expected_date=today, is_arrived=True)
ScheduledVisit.objects.create(visitor_name='DR. HELEN OCAMPO', category='guest', plate_number='HOC 2020',
                              purpose='Guest speaker, Research Congress', expected_date=today + timedelta(days=2))

# ── Cameras (documentation-range IPs: nothing answers) ────────────────────
Camera.objects.all().delete()
for n, name, ip, assign, gate in [
    (1, 'Gate 1 Entry Cam', '192.0.2.11', 'entry', 'gate1'),
    (2, 'Gate 4 Entry Cam', '192.0.2.14', 'entry', 'gate4'),
    (3, 'Main Parking Cam', '192.0.2.21', 'parking', None),
    (4, 'Motorcycle Parking Cam', '192.0.2.22', 'parking', None),
]:
    Camera.objects.create(cam_number=n, name=name, ip=ip, device_id=f'DEMO{n:04d}', password='',
                          rtsp_url=f'rtsp://admin:@{ip}:554/onvif1', assignment=assign, gate_id=gate,
                          is_active=False)

# ── Parking zones & bays with a drawn reference image ─────────────────────
from PIL import Image, ImageDraw
def lot_image(cols, rows, moto=False):
    W, H = 1280, 720
    img = Image.new('RGB', (W, H), (92, 96, 100))
    d = ImageDraw.Draw(img)
    for yy in range(0, H, 6):
        d.line([(0, yy), (W, yy)], fill=(88 + (yy % 12), 92 + (yy % 12), 96 + (yy % 12)))
    bw, bh = W / (cols + 1), H / (rows * 2 + 1)
    rects = []
    for r in range(rows):
        y0 = bh * (r * 2 + 0.5)
        for c in range(cols):
            x0 = bw * (c + 0.5)
            d.rectangle([x0, y0, x0 + bw * 0.92, y0 + bh * 1.6], outline=(245, 245, 235), width=4)
            rects.append((x0 / W, y0 / H, (x0 + bw * 0.92) / W, (y0 + bh * 1.6) / H))
            if random.random() < 0.55:
                cx, cy = x0 + bw * 0.46, y0 + bh * 0.8
                col = random.choice([(230, 230, 230), (40, 40, 45), (170, 30, 30), (60, 90, 160), (150, 150, 155)])
                if moto:
                    d.ellipse([cx - 14, cy - 40, cx + 14, cy + 40], fill=col)
                else:
                    d.rounded_rectangle([cx - bw * 0.33, cy - bh * 0.7, cx + bw * 0.33, cy + bh * 0.7], 18, fill=col)
                    d.rectangle([cx - bw * 0.25, cy - bh * 0.35, cx + bw * 0.25, cy - bh * 0.05], fill=(30, 40, 50))
    buf = io.BytesIO(); img.save(buf, 'JPEG', quality=88)
    return buf.getvalue(), rects

ParkingSpace.objects.all().delete(); ParkingZone.objects.all().delete()
for zname, cat, cam_no, cols, rows, prefix in [
    ('Main Building Car Park', 'car', 3, 8, 2, 'C'),
    ('Gym Motorcycle Area', 'motorcycle', 4, 12, 2, 'M'),
]:
    z = ParkingZone.objects.create(name=zname, vehicle_category=cat, camera=Camera.objects.get(cam_number=cam_no),
                                   occupancy_method='ml', detection_enabled=False)
    data, rects = lot_image(cols, rows, moto=(cat == 'motorcycle'))
    z.reference_image.save(f'{prefix.lower()}_zone.jpg', ContentFile(data), save=True)
    plates = list(owners.keys())
    for k, (x1, y1, x2, y2) in enumerate(rects, start=1):
        occ = random.random() < 0.55
        ParkingSpace.objects.create(zone=z, space_number=f'{prefix}{k:02d}', x1=x1, y1=y1, x2=x2, y2=y2,
                                    points=[[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
                                    is_occupied=occ, occupied_by=random.choice(plates) if occ else '')

# ── Events & notices ──────────────────────────────────────────────────────
Event.objects.all().delete()
Event.objects.create(name='Research Congress 2026', date=today + timedelta(days=2), start_time=time(8, 0),
                     end_time=time(17, 0), parking_share='third', organizer_plates=['HOC2020', 'RSZ8801'], created_by=admin)
Event.objects.create(name='Foundation Day Parade', date=today + timedelta(days=12), parking_share='half', created_by=admin)
Event.objects.create(name='Parents-Teachers Conference', date=today - timedelta(days=9), start_time=time(13, 0),
                     end_time=time(17, 0), parking_share='quarter', archived=True, created_by=admin)
ParkingNotice.objects.all().delete()
ParkingNotice.objects.create(title='Gym parking closed on Friday',
                             body='The gym motorcycle area will be closed on Friday for repainting. Please use the Main Building car park.',
                             created_by=admin)

# ── Gate traffic for the past week ────────────────────────────────────────
AccessLog.objects.all().delete()
known = list(owners.items())
sup_plates = ['WTX4521', 'LAS1090', 'COS3318']
for d in range(7, -1, -1):
    n_events = 14 if d else 10
    for k in range(n_events):
        gate = 'gate1' if k % 3 else 'gate4'
        hh = 6 + (k * 11) // n_events
        roll = random.random()
        if roll < 0.62:
            plate, (u, v, reg) = random.choice(known)
            entry = AccessLog.objects.create(vehicle=v, plate_number=plate, vehicle_type=v.vehicle_type,
                                             status='authorized', gate_id=gate, scanned_by=guards[0 if gate == 'gate1' else 1])
            AccessLog.objects.filter(pk=entry.pk).update(scanned_at=dt(d, hh, random.randint(0, 50)))
            if hh < 15 and (d or k < 5):
                ex = AccessLog.objects.create(vehicle=v, plate_number=plate, vehicle_type=v.vehicle_type,
                                              status='exited', gate_id=gate, paired_entry=entry,
                                              scanned_by=guards[0 if gate == 'gate1' else 1])
                AccessLog.objects.filter(pk=ex.pk).update(scanned_at=dt(d, min(hh + 4, 19), random.randint(0, 50)))
        elif roll < 0.72:
            plate = random.choice(sup_plates)
            AccessLog.objects.create(plate_number=plate, vehicle_type='truck', status='authorized', gate_id=gate,
                                     entrant_category='supplier')
            AccessLog.objects.filter(plate_number=plate, scanned_at__gte=now - timedelta(seconds=5)).update(scanned_at=dt(d, hh, 15))
        elif roll < 0.82:
            plate, (u, v, reg) = random.choice(known)
            e = AccessLog.objects.create(vehicle=v, plate_number=plate, vehicle_type=v.vehicle_type, status='wrong_day',
                                         gate_id=gate, denied_reason='Not a scheduled campus day for this pass')
            AccessLog.objects.filter(pk=e.pk).update(scanned_at=dt(d, hh, 30))
        elif roll < 0.93:
            plate = random.choice(['XYZ9876', 'KLM4432', 'PQR1209', 'TUV5550'])
            e = AccessLog.objects.create(plate_number=plate, vehicle_type='car', status='unknown', gate_id=gate,
                                         denied_reason='Plate not registered')
            AccessLog.objects.filter(pk=e.pk).update(scanned_at=dt(d, hh, 40))
        else:
            e = AccessLog.objects.create(plate_number='', vehicle_type='motorcycle', status='authorized', gate_id=gate,
                                         is_unrecognized=True, driver_name='RONALD PASCUAL', vehicle_color='Black',
                                         vehicle_model='Honda TMX', entry_note='No plate yet - showed OR/CR',
                                         entrant_category='visitor', scanned_by=guards[0], is_override=True,
                                         override_reason='Visitor to Registrar, OR/CR checked')
            AccessLog.objects.filter(pk=e.pk).update(scanned_at=dt(d, hh, 45))

# Visitor passes
office, _ = Office.objects.get_or_create(name="Registrar's Office")
Office.objects.get_or_create(name='Accounting Office')
VisitorPass.objects.all().delete()
vv, _ = Vehicle.objects.get_or_create(plate_number='HJK7021', defaults=dict(vehicle_type='car', model='Toyota Innova', color='Silver'))
VisitorPass.objects.create(vehicle=vv, plate_number='HJK7021', office=office, purpose='Transcript request',
                           issued_by=guards[0], printed_at=now - timedelta(minutes=40), expires_at=now + timedelta(minutes=20))

# ── Violations ────────────────────────────────────────────────────────────
Violation.objects.all().delete()
def vio(plate, vtype, days_ago, status='warning', notes='', offense=1, guard=None):
    u, v, reg = owners[plate]
    x = Violation.objects.create(vehicle=v, violation_type=vtype, notes=notes, offense_number=offense,
                                 status=status, issued_by=admin, on_duty_guard=guard,
                                 is_resolved=status in ('cleared', 'lifted'))
    Violation.objects.filter(pk=x.pk).update(issued_at=now - timedelta(days=days_ago, hours=3))
    return x
vio('123QWE', 'double_parking', 5, notes='Parked across bays M07 and M08 (camera auto-detected).', guard=guards[1])
vio('123QWE', 'time_exceed', 1, notes='Stayed 2h 40m past the allowed window.', offense=2, guard=guards[0])
vio('ABK5521', 'unauthorized_entry', 3, notes='Entered on a non-scheduled day.', guard=guards[0])
vio('NDA7788', 'double_parking', 8, status='cleared', notes='Settled with CDSO.')
vio('789UIO', 'time_exceed', 2, status='lifted', notes='Camera misread - false alarm.')
mark = owners['123QWE'][0]
mark.confiscation_level = 2; mark.confiscated_at = now - timedelta(days=1)
mark.confiscated_until = today + timedelta(days=13); mark.confiscation_reason = '2nd offence: Time Exceed'
mark.save()

# ── Audit trail & notifications ───────────────────────────────────────────
AuditLog.objects.all().delete()
entries = [
    (admin, 'user_created', guards[2], 'Created security account ANA GARCIA (Gate 1)'),
    (admin, 'updated', None, 'Updated System Settings: scan_dedup_seconds 60 -> 90'),
    (guards[0], 'guard_login', guards[0], 'Shift sign-in at Gate 1 (credentials)'),
    (guards[1], 'guard_login', guards[1], 'Shift sign-in at Gate 4 (QR badge)'),
    (admin, 'created', None, 'Accepted registration NDA 7788 (JOSEPHINE AQUINO)'),
    (guards[0], 'entry_override', None, 'Override: unregistered motorcycle, visitor to Registrar'),
    (guards[0], 'visitor_issued', None, "Visitor pass HJK 7021 -> Registrar's Office"),
    (admin, 'created', None, 'Created event "Research Congress 2026"'),
    (admin, 'updated', None, 'Rejected registration BXY 1180: outdated assessment form'),
    (admin, 'twofa_reset', guards[2], 'Two-factor reset requested by account holder'),
]
for k, (actor, action, target, details) in enumerate(entries):
    a = AuditLog.objects.create(actor=actor, action=action, target_user=target, details=details, ip_address='10.184.63.20')
    if a:
        AuditLog.objects.filter(pk=a.pk).update(created_at=now - timedelta(hours=(len(entries) - k) * 7))

Notification.objects.all().delete()
for cat, sev, title, msg, plate, link, read in [
    ('registration', 'info', 'New registration submitted', 'KEVIN DOMINGO submitted a student vehicle pass application.', 'BEE3307', '/admin/vehicles', False),
    ('violation', 'warning', '2nd offence recorded', 'MARK VILLANUEVA is confiscated for 2 weeks.', '123QWE', '/admin/violations', False),
    ('violation', 'info', 'Double parking detected', 'Gym Motorcycle Area, bays M07-M08.', '123QWE', '/admin/violations', True),
    ('registration', 'info', 'Receipt uploaded', 'GABRIEL TORRES uploaded an Official Receipt.', 'NGH2215', '/admin/vehicles', True),
]:
    Notification.objects.create(category=cat, severity=sev, title=title, message=msg, plate_number=plate, link=link, is_read=read)

print('seeded:', User.objects.count(), 'users,', VehicleRegistration.objects.count(), 'registrations,',
      AccessLog.objects.count(), 'access logs,', Violation.objects.count(), 'violations')

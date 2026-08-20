"""Approving a registration is all-or-nothing.

Acceptance writes three rows — the account, the vehicle, and the registration
itself. Without a transaction a failure between them committed the account and
nothing else, and that orphan was not harmless: it holds the applicant's email
under uniq_active_user_email, so the registration stays pending forever and
every retry is turned away with "already tied to an existing account". The
account is only reachable by an admin who knows to go looking for it.

The mail send is deliberately outside the transaction, so the last test here
pins the opposite guarantee: a dead SMTP server must NOT roll anything back.
"""
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from vehicles import views as vehicle_views
from vehicles.models import RegistrationPeriod, Vehicle, VehicleRegistration

LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'
DEAD_SMTP = dict(EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
                 EMAIL_HOST='127.0.0.1', EMAIL_PORT=1, EMAIL_TIMEOUT=2)

APPLICANT = dict(
    registrant_type='student',
    full_name='ATOMIC, TESTER',
    email='atomic@slc.edu.ph',
    plate_number='ATM 1001',
    vehicle_type='car',
    contact_number='+639171234567',
    address='San Fernando, La Union',
    drivers_license='N01-20-424242',
    student_id='20259999',
    program_year='BSIT 4',
    campus_days=['Monday'],
    student_level='college',
)


class _Boom(RuntimeError):
    """Stands in for anything that can fail mid-acceptance — in practice the
    vehicle upsert losing a race for the plate."""


@override_settings(EMAIL_BACKEND=LOCMEM, DEFAULT_FROM_EMAIL='slccdso@gmail.com')
class AcceptanceAtomicityTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        today = timezone.localdate()
        RegistrationPeriod.objects.create(
            label='Atomicity window', is_active=True,
            start_date=today - timedelta(days=1), end_date=today + timedelta(days=1))
        self.admin = User.objects.create_user(
            email='atomicadmin@slc.edu.ph', full_name='Atomic Admin',
            password='pw', role='admin', is_staff=True, is_superuser=True)

    def _submit(self, **over):
        payload = dict(APPLICANT)
        payload.update(over)
        res = self.client.post('/api/vehicles/register/open/', payload, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        return res.data['id']

    def _accept(self, reg_id):
        self.client.force_authenticate(user=self.admin)
        return self.client.post(f'/api/vehicles/registrations/{reg_id}/accept/',
                                {'or_number': '1234567'}, format='json')

    def _explode_on_vehicle_upsert(self):
        """Patch the vehicle step to fail, restoring it afterwards."""
        original = vehicle_views._upsert_vehicle_for_registration

        def boom(*args, **kwargs):
            raise _Boom('vehicle upsert failed')

        vehicle_views._upsert_vehicle_for_registration = boom
        self.addCleanup(setattr, vehicle_views,
                        '_upsert_vehicle_for_registration', original)

    def test_a_failure_mid_acceptance_leaves_no_orphan_account(self):
        reg_id = self._submit()
        self._explode_on_vehicle_upsert()

        with self.assertRaises(_Boom):
            self._accept(reg_id)

        self.assertFalse(
            User.objects.filter(email='atomic@slc.edu.ph').exists(),
            "the account was committed even though acceptance failed — it now "
            "holds the applicant's email and blocks them from re-registering")
        self.assertFalse(Vehicle.objects.filter(plate_number='ATM1001').exists())
        reg = VehicleRegistration.objects.get(pk=reg_id)
        self.assertEqual(reg.status, 'pending', 'the registration must stay reviewable')
        self.assertIsNone(reg.user)

    def test_the_applicant_can_still_be_accepted_after_a_failed_attempt(self):
        """The real cost of the orphan: without the rollback, this second
        attempt failed with 'User with this email already exists.'"""
        reg_id = self._submit()
        self._explode_on_vehicle_upsert()
        with self.assertRaises(_Boom):
            self._accept(reg_id)

        # Undo the patch and retry, exactly as CDSO would.
        self.doCleanups()

        res = self._accept(reg_id)
        self.assertEqual(res.status_code, 200, res.data)
        reg = VehicleRegistration.objects.get(pk=reg_id)
        self.assertEqual(reg.status, 'accepted')
        self.assertIsNotNone(reg.user)
        self.assertIsNotNone(reg.vehicle)

    def test_a_walk_in_failure_leaves_no_orphan_account(self):
        self._explode_on_vehicle_upsert()
        self.client.force_authenticate(user=self.admin)
        payload = dict(APPLICANT, or_number='7654321',
                       email='walkin@slc.edu.ph', plate_number='ATM 1002',
                       student_id='20259998', drivers_license='N01-20-424243')
        with self.assertRaises(_Boom):
            self.client.post('/api/vehicles/register/direct/', payload, format='json')

        self.assertFalse(User.objects.filter(email='walkin@slc.edu.ph').exists())
        self.assertFalse(
            VehicleRegistration.objects.filter(email='walkin@slc.edu.ph').exists(),
            'the walk-in registration row outlived the failed account creation')

    def test_a_mail_failure_does_not_roll_back_the_approval(self):
        """The opposite guarantee: the email is sent after the commit, so an
        unreachable SMTP server costs the owner their credentials email and
        nothing else."""
        reg_id = self._submit()
        with override_settings(**DEAD_SMTP):
            with self.assertLogs('vehicles.views', level='ERROR'):
                res = self._accept(reg_id)

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['email_status'], 'failed')
        reg = VehicleRegistration.objects.get(pk=reg_id)
        self.assertEqual(reg.status, 'accepted')
        self.assertIsNotNone(reg.user)
        self.assertIsNotNone(reg.vehicle)
        self.assertTrue(User.objects.filter(email='atomic@slc.edu.ph').exists())

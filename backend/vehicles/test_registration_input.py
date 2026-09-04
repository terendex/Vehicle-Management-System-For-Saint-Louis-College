"""What a registration submission is allowed to decide about itself.

/api/vehicles/register/open/ is an AllowAny endpoint whose payload used to be
handed to a `fields = '__all__'` ModelSerializer, so the form could set any
column on the row it was creating — including the ones the review process owns.
These tests pin the boundary: an applicant supplies their own details, and
nothing else.

The campus-day tests cover the second half of the same problem. Day names went
into a JSONField unchecked, so the only thing enforcing "real weekdays, at most
three of them" was the React form — which is not enforcement at all. An
applicant now registers for a whole rotation (MWF or TTHF) rather than for days
of their choosing, and these tests pin that the endpoint resolves to one whether
the payload names the rotation or names days.
"""
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from vehicles.campus_days import (ALL_DAYS, SCHEDULE_GROUP_DAYS,
                                  clean_campus_days, resolve_student_schedule,
                                  schedule_group)
from vehicles.models import RegistrationPeriod, VehicleRegistration

LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'

BASE = dict(
    registrant_type='student', full_name='SUBMIT, TESTER',
    email='submit@slc.edu.ph', plate_number='SUB 0001', vehicle_type='car',
    contact_number='+639171234567', address='San Fernando, La Union',
    drivers_license='N01-20-800001', student_id='20270001',
    program_year='BSIT 1', campus_days=['Monday'], student_level='college',
)


@override_settings(EMAIL_BACKEND=LOCMEM, DEFAULT_FROM_EMAIL='slccdso@gmail.com')
class RegistrationInputTestCase(TestCase):
    """Shared fixture: an open window, an admin, and unique-payload helpers."""

    def setUp(self):
        self.client = APIClient()
        self._n = 0
        today = timezone.localdate()
        RegistrationPeriod.objects.create(
            label='Input tests', is_active=True,
            start_date=today - timedelta(days=1), end_date=today + timedelta(days=1))
        self.admin = User.objects.create_user(
            email='inputadmin@slc.edu.ph', full_name='Input Admin',
            password='pw', role='admin', is_staff=True, is_superuser=True)

    def payload(self, **over):
        """A valid, conflict-free submission."""
        self._n += 1
        i = self._n
        data = dict(BASE)
        data.update(email=f'submit{i}@slc.edu.ph', plate_number=f'SUB {i:04d}',
                    student_id=f'2027{i:04d}', drivers_license=f'N01-20-80{i:04d}')
        data.update(over)
        return data

    def submit(self, **over):
        return self.client.post('/api/vehicles/register/open/',
                                self.payload(**over), format='json')

    def submit_ok(self, **over):
        res = self.submit(**over)
        self.assertEqual(res.status_code, 201, res.data)
        return VehicleRegistration.objects.get(pk=res.data['id'])


class ReviewFieldsAreNotApplicantWritableTests(RegistrationInputTestCase):
    """Fields the CDSO review owns must ignore whatever the payload says."""

    def test_a_submission_cannot_approve_itself(self):
        reg = self.submit_ok(status=VehicleRegistration.Status.ACCEPTED)
        self.assertEqual(reg.status, VehicleRegistration.Status.PENDING,
                         'a public submission approved itself, skipping CDSO review')

    def test_a_submission_cannot_mark_itself_rejected(self):
        reg = self.submit_ok(status=VehicleRegistration.Status.REJECTED,
                             rejection_reason='n/a')
        self.assertEqual(reg.status, VehicleRegistration.Status.PENDING)
        self.assertEqual(reg.rejection_reason, '')

    def test_a_submission_cannot_attach_itself_to_an_account(self):
        reg = self.submit_ok(user=self.admin.pk)
        self.assertIsNone(reg.user_id,
                          "a submission linked itself to somebody else's account")

    def test_a_submission_cannot_claim_a_vehicle(self):
        from vehicles.models import Vehicle
        owned = Vehicle.objects.create(plate_number='OWNED001', user=self.admin)
        reg = self.submit_ok(vehicle=owned.pk)
        self.assertIsNone(reg.vehicle_id)

    def test_a_submission_cannot_invent_an_or_number(self):
        reg = self.submit_ok(or_number='9999999')
        self.assertEqual(reg.or_number, '',
                         'an OR number is proof of payment — only CDSO may set it')

    def test_a_submission_cannot_claim_a_system_id(self):
        """system_student_id is unique; a squatted value would make the next
        genuine approval collide on it."""
        reg = self.submit_ok(system_student_id='SLC-STU-000001',
                             system_employee_id='SLC-EMP-000001')
        self.assertIsNone(reg.system_student_id)
        self.assertIsNone(reg.system_employee_id)

    def test_a_submission_cannot_grant_itself_special_case_status(self):
        reg = self.submit_ok(is_special_case=True, special_case_reason='self-granted')
        self.assertFalse(reg.is_special_case)
        self.assertEqual(reg.special_case_reason, '')

    def test_a_submission_cannot_forge_its_source_or_review_time(self):
        reg = self.submit_ok(source=VehicleRegistration.Source.DIRECT,
                             reviewed_at=timezone.now().isoformat())
        self.assertEqual(reg.source, VehicleRegistration.Source.PUBLIC)
        self.assertIsNone(reg.reviewed_at)

    def test_a_submission_cannot_change_its_registrant_type_past_the_gate(self):
        """registrant_type is validated against the allow-list in the view and
        then applied there; the payload copy must not win."""
        res = self.client.post('/api/vehicles/register/open/',
                               self.payload(registrant_type='admin'), format='json')
        self.assertEqual(res.status_code, 400)

    def test_the_normal_fields_still_go_through(self):
        """The lock-down must not cost the applicant their own details."""
        reg = self.submit_ok(vehicle_color='Blue', program_year='BSIT 4')
        self.assertEqual(reg.vehicle_color, 'Blue')
        self.assertEqual(reg.program_year, 'BSIT 4')
        self.assertEqual(reg.status, VehicleRegistration.Status.PENDING)

    def test_withheld_fields_are_dropped_even_when_a_payload_sends_them(self):
        """TEMPORARY — Data Privacy Office trial.

        The columns still exist and the serializer would happily write them, so
        the only thing stopping a stale bundle (or a hand-rolled POST) from
        filing the data the DPO asked us to stop collecting is the strip in the
        view. Pin it: a submission carrying every withheld field must store none
        of them, and must still be accepted rather than 400'd — the applicant is
        not at fault for a client that has not caught up.
        """
        reg = self.submit_ok(address='Bauang, La Union',
                             contact_number='+639171234567',
                             age=21, student_id='12345678',
                             employee_id='87654321',
                             driver_contact='+639179876543')
        self.assertEqual(reg.address, '')
        self.assertEqual(reg.contact_number, '')
        self.assertIsNone(reg.age)
        self.assertEqual(reg.student_id, '')
        self.assertEqual(reg.employee_id, '')
        self.assertEqual(reg.driver_contact, '')
        self.assertEqual(reg.status, VehicleRegistration.Status.PENDING)


class CampusDayValidationTests(RegistrationInputTestCase):
    def test_unknown_day_names_are_rejected(self):
        res = self.submit(campus_days=['Funday', 'Sunday'])
        self.assertEqual(res.status_code, 400)
        self.assertIn('Not a campus day', res.data['error'])

    def test_a_real_day_mixed_with_a_fake_one_is_still_rejected(self):
        res = self.submit(campus_days=['Monday', 'DROP TABLE'])
        self.assertEqual(res.status_code, 400)

    def test_days_from_both_rotations_are_rejected(self):
        """The form offers two rotations; a payload combining them is asking
        for a week that no schedule grants."""
        res = self.submit(campus_days=list(ALL_DAYS))
        self.assertEqual(res.status_code, 400)
        self.assertIn('one rotation', res.data['error'])

    def test_sped_students_get_every_campus_day(self):
        reg = self.submit_ok(student_level='sped', campus_days=list(ALL_DAYS),
                             driver_name='Parent Name', driver_relationship='parent')
        self.assertEqual(len(reg.campus_days), len(ALL_DAYS))

    def test_no_schedule_at_all_is_rejected(self):
        res = self.submit(campus_days=[])
        self.assertEqual(res.status_code, 400)

    def test_choosing_a_rotation_books_the_whole_week(self):
        """The point of the change: a pass issued as MWF admits all three days,
        so the stored days can't be a subset of the schedule printed on it."""
        reg = self.submit_ok(schedule='MWF', campus_days=[])
        self.assertEqual(reg.schedule, 'MWF')
        self.assertEqual(reg.campus_days, SCHEDULE_GROUP_DAYS['MWF'])

    def test_the_other_rotation(self):
        reg = self.submit_ok(schedule='TTHF', campus_days=[])
        self.assertEqual(reg.schedule, 'TTHF')
        self.assertEqual(reg.campus_days, SCHEDULE_GROUP_DAYS['TTHF'])

    def test_loose_days_are_snapped_up_to_their_rotation(self):
        """Older clients (and direct API callers) send days, not a rotation —
        they must not be able to buy a cheaper, partial week."""
        reg = self.submit_ok(campus_days=['Wednesday', 'Monday', 'Monday'])
        self.assertEqual(reg.schedule, 'MWF')
        self.assertEqual(reg.campus_days, SCHEDULE_GROUP_DAYS['MWF'])

    def test_employees_never_carry_campus_days(self):
        reg = self.submit_ok(registrant_type='employee', student_id='',
                             program_year='', student_level='',
                             employee_id='E-7001', department='Teaching',
                             campus_days=['Monday', 'Tuesday'])
        self.assertEqual(reg.campus_days, [])
        self.assertEqual(reg.schedule, 'ANY')


class ScheduleGroupTests(TestCase):
    """One rule, shared by the form, the CDSO override and the admin create."""

    def test_partial_weeks_keep_their_rotation(self):
        self.assertEqual(schedule_group(['Monday']), 'MWF')
        self.assertEqual(schedule_group(['Monday', 'Wednesday']), 'MWF')
        self.assertEqual(schedule_group(['Tuesday']), 'TTHF')
        self.assertEqual(schedule_group(['Thursday', 'Friday']), 'TTHF')

    def test_full_weeks(self):
        self.assertEqual(schedule_group(['Monday', 'Wednesday', 'Friday']), 'MWF')
        self.assertEqual(schedule_group(['Tuesday', 'Thursday', 'Friday']), 'TTHF')

    def test_friday_alone_is_ambiguous_and_resolves_to_mwf(self):
        """Friday is on both rotations. The answer only has to be stable —
        the form names its rotation outright and never relies on this."""
        self.assertEqual(schedule_group(['Friday']), 'MWF')

    def test_saturday_belongs_to_no_student_rotation(self):
        """Saturday is still a campus day CDSO can assign, but it is not part
        of MWF or TTHF, so a Saturday student is a custom case."""
        self.assertEqual(schedule_group(['Saturday']), 'MIXED')
        self.assertEqual(schedule_group(['Tuesday', 'Saturday']), 'MIXED')

    def test_straddling_both_rotations_is_mixed(self):
        self.assertEqual(schedule_group(['Monday', 'Tuesday']), 'MIXED')
        self.assertEqual(schedule_group(ALL_DAYS), 'MIXED')

    def test_no_days_is_any(self):
        self.assertEqual(schedule_group([]), 'ANY')
        self.assertEqual(schedule_group(None), 'ANY')

    def test_clean_campus_days_reports_what_it_dropped(self):
        cleaned, rejected = clean_campus_days(['monday', ' FRIDAY ', 'Funday', 7])
        self.assertEqual(cleaned, ['Monday', 'Friday'])
        self.assertEqual(rejected, ['Funday', 7])

    def test_clean_campus_days_survives_a_non_list(self):
        self.assertEqual(clean_campus_days('Monday'), ([], []))
        self.assertEqual(clean_campus_days(None), ([], []))


class ResolveStudentScheduleTests(TestCase):
    """A student registers for a rotation, so whatever the payload says has to
    come out as one of exactly two weeks (or SpEd's six days)."""

    def test_a_named_rotation_expands_to_its_week(self):
        for code, days in SCHEDULE_GROUP_DAYS.items():
            got_days, got_code, err = resolve_student_schedule(code, [], 'college')
            self.assertIsNone(err)
            self.assertEqual(got_code, code)
            self.assertEqual(got_days, days)

    def test_the_named_rotation_beats_stale_days_in_the_payload(self):
        days, code, err = resolve_student_schedule('TTHF', ['Monday'], 'college')
        self.assertIsNone(err)
        self.assertEqual(code, 'TTHF')
        self.assertEqual(days, SCHEDULE_GROUP_DAYS['TTHF'])

    def test_a_partial_week_is_filled_out(self):
        days, code, err = resolve_student_schedule('', ['Thursday'], 'college')
        self.assertIsNone(err)
        self.assertEqual(code, 'TTHF')
        self.assertEqual(days, SCHEDULE_GROUP_DAYS['TTHF'])

    def test_an_unknown_code_falls_back_to_the_days(self):
        days, code, err = resolve_student_schedule('WHENEVER', ['Wednesday'], 'college')
        self.assertIsNone(err)
        self.assertEqual(code, 'MWF')

    def test_saturday_is_no_longer_a_rotation_students_can_register_for(self):
        _, _, err = resolve_student_schedule('', ['Tuesday', 'Saturday'], 'college')
        self.assertIn('one rotation', err)

    def test_straddling_rotations_is_an_error_not_a_guess(self):
        days, code, err = resolve_student_schedule('MIXED', ['Monday', 'Tuesday'], 'college')
        self.assertEqual(days, [])
        self.assertIn('one rotation', err)

    def test_nothing_chosen_is_an_error(self):
        _, _, err = resolve_student_schedule('', [], 'college')
        self.assertIn('must choose a schedule', err)

    def test_bogus_days_are_named_back(self):
        _, _, err = resolve_student_schedule('', ['Funday'], 'college')
        self.assertIn('Not a campus day', err)

    def test_sped_takes_every_campus_day_whatever_was_sent(self):
        days, _, err = resolve_student_schedule('MWF', ['Monday'], 'sped')
        self.assertIsNone(err)
        self.assertEqual(days, list(ALL_DAYS))


class ScheduleGroupIsConsistentAcrossEntryPointsTests(RegistrationInputTestCase):
    """The bug this guards: the same two days produced MIXED online and MWF
    when a CDSO officer keyed them in by hand."""

    def test_online_and_cdso_override_agree(self):
        online = self.submit_ok(campus_days=['Monday', 'Wednesday'])

        other = self.submit_ok(campus_days=['Monday'])
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(f'/api/vehicles/registrations/{other.pk}/accept/',
                               {'or_number': '1234567',
                                'campus_days': ['Monday', 'Wednesday']}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        other.refresh_from_db()

        self.assertEqual(online.schedule, other.schedule,
                         'the online form and the CDSO override disagree about '
                         'which schedule group Monday+Wednesday is')
        self.assertEqual(online.schedule, 'MWF')

    def test_cdso_override_rejects_unknown_days(self):
        reg = self.submit_ok()
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(f'/api/vehicles/registrations/{reg.pk}/accept/',
                               {'or_number': '1234567',
                                'campus_days': ['Monday', 'Funday']}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        reg.refresh_from_db()
        self.assertEqual(reg.campus_days, ['Monday'], 'a bogus day was stored')


class BanIsRecheckedAtApprovalTests(RegistrationInputTestCase):
    """Approval can be days after submission — the applicant may have hit the
    violation ceiling in between."""

    def _ban(self, email):
        banned = User.objects.create_user(
            email='banned-owner@slc.edu.ph', full_name='BANNED, OWNER',
            password='pw', role='vehicle_owner')
        banned.registration_banned = True
        banned.save(update_fields=['registration_banned'])
        VehicleRegistration.objects.create(
            registrant_type='student', full_name='BANNED, OWNER', email=email,
            plate_number='BANNED01', vehicle_type='car', user=banned,
            status=VehicleRegistration.Status.EXPIRED)

    def test_a_pending_applicant_banned_after_submitting_cannot_be_accepted(self):
        reg = self.submit_ok()
        self._ban(reg.email)

        self.client.force_authenticate(user=self.admin)
        res = self.client.post(f'/api/vehicles/registrations/{reg.pk}/accept/',
                               {'or_number': '1234567'}, format='json')
        self.assertEqual(res.status_code, 403, res.data)
        self.assertTrue(res.data.get('registration_banned'))

        reg.refresh_from_db()
        self.assertEqual(reg.status, VehicleRegistration.Status.PENDING)
        self.assertIsNone(reg.user_id, 'an account was created for a banned applicant')

    def test_an_unbanned_applicant_is_unaffected(self):
        reg = self.submit_ok()
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(f'/api/vehicles/registrations/{reg.pk}/accept/',
                               {'or_number': '1234567'}, format='json')
        self.assertEqual(res.status_code, 200, res.data)


class WalkInRegistrationStillWorksTests(RegistrationInputTestCase):
    """The read_only lock-down must not break the CDSO walk-in path, which
    legitimately creates an already-accepted registration."""

    def _direct(self, **over):
        self.client.force_authenticate(user=self.admin)
        payload = self.payload(or_number='7654321', **over)
        return payload, self.client.post('/api/vehicles/register/direct/',
                                         payload, format='json')

    def test_walk_in_is_accepted_with_its_or_number(self):
        payload, res = self._direct()
        self.assertEqual(res.status_code, 201, res.data)

        reg = VehicleRegistration.objects.get(email=payload['email'])
        self.assertEqual(reg.status, VehicleRegistration.Status.ACCEPTED)
        self.assertEqual(reg.source, VehicleRegistration.Source.DIRECT)
        self.assertEqual(reg.or_number, '7654321')
        self.assertIsNotNone(reg.user_id)
        self.assertIsNotNone(reg.vehicle_id)
        self.assertIsNotNone(reg.reviewed_at)
        self.assertTrue(reg.system_student_id)

    def test_walk_in_derives_the_schedule_from_the_days_given(self):
        """This path read `schedule` back off the row and otherwise defaulted
        every student to MWF — so Tuesday/Thursday days were filed as MWF."""
        payload, res = self._direct(campus_days=['Tuesday', 'Thursday'])
        self.assertEqual(res.status_code, 201, res.data)
        reg = VehicleRegistration.objects.get(email=payload['email'])
        self.assertEqual(reg.schedule, 'TTHF')
        self.assertEqual(reg.user.schedule, 'TTHF')
        # The walk-in path is CDSO keying in exactly what they mean, so unlike
        # the public form the days are kept as given, not filled out to a week.
        self.assertEqual(reg.user.campus_days, ['Tuesday', 'Thursday'])

    def test_walk_in_rejects_unknown_days(self):
        _, res = self._direct(campus_days=['Monday', 'Funday'])
        self.assertEqual(res.status_code, 400)
        self.assertIn('Not a campus day', res.data['error'])

    def test_walk_in_employee_carries_no_campus_days(self):
        payload, res = self._direct(registrant_type='employee', student_id='',
                                    program_year='', student_level='',
                                    employee_id='E-8001',
                                    campus_days=['Monday', 'Tuesday'])
        self.assertEqual(res.status_code, 201, res.data)
        reg = VehicleRegistration.objects.get(email=payload['email'])
        self.assertEqual(reg.campus_days, [])
        self.assertEqual(reg.schedule, 'ANY')

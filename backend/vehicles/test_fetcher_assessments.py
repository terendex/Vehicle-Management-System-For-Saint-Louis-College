"""Per-student enrolment proof on a fetcher registration.

A fetcher is not enrolled anywhere, so their own assessment slot is always
empty — what proves the trip is legitimate is a form for each student they
collect. Those files arrive at the shared document endpoint as
`fetcher_assessment_<index>`, the index being the position in fetcher_students,
and that pairing is the whole feature: get it wrong and the reviewer sees a
document filed against the wrong child, or none at all.

The email tests cover the other half of the same gap — a fetcher's confirmation
used to carry nothing specific to their application, and their approval email
labelled their blank columns "Employee ID" and "Department".
"""
from datetime import timedelta

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from vehicles.email_utils import send_acceptance_email, send_pending_email
from vehicles.models import (FetcherStudentAssessment, RegistrationPeriod,
                             VehicleRegistration)

LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'

STUDENTS = [
    {'full_name': 'DELA CRUZ, JUAN', 'student_id': '23100174',
     'student_level': 'jhs', 'program_year': 'Grade 7'},
    {'full_name': 'DELA CRUZ, MARIA', 'student_id': '23100175',
     'student_level': 'elementary', 'program_year': 'Grade 4'},
]


def a_file(name='assessment.pdf'):
    return SimpleUploadedFile(name, b'%PDF-1.4 assessment', content_type='application/pdf')


@override_settings(EMAIL_BACKEND=LOCMEM, DEFAULT_FROM_EMAIL='slccdso@gmail.com')
class FetcherAssessmentUploadTests(TestCase):
    """The upload endpoint's half: which files land against which student."""

    URL = '/api/vehicles/register/documents/'

    def setUp(self):
        self.client = APIClient()
        today = timezone.localdate()
        RegistrationPeriod.objects.create(
            label='Fetcher assessment tests', is_active=True,
            start_date=today - timedelta(days=1), end_date=today + timedelta(days=1))
        self.reg = VehicleRegistration.objects.create(
            registrant_type='fetcher', full_name='FETCHER, PARENT',
            email='fetcher-assess@example.com', plate_number='FTA 0001',
            vehicle_type='car', contact_number='+639171234567',
            address='San Fernando, La Union', drivers_license='N01-20-900001',
            fetcher_type='drop_and_go', fetcher_students=list(STUDENTS),
            status=VehicleRegistration.Status.PENDING,
        )

    def _cleanup(self, assessment):
        self.addCleanup(assessment.assessment_form.delete, save=False)

    def test_file_lands_against_the_student_at_that_index(self):
        res = self.client.post(self.URL, {
            'registration_id': self.reg.id,
            'email': self.reg.email,
            'fetcher_assessment_1': a_file(),
        }, format='multipart')
        self.assertEqual(res.status_code, 200)

        rows = list(self.reg.fetcher_assessments.all())
        self.assertEqual(len(rows), 1)
        self._cleanup(rows[0])
        self.assertEqual(rows[0].student_index, 1)
        self.assertEqual(rows[0].student_name(), 'DELA CRUZ, MARIA')

    def test_one_file_per_student(self):
        res = self.client.post(self.URL, {
            'registration_id': self.reg.id,
            'email': self.reg.email,
            'fetcher_assessment_0': a_file('first.pdf'),
            'fetcher_assessment_1': a_file('second.pdf'),
        }, format='multipart')
        self.assertEqual(res.status_code, 200)

        rows = list(self.reg.fetcher_assessments.all())
        for row in rows:
            self._cleanup(row)
        self.assertEqual([r.student_index for r in rows], [0, 1])

    def test_reupload_replaces_rather_than_duplicating(self):
        """A retried submit must not leave the reviewer two forms for one child."""
        for name in ('first.pdf', 'second.pdf'):
            res = self.client.post(self.URL, {
                'registration_id': self.reg.id,
                'email': self.reg.email,
                'fetcher_assessment_0': a_file(name),
            }, format='multipart')
            self.assertEqual(res.status_code, 200)

        rows = list(self.reg.fetcher_assessments.all())
        self.assertEqual(len(rows), 1)
        self._cleanup(rows[0])
        self.assertIn('second', rows[0].assessment_form.name)

    def test_index_with_no_student_behind_it_is_rejected(self):
        res = self.client.post(self.URL, {
            'registration_id': self.reg.id,
            'email': self.reg.email,
            'fetcher_assessment_7': a_file(),
        }, format='multipart')
        self.assertEqual(res.status_code, 400)
        self.assertFalse(FetcherStudentAssessment.objects.exists())

    def test_non_document_extension_is_rejected(self):
        res = self.client.post(self.URL, {
            'registration_id': self.reg.id,
            'email': self.reg.email,
            'fetcher_assessment_0': SimpleUploadedFile(
                'payload.exe', b'MZ', content_type='application/octet-stream'),
        }, format='multipart')
        self.assertEqual(res.status_code, 400)
        self.assertFalse(FetcherStudentAssessment.objects.exists())

    def test_a_request_carrying_no_file_is_still_a_client_bug(self):
        res = self.client.post(self.URL, {
            'registration_id': self.reg.id,
            'email': self.reg.email,
        }, format='multipart')
        self.assertEqual(res.status_code, 400)

    def test_review_payload_pairs_each_document_with_its_student(self):
        """The reviewer reads these per student, so the URL rides on the entry."""
        from vehicles.serializers import VehicleRegistrationSerializer

        self.client.post(self.URL, {
            'registration_id': self.reg.id,
            'email': self.reg.email,
            'fetcher_assessment_0': a_file(),
        }, format='multipart')
        row = self.reg.fetcher_assessments.get()
        self._cleanup(row)

        self.reg.refresh_from_db()
        students = VehicleRegistrationSerializer(self.reg).data['fetcher_students']
        self.assertTrue(students[0]['assessment_form'])
        self.assertIsNone(students[1]['assessment_form'])


@override_settings(EMAIL_BACKEND=LOCMEM, DEFAULT_FROM_EMAIL='slccdso@gmail.com')
class FetcherEmailTests(TestCase):
    """A fetcher's emails should describe a fetcher, not an employee with gaps."""

    def setUp(self):
        self.reg = VehicleRegistration.objects.create(
            registrant_type='fetcher', full_name='FETCHER, PARENT',
            email='fetcher-mail@example.com', plate_number='FTM 0001',
            vehicle_type='car', contact_number='+639171234567',
            address='San Fernando, La Union', drivers_license='N01-20-900002',
            fetcher_type='standby', fetcher_students=list(STUDENTS),
            status=VehicleRegistration.Status.PENDING,
        )

    def test_pending_email_lists_classification_and_students(self):
        send_pending_email(self.reg)
        body = mail.outbox[-1].alternatives[0][0]
        self.assertIn('Standby', body)
        self.assertIn('DELA CRUZ, JUAN', body)
        self.assertIn('DELA CRUZ, MARIA', body)
        self.assertIn('23100174', body)

    def test_pending_plain_text_lists_the_students_too(self):
        send_pending_email(self.reg)
        self.assertIn('Students to fetch:', mail.outbox[-1].body)
        self.assertIn('DELA CRUZ, JUAN', mail.outbox[-1].body)

    def test_acceptance_email_does_not_call_a_fetcher_an_employee(self):
        send_acceptance_email(self.reg, 'temp-pass-123', user_code='VO-0001')
        body = mail.outbox[-1].alternatives[0][0]
        self.assertNotIn('Employee ID', body)
        self.assertNotIn('Department', body)
        self.assertIn('Classification', body)
        self.assertIn('DELA CRUZ, JUAN', body)

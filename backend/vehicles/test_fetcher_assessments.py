"""What a fetcher registration carries — TEMPORARY, Data Privacy Office trial.

This file used to cover the per-student enrolment proof: one assessment form per
student a fetcher collects, arriving at the shared document endpoint as
`fetcher_assessment_<index>`, the index being the position in fetcher_students.
That pairing was the whole feature, and getting it wrong filed a document
against the wrong child.

Nothing is uploaded any more. The DPO's instruction was that a copy of the
driver's licence need not be collected, and the rest of the attachments were
withdrawn with it, so what is left to pin is the shape of the closure: the
endpoint answers plainly instead of accepting a file, and no student ID rides
along in the review payload or the emails. The upload tests are kept as their
inverse rather than deleted — they are what a revert has to make pass again.

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

# The ID is still written into the JSON here on purpose: rows filed before the
# trial carry one, and neither the review payload nor the emails may show it.
STUDENTS = [
    {'full_name': 'DELA CRUZ, JUAN', 'student_id': '23100174',
     'student_level': 'jhs', 'program_year': 'Grade 7'},
    {'full_name': 'DELA CRUZ, MARIA', 'student_id': '23100175',
     'student_level': 'elementary', 'program_year': 'Grade 4'},
]


def a_file(name='assessment.pdf'):
    return SimpleUploadedFile(name, b'%PDF-1.4 assessment', content_type='application/pdf')


@override_settings(EMAIL_BACKEND=LOCMEM, DEFAULT_FROM_EMAIL='slccdso@gmail.com')
class DocumentUploadIsClosedTests(TestCase):
    """The endpoint is kept and refuses, rather than removed.

    A browser still running the previous bundle would read a 404 as a network
    fault and retry, and the retry would be an upload we must not accept — so
    the route answers 410 Gone and says why.
    """

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
            vehicle_type='car', drivers_license='N01-20-900001',
            fetcher_type='drop_and_go', fetcher_students=list(STUDENTS),
            status=VehicleRegistration.Status.PENDING,
        )

    def _post(self, **extra):
        payload = {'registration_id': self.reg.id, 'email': self.reg.email}
        payload.update(extra)
        return self.client.post(self.URL, payload, format='multipart')

    def test_a_licence_photo_is_refused(self):
        res = self._post(image=SimpleUploadedFile(
            'licence.jpg', b'\xff\xd8\xff', content_type='image/jpeg'))
        self.assertEqual(res.status_code, 410)
        self.assertTrue(res.data['uploads_disabled'])

    def test_an_assessment_form_is_refused(self):
        res = self._post(assessment_form=a_file())
        self.assertEqual(res.status_code, 410)

    def test_a_per_student_assessment_is_refused_and_stores_nothing(self):
        res = self._post(fetcher_assessment_0=a_file('first.pdf'),
                         fetcher_assessment_1=a_file('second.pdf'))
        self.assertEqual(res.status_code, 410)
        self.assertFalse(FetcherStudentAssessment.objects.exists())

    def test_the_refusal_tells_the_applicant_nothing_more_is_needed(self):
        """A retried upload must not read as an application left unfinished."""
        res = self._post(fetcher_assessment_0=a_file())
        self.assertIn('no longer collected', res.data['error'])
        self.assertIn('CDSO Office', res.data['error'])

    def test_review_payload_carries_no_document_and_no_student_id(self):
        """The reviewer reads names and levels; the ID and the file are gone."""
        from vehicles.serializers import VehicleRegistrationSerializer

        data = VehicleRegistrationSerializer(self.reg).data
        students = data['fetcher_students']
        self.assertEqual([s['full_name'] for s in students],
                         ['DELA CRUZ, JUAN', 'DELA CRUZ, MARIA'])
        for student in students:
            self.assertNotIn('student_id', student)
            self.assertNotIn('assessment_form', student)
        self.assertIsNone(data['drivers_license_image'])
        self.assertIsNone(data['assessment_form'])
        self.assertIsNone(data['or_receipt_image'])
        # The applicant's own withheld fields go the same way.
        for name in ('address', 'contact_number', 'age',
                     'student_id', 'employee_id', 'driver_contact'):
            self.assertNotIn(name, data)


@override_settings(EMAIL_BACKEND=LOCMEM, DEFAULT_FROM_EMAIL='slccdso@gmail.com')
class FetcherEmailTests(TestCase):
    """A fetcher's emails should describe a fetcher, not an employee with gaps."""

    def setUp(self):
        self.reg = VehicleRegistration.objects.create(
            registrant_type='fetcher', full_name='FETCHER, PARENT',
            email='fetcher-mail@example.com', plate_number='FTM 0001',
            vehicle_type='car', drivers_license='N01-20-900002',
            fetcher_type='standby', fetcher_students=list(STUDENTS),
            status=VehicleRegistration.Status.PENDING,
        )

    def test_pending_email_lists_classification_and_students(self):
        send_pending_email(self.reg)
        body = mail.outbox[-1].alternatives[0][0]
        self.assertIn('Standby', body)
        self.assertIn('DELA CRUZ, JUAN', body)
        self.assertIn('DELA CRUZ, MARIA', body)

    def test_pending_email_does_not_print_a_student_id(self):
        """TEMPORARY (DPO trial) — the row exists but the number is withheld."""
        send_pending_email(self.reg)
        self.assertNotIn('23100174', mail.outbox[-1].alternatives[0][0])
        self.assertNotIn('23100174', mail.outbox[-1].body)

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


@override_settings(EMAIL_BACKEND=LOCMEM, DEFAULT_FROM_EMAIL='slccdso@gmail.com')
class WithheldFieldsAreAbsentFromEmailsTests(TestCase):
    """TEMPORARY — Data Privacy Office trial.

    Rows filed before the trial still hold an address, a contact number and an
    ID. The emails are rebuilt from the row every time they are sent, so a
    legacy row is exactly the case that would put the withheld data back in
    front of someone.
    """

    def setUp(self):
        self.reg = VehicleRegistration.objects.create(
            registrant_type='student', full_name='LEGACY, TESTER',
            email='legacy-test@example.com', plate_number='LEG 0001',
            vehicle_type='car', drivers_license='N01-20-900004',
            # Pre-trial values, as an older row would carry them.
            address='123 Rizal Street, San Fernando, La Union',
            contact_number='+639171234567', age=21, student_id='23100999',
            driver_name='DELA CRUZ, PEDRO', driver_relationship='parent',
            driver_contact='+639179876543',
            program_year='BSIT - 4', student_level='college',
            campus_days=['Monday'], status=VehicleRegistration.Status.PENDING,
        )

    def _assert_withheld(self, text):
        for value in ('Rizal Street', '+639171234567', '+639179876543',
                      '23100999'):
            self.assertNotIn(value, text)
        for label in ('Address', 'Contact No.', 'Student ID'):
            self.assertNotIn(label, text)

    def test_pending_email_withholds_them(self):
        send_pending_email(self.reg)
        self._assert_withheld(mail.outbox[-1].alternatives[0][0])

    def test_acceptance_email_withholds_them(self):
        send_acceptance_email(self.reg, 'temp-pass-123', user_code='VO-0002')
        body = mail.outbox[-1].alternatives[0][0]
        self._assert_withheld(body)
        # The authorized driver is still named — only their number is withheld.
        self.assertIn('DELA CRUZ, PEDRO', body)

    def test_the_pdf_still_builds_for_a_legacy_row(self):
        """The rows the trial dropped were part of every section it prints."""
        from registration_pdf import registration_confirmation_pdf

        for pending in (True, False):
            with self.subTest(pending=pending):
                pdf = registration_confirmation_pdf(
                    self.reg, include_documents=True, pending=pending)
                self.assertTrue(pdf.startswith(b'%PDF'))


@override_settings(EMAIL_BACKEND=LOCMEM, DEFAULT_FROM_EMAIL='slccdso@gmail.com')
class PendingAcknowledgementPdfTests(TestCase):
    """The registration-received email carries proof of application.

    The approval PDF states that a pass was granted, so it cannot stand in for
    this one — an applicant asked "did you register?" before CDSO has decided
    had nothing to show but an inbox.
    """

    def setUp(self):
        self.reg = VehicleRegistration.objects.create(
            registrant_type='student', full_name='ACK, TESTER',
            email='ack-test@example.com', plate_number='ACK 0001',
            vehicle_type='car', drivers_license='N01-20-900003',
            program_year='BSIT - 4',
            student_level='college', campus_days=['Monday'],
            status=VehicleRegistration.Status.PENDING,
        )

    def test_pending_email_attaches_a_pdf(self):
        send_pending_email(self.reg)
        attachments = mail.outbox[-1].attachments
        self.assertEqual(len(attachments), 1)
        name, content, mimetype = attachments[0]
        self.assertTrue(name.endswith('.pdf'))
        self.assertEqual(mimetype, 'application/pdf')
        self.assertTrue(content.startswith(b'%PDF'))

    def test_acknowledgement_is_not_named_like_the_pass(self):
        from registration_pdf import registration_pdf_filename

        self.assertNotEqual(registration_pdf_filename(self.reg, pending=True),
                            registration_pdf_filename(self.reg))
        self.assertIn('Acknowledgement',
                      registration_pdf_filename(self.reg, pending=True))

    def test_a_broken_pdf_does_not_cost_the_applicant_the_email(self):
        """The submission is already saved; a build failure must not raise."""
        import registration_pdf

        def broken(*a, **kw):
            raise RuntimeError('no reportlab today')

        original = registration_pdf.registration_confirmation_pdf
        registration_pdf.registration_confirmation_pdf = broken
        try:
            send_pending_email(self.reg)
        finally:
            registration_pdf.registration_confirmation_pdf = original

        self.assertEqual(len(mail.outbox[-1].attachments), 0)
        self.assertIn('REG-', mail.outbox[-1].subject)

"""Printing an approved registration from the review page.

The printed copy used to be issued from User Management, which meant leaving
the registration you were looking at to find the owner's account. It is issued
here now, and the CDSO's copy carries the scans the applicant uploaded so the
filed paper holds the evidence the approval was based on.
"""
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from registration_pdf import _read_document, registration_confirmation_pdf
from vehicles.models import VehicleRegistration

User = get_user_model()


def _jpeg(size=(240, 160)):
    from PIL import Image
    buf = BytesIO()
    Image.new('RGB', size, (200, 210, 220)).save(buf, format='JPEG')
    return buf.getvalue()


def _registration(**kwargs):
    fields = dict(
        registrant_type='student', full_name='DELA CRUZ, JUAN',
        email='print-test@slc.edu.ph', vehicle_type='Motorcycle',
        plate_number='PRT1234', status='accepted', or_number='1234567',
    )
    fields.update(kwargs)
    return VehicleRegistration.objects.create(**fields)


class ReadDocumentTests(TestCase):
    """What can be drawn, and what has to be named instead."""

    def test_absent_file_reports_not_provided(self):
        data, note = _read_document(None)
        self.assertIsNone(data)
        self.assertEqual(note, 'Not provided')

    def test_image_is_returned_for_drawing(self):
        reg = _registration(
            or_receipt_image=SimpleUploadedFile('r.jpg', _jpeg(), content_type='image/jpeg'))
        self.addCleanup(reg.or_receipt_image.delete, save=False)
        data, note = _read_document(reg.or_receipt_image)
        self.assertIsNone(note)
        self.assertTrue(data.startswith(b'\xff\xd8'))

    def test_pdf_upload_is_named_rather_than_drawn(self):
        # A PDF cannot be embedded as a picture, but "submitted as a PDF" and
        # "nothing submitted" must not look the same on the printout.
        reg = _registration(
            assessment_form=SimpleUploadedFile('a.pdf', b'%PDF-1.4 ...',
                                               content_type='application/pdf'))
        self.addCleanup(reg.assessment_form.delete, save=False)
        data, note = _read_document(reg.assessment_form)
        self.assertIsNone(data)
        self.assertIn('PDF', note)

    def test_heic_upload_is_named_rather_than_drawn(self):
        # Pillow cannot decode an iPhone HEIC without a plugin; caught here so
        # it fails as a caption instead of an exception mid-build.
        reg = _registration(
            drivers_license_image=SimpleUploadedFile('l.heic', b'ftypheic',
                                                     content_type='image/heic'))
        self.addCleanup(reg.drivers_license_image.delete, save=False)
        data, note = _read_document(reg.drivers_license_image)
        self.assertIsNone(data)
        self.assertIn('HEIC', note)


class RegistrationPdfBuilderTests(TestCase):
    def test_documents_are_only_added_when_asked_for(self):
        # The emailed copy goes to the person who uploaded them; only the CDSO's
        # filed copy carries the scans back.
        reg = _registration(
            drivers_license_image=SimpleUploadedFile('l.jpg', _jpeg(), content_type='image/jpeg'))
        self.addCleanup(reg.drivers_license_image.delete, save=False)

        plain = registration_confirmation_pdf(reg)
        filed = registration_confirmation_pdf(reg, include_documents=True)
        self.assertGreater(len(filed), len(plain))
        self.assertTrue(plain.startswith(b'%PDF'))
        self.assertTrue(filed.startswith(b'%PDF'))

    def test_build_survives_a_registration_with_no_uploads_at_all(self):
        pdf = registration_confirmation_pdf(_registration(), include_documents=True)
        self.assertTrue(pdf.startswith(b'%PDF'))


class RegistrationPdfEndpointTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            email='print-admin@slc.edu.ph', full_name='ADMIN', password='x', role='admin')
        cls.guard = User.objects.create_user(
            email='print-guard@slc.edu.ph', full_name='GUARD', password='x', role='security')

    def _url(self, reg):
        return f'/api/vehicles/registrations/{reg.id}/pdf/'

    def test_admin_downloads_the_pdf(self):
        reg = _registration()
        self.client.force_authenticate(self.admin)
        r = self.client.get(self._url(reg))
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertIn('PRT1234', r['Content-Disposition'])
        self.assertTrue(r.content.startswith(b'%PDF'))

    def test_pending_registration_cannot_be_printed(self):
        # The document says the pass was approved; printing one for an
        # application still under review hands out a pass nobody granted.
        reg = _registration(status='pending', or_number='')
        self.client.force_authenticate(self.admin)
        r = self.client.get(self._url(reg))
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_guard_cannot_print(self):
        reg = _registration()
        self.client.force_authenticate(self.guard)
        self.assertEqual(self.client.get(self._url(reg)).status_code,
                         status.HTTP_403_FORBIDDEN)

    def test_anonymous_cannot_print(self):
        reg = _registration()
        self.assertIn(self.client.get(self._url(reg)).status_code,
                      (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

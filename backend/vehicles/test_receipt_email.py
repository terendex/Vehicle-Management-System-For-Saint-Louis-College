"""The registration form travels with the Official Receipt.

Until the receipt is in there is no settled fee to confirm, so the approval
mail no longer carries a PDF for an application accepted unpaid. The receipt
upload is what completes the form, and that is the mail the PDF rides on — the
same document, uploads and all, that the CDSO files from Vehicle Registration
Management.
"""
from io import BytesIO

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from vehicles.email_utils import (send_acceptance_email,
                                  send_receipt_received_email)
from vehicles.models import VehicleRegistration

LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'


def _jpeg():
    from PIL import Image
    buf = BytesIO()
    Image.new('RGB', (200, 140), (180, 190, 200)).save(buf, format='JPEG')
    return buf.getvalue()


def _registration(**kwargs):
    fields = dict(
        registrant_type='student', full_name='DELA CRUZ, JUAN',
        email='receipt-mail@slc.edu.ph', vehicle_type='Motorcycle',
        plate_number='RCP1234', status='accepted',
    )
    fields.update(kwargs)
    return VehicleRegistration.objects.create(**fields)


def _pdfs(message):
    return [a for a in message.attachments
            if isinstance(a, tuple) and a[0].lower().endswith('.pdf')]


@override_settings(EMAIL_BACKEND=LOCMEM, EMAIL_SEND_ASYNC=False)
class AcceptanceEmailAttachmentTests(TestCase):
    def test_paid_registration_keeps_its_pdf(self):
        reg = _registration(payment_status='paid', or_number='1234567')
        send_acceptance_email(reg, 'Temp!234', user_code='SLC-OWN-000001')
        self.assertEqual(len(_pdfs(mail.outbox[0])), 1)

    def test_fee_exempt_registration_still_gets_its_pdf(self):
        # Nothing was owed, so nothing is outstanding — an exempt applicant
        # must not be left waiting on a receipt that will never exist.
        reg = _registration(payment_status='exempt')
        send_acceptance_email(reg, 'Temp!234', user_code='SLC-OWN-000002')
        self.assertEqual(len(_pdfs(mail.outbox[0])), 1)

    def test_unpaid_registration_gets_no_pdf_yet(self):
        reg = _registration(payment_status='unpaid',
                            unpaid_accept_reason='Brought the OR to the counter later.')
        send_acceptance_email(reg, 'Temp!234', user_code='SLC-OWN-000003')
        msg = mail.outbox[0]
        self.assertEqual(_pdfs(msg), [])
        # ...and the mail must not promise an attachment it did not send.
        self.assertNotIn('is attached', msg.body)
        self.assertIn('once your Official Receipt has been uploaded', msg.body)


@override_settings(EMAIL_BACKEND=LOCMEM, EMAIL_SEND_ASYNC=False)
class ReceiptReceivedEmailTests(TestCase):
    def test_accepted_registration_receives_the_form_as_a_pdf(self):
        reg = _registration(
            payment_status='paid', or_number='7654321',
            or_receipt_image=SimpleUploadedFile('r.jpg', _jpeg(), content_type='image/jpeg'))
        self.addCleanup(reg.or_receipt_image.delete, save=False)

        send_receipt_received_email(reg)
        msg = mail.outbox[0]
        self.assertIn('7654321', msg.body)
        attached = _pdfs(msg)
        self.assertEqual(len(attached), 1)
        self.assertIn('RCP1234', attached[0][0])
        self.assertTrue(attached[0][1].startswith(b'%PDF'))

    def test_pending_registration_is_told_the_form_follows_approval(self):
        # The PDF states the pass was granted; sending it to someone still
        # under review would hand out a pass nobody issued.
        reg = _registration(status='pending', payment_status='paid', or_number='1112223')
        send_receipt_received_email(reg)
        msg = mail.outbox[0]
        self.assertEqual(_pdfs(msg), [])
        self.assertIn('queued for CDSO review', msg.body)

    def test_upload_endpoint_sends_the_mail(self):
        reg = _registration(status='pending', payment_status='unpaid')
        reg.payment_token = __import__('uuid').uuid4()
        reg.save(update_fields=['payment_token'])

        r = self.client.post('/api/vehicles/register/payment/', {
            'token': str(reg.payment_token),
            'or_number': '9998887',
            'receipt': SimpleUploadedFile('r.jpg', _jpeg(), content_type='image/jpeg'),
        })
        self.assertEqual(r.status_code, 200, r.content)
        reg.refresh_from_db()
        self.addCleanup(reg.or_receipt_image.delete, save=False)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('9998887', mail.outbox[0].body)

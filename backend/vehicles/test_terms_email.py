"""The registration emails hand the applicant a copy of the Terms and Conditions.

They agree to the terms on the form; the "received" and "approved" mails repeat
them, in both the HTML and the plain-text part, with the fee line matching what
this applicant actually owes.
"""
from django.core import mail
from django.test import TestCase, override_settings

from vehicles import pass_terms
from vehicles.email_utils import send_acceptance_email, send_pending_email
from vehicles.test_registration_email import LOCMEM, html_of, make_reg


@override_settings(EMAIL_BACKEND=LOCMEM, DEFAULT_FROM_EMAIL='slccdso@gmail.com',
                   PUBLIC_SITE_URL='https://slc.example.edu')
class TermsInRegistrationEmailsTests(TestCase):

    def assert_carries_terms(self, msg):
        html = html_of(msg)
        self.assertIn('Terms and Conditions', html)
        self.assertIn('TERMS AND CONDITIONS', msg.body)
        for text in (pass_terms.INTRO, 'speed limit of 10 kph', 'Third Offense'):
            self.assertIn(text, msg.body)
            self.assertIn(text, html)
        # The **bold** markers are formatting, never shown as asterisks.
        self.assertNotIn('**', html)
        self.assertNotIn('**', msg.body)
        self.assertIn('https://slc.example.edu/policy', html)

    def test_the_received_email_carries_the_terms(self):
        send_pending_email(make_reg())
        self.assert_carries_terms(mail.outbox[-1])

    def test_the_approved_email_carries_the_terms(self):
        send_acceptance_email(make_reg(payment_status='paid', or_number='1234567'),
                              'TempPass1!', 'SLC-VO-000001')
        self.assert_carries_terms(mail.outbox[-1])

    def test_the_fee_line_quotes_what_this_applicant_owes(self):
        reg = make_reg()
        send_pending_email(reg)
        self.assertIn(f'PHP {reg.pass_fee():,.2f}', mail.outbox[-1].body)

    def test_an_exempt_applicant_is_not_told_to_upload_a_receipt(self):
        reg = make_reg(registrant_type='employee', department_type='cleaning_services',
                       student_id='', program_year='', campus_days=[], schedule='ANY')
        self.assertEqual(reg.pass_fee(), 0)
        send_pending_email(reg)
        terms = mail.outbox[-1].body.split('TERMS AND CONDITIONS', 1)[1]
        self.assertNotIn('upload the Official Receipt', terms)

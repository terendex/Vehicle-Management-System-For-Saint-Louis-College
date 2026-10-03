"""The Policies page API: anyone reads, only the CDSO (with a step-up) edits,
and what is saved is cleaned to the editor's HTML allowlist."""
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import AuditLog, User
from accounts.test_twofa import make_confirmed_device
from accounts.twofa_api import build_login_response
from vehicles.models import PolicyDocument
from vehicles.policy_html import clean_policy_html

PASSWORD = 'Test!Pass9'
LIST_URL = '/api/vehicles/policies/'


def authed_client(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION='Bearer ' + build_login_response(user)['access'])
    return client


class PolicyApiTests(TestCase):

    def setUp(self):
        self.admin = User.objects.create_user(
            email='cdso@slc.edu.ph', last_name='ADMIN', first_name='CDSO',
            password=PASSWORD, role='admin',
        )
        self.owner = User.objects.create_user(
            email='owner@slc.edu.ph', last_name='OWNER', first_name='VEHICLE',
            password=PASSWORD, role='vehicle_owner',
        )

    def test_unedited_policies_read_as_null_for_anyone(self):
        res = APIClient().get(LIST_URL)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(set(res.data), {'privacy', 'terms'})
        self.assertIsNone(res.data['terms']['content'])

    def test_public_read_ignores_a_dead_token(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer not-a-real-token')
        self.assertEqual(client.get(LIST_URL).status_code, 200)

    def test_admin_saves_and_everyone_reads_it(self):
        res = authed_client(self.admin).put(LIST_URL + 'terms/', {'content': '<h2>New rules</h2>'}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['updated_by'], self.admin.full_name)

        read = APIClient().get(LIST_URL)
        self.assertEqual(read.data['terms']['content'], '<h2>New rules</h2>')
        self.assertIsNone(read.data['privacy']['content'])
        self.assertTrue(AuditLog.objects.filter(details__startswith='Policy updated | Vehicle Pass Terms').exists())

    def test_saving_twice_keeps_one_row(self):
        client = authed_client(self.admin)
        client.put(LIST_URL + 'privacy/', {'content': '<p>one</p>'}, format='json')
        client.put(LIST_URL + 'privacy/', {'content': '<p>two</p>'}, format='json')
        self.assertEqual(PolicyDocument.objects.get(key='privacy').content, '<p>two</p>')
        self.assertEqual(PolicyDocument.objects.count(), 1)

    def test_restore_default_deletes_the_edit(self):
        client = authed_client(self.admin)
        client.put(LIST_URL + 'terms/', {'content': '<p>edited</p>'}, format='json')
        res = client.delete(LIST_URL + 'terms/')
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.data['content'])
        self.assertFalse(PolicyDocument.objects.exists())

    def test_blank_unknown_and_oversized_are_refused(self):
        client = authed_client(self.admin)
        self.assertEqual(client.put(LIST_URL + 'terms/', {'content': '   '}, format='json').status_code, 400)
        self.assertEqual(client.put(LIST_URL + 'terms/', {'content': '<p></p><script>x</script>'}, format='json').status_code, 400)
        self.assertEqual(client.put(LIST_URL + 'other/', {'content': 'x'}, format='json').status_code, 404)
        self.assertEqual(client.put(LIST_URL + 'terms/', {'content': 'x' * 200_001}, format='json').status_code, 400)
        self.assertFalse(PolicyDocument.objects.exists())

    def test_non_admins_cannot_edit(self):
        self.assertEqual(APIClient().put(LIST_URL + 'terms/', {'content': 'x'}, format='json').status_code, 401)
        self.assertEqual(authed_client(self.owner).put(LIST_URL + 'terms/', {'content': 'x'}, format='json').status_code, 403)
        self.assertEqual(authed_client(self.owner).delete(LIST_URL + 'terms/').status_code, 403)
        self.assertFalse(PolicyDocument.objects.exists())

    def test_enrolled_admin_needs_step_up(self):
        make_confirmed_device(self.admin)
        res = authed_client(self.admin).put(LIST_URL + 'terms/', {'content': 'x'}, format='json')
        self.assertEqual(res.status_code, 403)
        self.assertTrue(res.data.get('stepup_required'))

    def test_saved_html_is_cleaned(self):
        dirty = ('<h2 onclick="steal()">Rules</h2><script>alert(1)</script>'
                 '<p style="text-align: justify; position: fixed">Body <a href="javascript:alert(1)">x</a></p>')
        res = authed_client(self.admin).put(LIST_URL + 'terms/', {'content': dirty}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['content'],
                         '<h2>Rules</h2><p style="text-align: justify">Body <a>x</a></p>')


class CleanPolicyHtmlTests(TestCase):
    """The allowlist keeps everything the editor produces and nothing else."""

    def test_editor_markup_survives_unchanged(self):
        html = ('<h1>Title</h1><p>Sub</p><h2 style="text-align: center">Section</h2>'
                '<p style="text-align: justify"><strong>B</strong> <em>I</em> <u>U</u> <s>S</s> '
                '<span style="color: #9B1C1C; font-size: 18px; font-family: Georgia, serif">styled</span> '
                '<mark data-color="#FDF0BE" style="background-color: #FDF0BE; color: inherit">hi</mark> '
                '<a target="_blank" rel="noopener noreferrer nofollow" href="mailto:a@b.ph">a@b.ph</a></p>'
                '<ol type="a" style="list-style-type: lower-alpha"><li><p>one</p></li></ol>'
                '<ul><li><p>dot</p></li></ul><blockquote><h3>Box</h3><p>text</p></blockquote><hr>'
                '<table style="min-width: 50px"><colgroup><col style="min-width: 25px"></colgroup><tbody><tr>'
                '<td colspan="1" rowspan="1" data-background-color="#FCEDED" style="background-color: #FCEDED">'
                '<p>cell</p></td></tr></tbody></table>')
        self.assertEqual(clean_policy_html(html), html)

    def test_dangerous_content_is_removed(self):
        cases = {
            '<p>a<script>alert(1)</script>b</p>': '<p>ab</p>',
            '<p>a<style>p{}</style>b</p>': '<p>ab</p>',
            '<img src=x onerror=alert(1)><p>ok</p>': '<p>ok</p>',
            '<iframe src="https://evil"></iframe><p>ok</p>': '<p>ok</p>',
            '<a href="java\tscript:alert(1)">x</a>': '<a>x</a>',
            '<a href="data:text/html,x">x</a>': '<a>x</a>',
            '<p style="background: url(https://evil)">x</p>': '<p>x</p>',
            '<p style="color: expression(alert(1))">x</p>': '<p>x</p>',
            '<ol type="&quot;onmouseover">x</ol>': '<ol>x</ol>',
            '<div><p>kept text</p></div>': '<p>kept text</p>',
            '<p>&lt;script&gt; stays text</p>': '<p>&lt;script&gt; stays text</p>',
            '<p><strong>unclosed': '<p><strong>unclosed</strong></p>',
        }
        for dirty, clean in cases.items():
            with self.subTest(dirty=dirty):
                self.assertEqual(clean_policy_html(dirty), clean)

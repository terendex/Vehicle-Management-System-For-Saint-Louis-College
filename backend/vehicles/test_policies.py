"""The Policies page API: anyone reads, only the CDSO (with a step-up) edits."""
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import AuditLog, User
from accounts.test_twofa import make_confirmed_device
from accounts.twofa_api import build_login_response
from vehicles.models import PolicyDocument

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
        res = authed_client(self.admin).put(LIST_URL + 'terms/', {'content': '## New rules'}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['updated_by'], self.admin.full_name)

        read = APIClient().get(LIST_URL)
        self.assertEqual(read.data['terms']['content'], '## New rules')
        self.assertIsNone(read.data['privacy']['content'])
        self.assertTrue(AuditLog.objects.filter(details__startswith='Policy updated | Vehicle Pass Terms').exists())

    def test_saving_twice_keeps_one_row(self):
        client = authed_client(self.admin)
        client.put(LIST_URL + 'privacy/', {'content': 'one'}, format='json')
        client.put(LIST_URL + 'privacy/', {'content': 'two'}, format='json')
        self.assertEqual(PolicyDocument.objects.get(key='privacy').content, 'two')
        self.assertEqual(PolicyDocument.objects.count(), 1)

    def test_restore_default_deletes_the_edit(self):
        client = authed_client(self.admin)
        client.put(LIST_URL + 'terms/', {'content': 'edited'}, format='json')
        res = client.delete(LIST_URL + 'terms/')
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.data['content'])
        self.assertFalse(PolicyDocument.objects.exists())

    def test_blank_unknown_and_oversized_are_refused(self):
        client = authed_client(self.admin)
        self.assertEqual(client.put(LIST_URL + 'terms/', {'content': '   '}, format='json').status_code, 400)
        self.assertEqual(client.put(LIST_URL + 'other/', {'content': 'x'}, format='json').status_code, 404)
        self.assertEqual(client.put(LIST_URL + 'terms/', {'content': 'x' * 100_001}, format='json').status_code, 400)
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

"""A broadcast reaches the owners who existed when it was sent — nobody else.

Parking notices go out by email to the current owner list and are mirrored in
the owner portal. The portal read was unfiltered, so an owner who registered
today opened their dashboard to every notice ever posted, none of which was
addressed to them. These tests pin the cut-off at the reader's join date.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from vehicles.models import ParkingNotice

User = get_user_model()

NOTICES = '/api/vehicles/notices/'


class NoticeVisibilityTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            email='notice-admin@slc.edu.ph', full_name='ADMIN', password='x', role='admin')
        # An owner who was already registered when the old notice went out.
        cls.veteran = User.objects.create_user(
            email='notice-veteran@slc.edu.ph', full_name='VETERAN', password='x',
            role='vehicle_owner')
        User.objects.filter(pk=cls.veteran.pk).update(
            date_joined=timezone.now() - timedelta(days=30))
        cls.veteran.refresh_from_db()

        # created_at is auto_now_add, so backdate it after the fact.
        cls.old = ParkingNotice.objects.create(
            title='Old Notice', body='Sent before the newcomer existed.', created_by=cls.admin)
        ParkingNotice.objects.filter(pk=cls.old.pk).update(
            created_at=timezone.now() - timedelta(days=7))

        cls.newcomer = User.objects.create_user(
            email='notice-newcomer@slc.edu.ph', full_name='NEWCOMER', password='x',
            role='vehicle_owner')

        cls.fresh = ParkingNotice.objects.create(
            title='Fresh Notice', body='Sent after everyone registered.', created_by=cls.admin)

    def _titles(self, user):
        self.client.force_authenticate(user)
        r = self.client.get(NOTICES)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        return {n['title'] for n in r.data}

    def test_newcomer_does_not_see_notices_predating_their_account(self):
        self.assertEqual(self._titles(self.newcomer), {'Fresh Notice'})

    def test_owner_who_was_a_recipient_still_sees_the_notice(self):
        self.assertEqual(self._titles(self.veteran), {'Old Notice', 'Fresh Notice'})

    def test_admin_sees_every_active_notice_to_manage_it(self):
        # The admin account itself was created after the backdated notice.
        self.assertEqual(self._titles(self.admin), {'Old Notice', 'Fresh Notice'})

    def test_deactivated_notice_is_hidden_from_everyone(self):
        ParkingNotice.objects.filter(pk=self.fresh.pk).update(is_active=False)
        self.assertEqual(self._titles(self.newcomer), set())
        self.assertEqual(self._titles(self.admin), {'Old Notice'})

"""Tests for accounts/db_delete_rules.py — ON DELETE rules in the database itself.

Two things have to hold for the change to be safe:

  * a delete made OUTSIDE Django (the Neon console, raw SQL) cascades the way
    the app would, instead of failing on a foreign key,
  * a delete made THROUGH Django behaves exactly as it did before, because
    Django's own collector still runs first and the database rule never fires.
"""

from django.db import connection
from django.test import TestCase

from accounts.db_delete_rules import sync_fk_delete_rules
from accounts.models import AuditLog, User
from vehicles.models import Vehicle, VehicleRegistration


class DbDeleteRulesTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email='rules-admin@example.com', full_name='Rules Admin', password='Passw0rd!23', role='admin',
        )
        self.owner = User.objects.create_user(
            email='rules-owner@example.com', full_name='Rules Owner', password='Passw0rd!23', role='admin',
        )
        self.vehicle = Vehicle.objects.create(plate_number='DBR 1001', vehicle_type='car', user=self.owner)
        self.reg = VehicleRegistration.objects.create(
            registrant_type='employee', full_name='Rules Owner', email='rules-owner@example.com',
            plate_number='DBR 1001', vehicle_type='car', user=self.owner, vehicle=self.vehicle,
        )
        self.log = AuditLog.objects.create(
            actor=self.admin, action=AuditLog.Action.USER_UPDATED, target_user=self.owner,
        )

    def _raw_delete_user(self, user):
        with connection.cursor() as cur:
            cur.execute(f'DELETE FROM {User._meta.db_table} WHERE {User._meta.pk.column} = %s', [user.pk])

    def test_migrate_left_every_rule_in_sync(self):
        # post_migrate already ran when the test database was built.
        self.assertEqual(sync_fk_delete_rules(verbosity=0), 0)

    def test_raw_delete_cascades_like_the_app(self):
        # The exact failure from the Neon console: a user an audit row points at.
        self._raw_delete_user(self.owner)

        self.assertFalse(User.objects.filter(pk=self.owner.pk).exists())
        self.log.refresh_from_db()
        self.assertIsNone(self.log.target_user_id)             # history kept, reference nulled
        self.assertEqual(self.log.actor_id, self.admin.pk)
        # Owned records go with the account, as delete_users_with_owned_records() does.
        self.assertFalse(Vehicle.objects.filter(pk=self.vehicle.pk).exists())
        self.assertFalse(VehicleRegistration.objects.filter(pk=self.reg.pk).exists())

    def test_orm_delete_is_unchanged(self):
        # A bare ORM delete still follows the model's SET_NULL, not the DB's CASCADE.
        self.owner.delete()

        self.vehicle.refresh_from_db()
        self.reg.refresh_from_db()
        self.assertIsNone(self.vehicle.user_id)
        self.assertIsNone(self.reg.user_id)
        self.log.refresh_from_db()
        self.assertIsNone(self.log.target_user_id)

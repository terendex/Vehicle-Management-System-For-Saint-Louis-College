"""Tests for accounts/db_choice_checks.py — choice CHECK constraints.

  * a value outside a column's choices is refused by the database itself, the
    way a typo in the Neon table editor would be,
  * every value the app legitimately writes (including the stream's legacy
    'stream'/'auto' training samples) is still accepted.
"""

from django.db import IntegrityError, connection, transaction
from django.test import TestCase

from accounts.db_choice_checks import sync_choice_checks
from accounts.models import User
from scanning.models import MLTrainingSample
from vehicles.models import Vehicle, VehicleRegistration


class DbChoiceChecksTests(TestCase):
    def setUp(self):
        self.reg = VehicleRegistration.objects.create(
            registrant_type='employee', full_name='Check Owner', email='check-owner@example.com',
            plate_number='CHK 1001', vehicle_type='car',
        )

    def _raw_update(self, sql, params):
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute(sql, params)

    def test_migrate_left_every_check_in_sync(self):
        # post_migrate already ran when the test database was built.
        self.assertEqual(sync_choice_checks(verbosity=0), 0)

    def test_wrong_case_status_is_refused(self):
        with self.assertRaises(IntegrityError):
            self._raw_update(
                f'UPDATE {VehicleRegistration._meta.db_table} SET status = %s '
                f'WHERE {VehicleRegistration._meta.pk.column} = %s',
                ['Accepted', self.reg.pk],
            )

    def test_unknown_role_is_refused(self):
        user = User.objects.create_user(
            email='check-user@example.com', full_name='Check User', password='Passw0rd!23', role='admin',
        )
        with self.assertRaises(IntegrityError):
            self._raw_update(
                f'UPDATE {User._meta.db_table} SET role = %s WHERE {User._meta.pk.column} = %s',
                ['cdso', user.pk],
            )

    def test_app_values_are_still_accepted(self):
        self.reg.status = VehicleRegistration.Status.ACCEPTED
        self.reg.save()
        Vehicle.objects.create(plate_number='CHK 1002', vehicle_type='car')

    def test_stream_training_samples_are_still_accepted(self):
        # scanning/consumers.py writes these two values, which the choices lack.
        MLTrainingSample.objects.create(image='x.jpg', status='auto', source='stream')

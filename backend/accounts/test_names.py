"""Names stored as last name / first name / middle initial.

accounts.User and vehicles.VehicleRegistration used to keep one `full_name`
column. These pin the replacement: how names are split and displayed, that
search and the APIs work on the parts, that a backup from before the change
still restores, and that the deferred column drop does its job.
"""
import importlib
import io
import json

from django.core.management import call_command
from django.db import connection
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from accounts.models import User
from accounts.names import (compose_full_name, name_search_q, split_full_name,
                            upgrade_legacy_backup)
from vehicles.models import VehicleRegistration


class SplitAndComposeTests(SimpleTestCase):

    CASES = {
        # Applicants: the form joined its three boxes with commas.
        'CAASI, ROLANDO, OLIVERAS': ('CAASI', 'ROLANDO', 'O'),
        'ESPINUEVA, NELLA BRENDA, PERALTA': ('ESPINUEVA', 'NELLA BRENDA', 'P'),
        'PANGAYAN, JUNALYN': ('PANGAYAN', 'JUNALYN', ''),
        'REYES, MARIE ANN': ('REYES', 'MARIE ANN', ''),          # two-word first name kept whole
        'DELA CRUZ, JUAN S.': ('DELA CRUZ', 'JUAN', 'S'),        # the display form parses back
        # Staff accounts: typed FIRST [MIDDLE] LAST, no comma.
        'System Admin': ('Admin', 'System', ''),
        'ALADIN C. VILLAREAL': ('VILLAREAL', 'ALADIN', 'C'),
        'AXEL JONAS CATALAN TANGALIN': ('TANGALIN', 'AXEL JONAS', 'C'),
        'Madonna': ('Madonna', '', ''),
        # Surname particles stay with the last name.
        'JUAN DELA CRUZ': ('DELA CRUZ', 'JUAN', ''),
        'MARIA DE LOS SANTOS': ('DE LOS SANTOS', 'MARIA', ''),
        'JUAN SANTOS DELA CRUZ': ('DELA CRUZ', 'JUAN', 'S'),
        '': ('', '', ''),
    }

    def test_split(self):
        for text, parts in self.CASES.items():
            self.assertEqual(split_full_name(text), parts, text)

    def test_compose(self):
        self.assertEqual(compose_full_name('DELA CRUZ', 'JUAN', 'santos'), 'DELA CRUZ, JUAN S.')
        self.assertEqual(compose_full_name('DELA CRUZ', 'JUAN', ''), 'DELA CRUZ, JUAN')
        self.assertEqual(compose_full_name('  DELA   CRUZ ', 'JUAN', 's.'), 'DELA CRUZ, JUAN S.')
        self.assertEqual(compose_full_name('', '', 'S'), '')

    def test_migrations_froze_the_same_splitter(self):
        """The data migrations carry their own copy; it must split identically."""
        for name in ('accounts.migrations.0040_split_full_name',
                     'vehicles.migrations.0094_split_registration_full_name'):
            frozen = importlib.import_module(name).split_full_name
            for text, parts in self.CASES.items():
                self.assertEqual(frozen(text), parts, (name, text))


class ModelTests(TestCase):

    def test_user_full_name_is_computed(self):
        u = User.objects.create_user(email='n1@slc.edu.ph', password='x',
                                     last_name=' DELA  CRUZ ', first_name='JUAN', middle_initial='santos')
        u.refresh_from_db()
        self.assertEqual((u.last_name, u.first_name, u.middle_initial), ('DELA CRUZ', 'JUAN', 'S'))
        self.assertEqual(u.full_name, 'DELA CRUZ, JUAN S.')
        self.assertEqual(u.get_full_name(), 'DELA CRUZ, JUAN S.')

    def test_create_user_refuses_the_old_argument(self):
        with self.assertRaises(TypeError):
            User.objects.create_user(email='n2@slc.edu.ph', password='x', full_name='A, B')

    def test_create_user_requires_a_name(self):
        with self.assertRaises(ValueError):
            User.objects.create_user(email='n3@slc.edu.ph', password='x')

    def test_registration_full_name_is_computed(self):
        reg = VehicleRegistration.objects.create(
            registrant_type='student', last_name='REYES', first_name='ANA', middle_initial='m',
            email='reg@slc.edu.ph', plate_number='NAM 1001', vehicle_type='car')
        self.assertEqual(reg.full_name, 'REYES, ANA M.')
        self.assertEqual(str(reg).split(' - ')[0], 'REYES, ANA M.')

    def test_search_finds_a_name_in_either_order(self):
        User.objects.create_user(email='s1@slc.edu.ph', password='x', role='security',
                                 last_name='DELA CRUZ', first_name='JUAN', middle_initial='S')
        User.objects.create_user(email='s2@slc.edu.ph', password='x', role='security',
                                 last_name='REYES', first_name='ANA')
        for term in ('juan dela cruz', 'Dela Cruz, Juan', 'cruz', 'juan s'):
            self.assertEqual(list(User.objects.filter(name_search_q(term))
                                  .values_list('email', flat=True)), ['s1@slc.edu.ph'], term)


class ApiTests(TestCase):

    def setUp(self):
        self.admin = User.objects.create_user(
            email='names.admin@slc.edu.ph', password='x', role='admin',
            last_name='ADMIN', first_name='NAMES', is_staff=True, is_superuser=True)
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def test_user_payload_carries_parts_and_display_name(self):
        res = self.client.get(f'/api/accounts/users/{self.admin.pk}/')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['full_name'], 'ADMIN, NAMES')
        self.assertEqual((res.data['last_name'], res.data['first_name']), ('ADMIN', 'NAMES'))

    def test_guard_created_from_parts(self):
        from unittest import mock
        with mock.patch('accounts.serializers._send_account_created_email'):
            res = self.client.post('/api/accounts/admin/create-guard/', {
                'last_name': 'SANTOS', 'first_name': 'PEDRO', 'middle_initial': 'Garcia',
                'email': 'pedro@slc.edu.ph', 'agency': 'ACME'}, format='json')
        self.assertIn(res.status_code, (200, 201), res.data)
        guard = User.objects.get(email='pedro@slc.edu.ph')
        self.assertEqual(guard.full_name, 'SANTOS, PEDRO G.')

    def test_old_single_name_payload_is_split(self):
        """A browser still on the previous bundle sends one full_name."""
        from unittest import mock
        with mock.patch('accounts.serializers._send_account_created_email'):
            res = self.client.post('/api/accounts/admin/create-guard/', {
                'full_name': 'REYES, JOSE', 'email': 'jose@slc.edu.ph', 'agency': 'ACME'}, format='json')
        self.assertIn(res.status_code, (200, 201), res.data)
        guard = User.objects.get(email='jose@slc.edu.ph')
        self.assertEqual((guard.last_name, guard.first_name), ('REYES', 'JOSE'))

    def test_missing_first_name_is_refused(self):
        res = self.client.post('/api/accounts/admin/create-guard/', {
            'last_name': 'SOLO', 'email': 'solo@slc.edu.ph', 'agency': 'ACME'}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('first_name', res.data)

    def test_user_list_search_by_name(self):
        User.objects.create_user(email='g1@slc.edu.ph', password='x', role='security',
                                 last_name='DELA CRUZ', first_name='JUAN')
        res = self.client.get('/api/accounts/users/', {'search': 'juan dela cruz'})
        self.assertEqual(res.status_code, 200)
        rows = res.data.get('results', res.data)
        self.assertEqual([r['email'] for r in rows], ['g1@slc.edu.ph'])


class LegacyBackupTests(SimpleTestCase):

    def test_old_backup_is_rewritten(self):
        old = json.dumps([
            {'model': 'accounts.user', 'pk': 1, 'fields': {'full_name': 'CAASI, ROLANDO, OLIVERAS',
                                                          'first_name': '', 'last_name': ''}},
            {'model': 'vehicles.vehicleregistration', 'pk': 2, 'fields': {'full_name': 'REYES, ANA'}},
            {'model': 'vehicles.vehicle', 'pk': 3, 'fields': {'plate_number': 'ABC123'}},
        ])
        records = json.loads(upgrade_legacy_backup(old))
        self.assertNotIn('full_name', records[0]['fields'])
        self.assertEqual((records[0]['fields']['last_name'], records[0]['fields']['first_name'],
                          records[0]['fields']['middle_initial']), ('CAASI', 'ROLANDO', 'O'))
        self.assertEqual(records[1]['fields']['last_name'], 'REYES')
        self.assertEqual(records[2], {'model': 'vehicles.vehicle', 'pk': 3,
                                      'fields': {'plate_number': 'ABC123'}})

    def test_current_backup_is_untouched(self):
        payload = json.dumps([{'model': 'accounts.user', 'pk': 1,
                               'fields': {'last_name': 'A', 'first_name': 'B', 'middle_initial': ''}}])
        self.assertIs(upgrade_legacy_backup(payload), payload)


class DropLegacyColumnTests(TestCase):
    """The deferred second step. DDL is transactional on PostgreSQL, so the
    drop is rolled back with the test and other tests keep their column."""

    def _columns(self, table):
        with connection.cursor() as cursor:
            return {c.name for c in connection.introspection.get_table_description(cursor, table)}

    def test_backfills_rows_written_by_old_code_then_drops(self):
        u = User.objects.create_user(email='old.code@slc.edu.ph', password='x',
                                     last_name='TEMP', first_name='TEMP')
        with connection.cursor() as cursor:
            # What the previous code does: writes full_name, leaves the parts blank.
            cursor.execute("UPDATE tbl_user SET full_name = %s, last_name = '', first_name = '' "
                           "WHERE user_id = %s", ['NUGAO, KRIS, KNOWELL', u.pk])
        self.assertIn('full_name', self._columns('tbl_user'))

        call_command('drop_legacy_full_name', stdout=io.StringIO())
        self.assertIn('full_name', self._columns('tbl_user'), 'a dry run must change nothing')

        call_command('drop_legacy_full_name', '--apply', stdout=io.StringIO())
        u.refresh_from_db()
        self.assertEqual(u.full_name, 'NUGAO, KRIS K.')
        self.assertNotIn('full_name', self._columns('tbl_user'))
        self.assertNotIn('full_name', self._columns('tbl_vehicle_registration'))

        # Running it again is harmless.
        call_command('drop_legacy_full_name', '--apply', stdout=io.StringIO())


class NoUsernameTests(TestCase):
    """username is off the model (accounts 0043): sign-in is by email and the
    name is the three parts. The column stays until drop_legacy_username, so
    an install on the previous code keeps working against the same database."""

    def _columns(self):
        with connection.cursor() as cursor:
            return {c.name for c in connection.introspection.get_table_description(cursor, 'tbl_user')}

    def test_the_model_has_no_username(self):
        from django.core.exceptions import FieldDoesNotExist
        with self.assertRaises(FieldDoesNotExist):
            User._meta.get_field('username')
        self.assertEqual(User.USERNAME_FIELD, 'email')

    def test_accounts_are_made_and_found_without_it(self):
        u = User.objects.create_user(email='no.username@slc.edu.ph', password='Passw0rd!23',
                                     last_name='DELA CRUZ', first_name='JUAN', middle_initial='P')
        self.assertEqual(User.objects.get_by_natural_key('no.username@slc.edu.ph').pk, u.pk)
        self.assertEqual(u.full_name, 'DELA CRUZ, JUAN P.')
        self.assertTrue(self.client.login(email='no.username@slc.edu.ph', password='Passw0rd!23'))

    def test_the_previous_code_can_still_write_the_column(self):
        """What an older install does: its INSERT/UPDATE names username."""
        u = User.objects.create_user(email='old.install@slc.edu.ph', password='x',
                                     last_name='OLD', first_name='CODE')
        self.assertIn('username', self._columns())
        with connection.cursor() as cursor:
            cursor.execute("UPDATE tbl_user SET username = NULL WHERE user_id = %s", [u.pk])
        u.refresh_from_db()
        self.assertEqual(u.last_name, 'OLD')

    def test_an_older_backup_still_restores(self):
        from accounts.backup_utils import dump_backup, load_backup
        u = User.objects.create_user(email='restore.me@slc.edu.ph', password='x',
                                     last_name='BEFORE', first_name='RESTORE')
        records = json.loads(dump_backup())
        for record in records:
            if record['model'] == 'accounts.user':
                record['fields']['username'] = None          # what every older backup carries
                if record['pk'] == u.pk:
                    record['fields']['last_name'] = 'RESTORED'
        load_backup(json.dumps(records))
        u.refresh_from_db()
        self.assertEqual(u.last_name, 'RESTORED')

    def test_upgrade_drops_username_only_from_accounts(self):
        old = json.dumps([
            {'model': 'accounts.user', 'pk': 1,
             'fields': {'username': None, 'last_name': 'A', 'first_name': 'B', 'middle_initial': ''}},
            {'model': 'vehicles.vehicle', 'pk': 3, 'fields': {'plate_number': 'ABC123'}},
        ])
        records = json.loads(upgrade_legacy_backup(old))
        self.assertNotIn('username', records[0]['fields'])
        self.assertEqual(records[0]['fields']['last_name'], 'A')
        self.assertEqual(records[1], {'model': 'vehicles.vehicle', 'pk': 3,
                                      'fields': {'plate_number': 'ABC123'}})

    def test_the_deferred_drop(self):
        """Rolled back with the test (DDL is transactional on PostgreSQL)."""
        call_command('drop_legacy_username', stdout=io.StringIO())
        self.assertIn('username', self._columns(), 'a dry run must change nothing')
        call_command('drop_legacy_username', '--apply', stdout=io.StringIO())
        self.assertNotIn('username', self._columns())
        User.objects.create_user(email='after.drop@slc.edu.ph', password='x',
                                 last_name='AFTER', first_name='DROP')
        out = io.StringIO()
        call_command('drop_legacy_username', '--apply', stdout=out)   # again: harmless
        self.assertIn('already dropped', out.getvalue())


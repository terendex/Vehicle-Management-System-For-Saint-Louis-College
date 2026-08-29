"""Registration must cost the same whether the table holds 10 rows or 10,000.

These are complexity tests, not speed tests: each endpoint is exercised against
a small dataset and then a much larger one, and the assertion is that the query
count does not grow with the row count. A query count that rises with N is the
signature of an O(N) path — usually a missing index, a per-row lookup in a loop,
or a filter Postgres cannot answer from an index.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.db.models import Value
from django.db.models.functions import Lower, Replace, Upper
from django.utils import timezone
from rest_framework.test import APITestCase

from vehicles.models import (
    VehicleRegistration, RegistrationPeriod, SystemSettings, Vehicle,
)

User = get_user_model()

SMALL, LARGE = 25, 500


def seed_registrations(n, offset=0):
    """Bulk-create accepted registrations that all sit in the lookup indexes."""
    VehicleRegistration.objects.bulk_create([
        VehicleRegistration(
            full_name=f'SEED, USER {i}',
            email=f'perf{offset + i}@slc.edu.ph',
            plate_number=f'PRF{offset + i:05d}',
            student_id=f'{20000000 + offset + i}',
            registrant_type='student',
            status=VehicleRegistration.Status.ACCEPTED,
        )
        for i in range(n)
    ])


class RegistrationComplexityTests(APITestCase):
    """Query count per request must not scale with the number of registrations."""

    def setUp(self):
        today = timezone.localdate()
        RegistrationPeriod.objects.create(
            label='Perf window', is_active=True,
            start_date=today - timedelta(days=1),
            end_date=today + timedelta(days=1),
        )
        SystemSettings.get()

    def _count(self, fn):
        with CaptureQueriesContext(connection) as ctx:
            fn()
        return len(ctx.captured_queries)

    def _scaling(self, fn):
        """Return (queries_at_small_N, queries_at_large_N)."""
        VehicleRegistration.objects.filter(email__startswith='perf').delete()
        seed_registrations(SMALL)
        small = self._count(fn)
        seed_registrations(LARGE, offset=SMALL)
        large = self._count(fn)
        return small, large

    def assertConstant(self, label, small, large):
        self.assertEqual(
            small, large,
            f"{label}: {small} queries at N={SMALL} but {large} at N={SMALL + LARGE} "
            f"— the query count scales with the number of registrations (O(N))."
        )

    # ── read endpoints the form hits before submitting ──────────────────────
    def test_status_endpoint_is_constant(self):
        small, large = self._scaling(
            lambda: self.client.get('/api/vehicles/register/status/'))
        self.assertConstant('register/status', small, large)

    def test_availability_endpoint_is_constant(self):
        def call():
            self.client.get('/api/vehicles/register/availability/',
                            {'plate_number': 'PRF00001'})
        small, large = self._scaling(call)
        self.assertConstant('register/availability', small, large)

    def test_schedule_slots_endpoint_is_constant(self):
        small, large = self._scaling(
            lambda: self.client.get('/api/vehicles/register/schedule-slots/'))
        self.assertConstant('register/schedule-slots', small, large)

    # ── the submission itself ───────────────────────────────────────────────
    def test_submission_is_constant(self):
        counter = {'n': 0}

        def submit():
            counter['n'] += 1
            i = counter['n']
            self.client.post('/api/vehicles/register/open/', {
                'full_name': 'DELA CRUZ, JUAN',
                'email': f'submit{i}@slc.edu.ph',
                'contact_number': '+639171234567',
                'plate_number': f'SUB{i:05d}',
                'vehicle_type': 'car',
                'registrant_type': 'employee',
                'employee_id': f'{31000000 + i}',
                'department': 'Cleaning and Services',
                'address': 'San Fernando, La Union',
                'privacy_consent': True,
            }, format='json')

        small, large = self._scaling(submit)
        self.assertConstant('register/open (submit)', small, large)


class LookupIndexTests(TestCase):
    """Every duplicate check must have an index path available.

    Testing the planner's *choice* on a small table proves nothing — Postgres
    correctly prefers a sequential scan over an index when the whole table fits
    in a couple of pages, index or not. What matters is whether an index path
    exists at all, so these disable seqscan and check the planner can still
    answer the query. If it falls back to Seq Scan even then, no usable index
    exists and the lookup is O(N) once the table grows.

    The queries mirror what _registration_conflict actually runs, including the
    status filter — the partial unique indexes only apply with it.
    """

    @classmethod
    def setUpTestData(cls):
        seed_registrations(300)

    ACTIVE = ['pending', 'accepted']

    def assertHasIndexPath(self, qs, label):
        sql, params = qs.query.sql_with_params()
        with connection.cursor() as cur:
            cur.execute('SET enable_seqscan = off')
            try:
                cur.execute('EXPLAIN ' + sql, params)
                plan = '\n'.join(r[0] for r in cur.fetchall())
            finally:
                cur.execute('SET enable_seqscan = on')
        self.assertNotIn(
            'Seq Scan', plan,
            f"{label}: no index path exists even with seqscan disabled — "
            f"this lookup is O(N):\n{plan}")

    def _active(self):
        return VehicleRegistration.objects.filter(status__in=self.ACTIVE)

    def test_normalised_plate_lookup_has_an_index(self):
        qs = self._active().exclude(plate_number='').annotate(
            _n=Upper(Replace('plate_number', Value(' '), Value('')))
        ).filter(_n='PRF00007')
        self.assertHasIndexPath(qs, 'normalised plate lookup')

    def test_normalised_email_lookup_has_an_index(self):
        qs = self._active().exclude(email='').annotate(
            _n=Lower('email')
        ).filter(_n='perf7@slc.edu.ph')
        self.assertHasIndexPath(qs, 'normalised email lookup')

    def test_student_id_lookup_has_an_index(self):
        qs = self._active().filter(registrant_type='student', student_id='20000007')
        self.assertHasIndexPath(qs, 'student_id lookup')

    def test_employee_id_lookup_has_an_index(self):
        qs = self._active().filter(registrant_type='employee', employee_id='31000007')
        self.assertHasIndexPath(qs, 'employee_id lookup')

    def test_conduction_lookup_has_an_index(self):
        qs = self._active().filter(conduction_number='CS12345A678')
        self.assertHasIndexPath(qs, 'conduction_number lookup')

    def test_iexact_on_ids_would_defeat_the_index(self):
        """Guards the fix: __iexact wraps the column in UPPER() and the partial
        unique index no longer applies. Kept as a live demonstration so the
        pattern is not reintroduced."""
        qs = self._active().filter(registrant_type='student', student_id__iexact='20000007')
        sql, params = qs.query.sql_with_params()
        with connection.cursor() as cur:
            cur.execute('SET enable_seqscan = off')
            try:
                cur.execute('EXPLAIN ' + sql, params)
                plan = '\n'.join(r[0] for r in cur.fetchall())
            finally:
                cur.execute('SET enable_seqscan = on')
        self.assertIn('upper', plan.lower(),
                      'expected __iexact to force an UPPER() filter')

    def test_drivers_license_lookup_has_an_index(self):
        """_license_db_conflict ran __iexact against a column save() already
        stores upper-cased, so uniq_active_registration_drivers_license never
        applied and the pre-check scanned every active registration."""
        qs = self._active().filter(drivers_license='N01-20-123456')
        self.assertHasIndexPath(qs, "drivers_license lookup")

    def test_vehicle_plate_lookup_has_an_index(self):
        """_plate_conflict's second check, against already-owned vehicles."""
        qs = Vehicle.objects.filter(plate_number='PRF00007', user__isnull=False)
        self.assertHasIndexPath(qs, 'owned-vehicle plate lookup')

    def test_user_email_lookup_has_an_index(self):
        """Every account lookup by address uses email__iexact, which compiles to
        UPPER(email) = UPPER(%s); user_email_upper indexes that expression."""
        qs = User.objects.filter(email__iexact='perf7@slc.edu.ph', is_archived=False)
        self.assertHasIndexPath(qs, 'user email lookup')

    def test_ban_check_lookups_have_an_index(self):
        """_registration_ban ORs five identifiers together. Each disjunct has to
        be answerable from an index or the whole check degrades to a scan — it
        runs on every submission *and* every availability keystroke."""
        qs = (VehicleRegistration.objects
              .annotate(_plate_norm=Upper(Replace('plate_number', Value(' '), Value(''))),
                        _email_norm=Lower('email'))
              .filter(_plate_norm='PRF00007', user__registration_banned=True))
        self.assertHasIndexPath(qs, 'ban check by normalised plate')

        qs = (VehicleRegistration.objects
              .annotate(_email_norm=Lower('email'))
              .filter(_email_norm='perf7@slc.edu.ph', user__registration_banned=True))
        self.assertHasIndexPath(qs, 'ban check by normalised email')


class ConflictHelperComplexityTests(TestCase):
    """The helpers behind the submit path, measured directly.

    The endpoint-level tests above count queries, which catches a per-row loop
    but not a single query that happens to scan the whole table. These call the
    helpers against a seeded table and assert the plan uses an index — the only
    thing that keeps the cost flat as registrations accumulate.
    """

    @classmethod
    def setUpTestData(cls):
        seed_registrations(300)

    def _plan(self, fn):
        with CaptureQueriesContext(connection) as ctx:
            fn()
        return [q['sql'] for q in ctx.captured_queries]

    def test_license_precheck_does_not_upper_the_column(self):
        from vehicles.views import _license_db_conflict
        sqls = self._plan(lambda: _license_db_conflict('n01-20-123456'))
        self.assertTrue(sqls, 'expected the pre-check to run a query')
        joined = ' '.join(sqls).lower()
        self.assertNotIn('upper("tbl_vehicle_registration"."drivers_license")', joined,
                         'the licence pre-check is UPPER()-wrapping its column again '
                         '— uniq_active_registration_drivers_license cannot be used')

    def test_license_precheck_still_matches_case_insensitively(self):
        """Dropping __iexact must not weaken the check: the input is upper-cased
        before matching, exactly as save() stores it."""
        from vehicles.views import _license_db_conflict
        VehicleRegistration.objects.create(
            registrant_type='student', full_name='LICENCE, HOLDER',
            email='lic@slc.edu.ph', plate_number='LIC0001', vehicle_type='car',
            drivers_license='N01-20-777777', status=VehicleRegistration.Status.PENDING)
        self.assertIsNotNone(_license_db_conflict('n01-20-777777'))
        self.assertIsNotNone(_license_db_conflict('  N01-20-777777  '))
        self.assertIsNone(_license_db_conflict('N01-20-888888'))

    def test_ban_check_does_not_upper_its_columns(self):
        from vehicles.views import _registration_ban
        sqls = self._plan(lambda: _registration_ban(
            'PRF00007', 'perf7@slc.edu.ph', '20000007', '', conduction_number='CN1'))
        joined = ' '.join(sqls).lower()
        for column in ('conduction_number', 'student_id', 'employee_id'):
            self.assertNotIn(f'upper("tbl_vehicle_registration"."{column}")', joined,
                             f'the ban check is UPPER()-wrapping {column} again')

    def test_ban_check_still_finds_a_banned_applicant(self):
        from vehicles.views import _registration_ban
        banned = User.objects.create_user(
            email='banned@slc.edu.ph', full_name='BANNED, ONE',
            password='pw', role='vehicle_owner')
        banned.registration_banned = True
        banned.save(update_fields=['registration_banned'])
        VehicleRegistration.objects.create(
            registrant_type='student', full_name='BANNED, ONE',
            email='banned@slc.edu.ph', plate_number='BAN0001', vehicle_type='car',
            student_id='99887766', user=banned,
            status=VehicleRegistration.Status.EXPIRED)

        # Matched on each identifier, in the same spacing/case a form would send
        self.assertIsNotNone(_registration_ban('ban 0001', '', '', ''))
        self.assertIsNotNone(_registration_ban('', 'BANNED@slc.edu.ph', '', ''))
        self.assertIsNotNone(_registration_ban('', '', '99887766', ''))
        self.assertIsNone(_registration_ban('ZZZ9999', 'nobody@slc.edu.ph', '', ''))


class PaymentComplexityTests(APITestCase):
    """The receipt-upload step must cost the same at 25 rows and at 10,000.

    It is reached by an unauthenticated applicant holding a link, so an O(N)
    lookup here is also the cheapest thing on the system to hammer.
    """

    def setUp(self):
        today = timezone.localdate()
        RegistrationPeriod.objects.create(
            label='Payment perf window', is_active=True,
            start_date=today - timedelta(days=1),
            end_date=today + timedelta(days=1),
        )
        SystemSettings.get()

    def _count(self, fn):
        with CaptureQueriesContext(connection) as ctx:
            fn()
        return len(ctx.captured_queries)

    def _pending(self, tag):
        """A pending row with a token, created through save() so it gets one."""
        return VehicleRegistration.objects.create(
            registrant_type='student', full_name='PAYER, PERF',
            email=f'payperf{tag}@slc.edu.ph', plate_number=f'PYP{tag:05d}',
            vehicle_type='car', student_id=f'{40000000 + tag}',
            status=VehicleRegistration.Status.PENDING,
        )

    def _scaling(self, make_call):
        VehicleRegistration.objects.filter(email__startswith='perf').delete()

        seed_registrations(SMALL)
        small = self._count(make_call(1))
        seed_registrations(LARGE, offset=SMALL)
        large = self._count(make_call(2))
        return small, large

    def assertConstant(self, label, small, large):
        self.assertEqual(
            small, large,
            f"{label}: {small} queries at N={SMALL} but {large} at N={SMALL + LARGE} "
            f"— the query count scales with the number of registrations (O(N))."
        )

    def test_payment_lookup_is_constant(self):
        def make_call(tag):
            reg = self._pending(tag)
            return lambda: self.client.get(
                '/api/vehicles/register/payment/', {'token': str(reg.payment_token)})
        small, large = self._scaling(make_call)
        self.assertConstant('register/payment (GET)', small, large)

    def test_receipt_upload_is_constant(self):
        def make_call(tag):
            reg = self._pending(tag)

            def call():
                self.client.post('/api/vehicles/register/payment/', {
                    'token': str(reg.payment_token),
                    'or_number': '1380093',
                    'receipt': SimpleUploadedFile(
                        f'r{tag}.jpg', b'x' * 32, content_type='image/jpeg'),
                }, format='multipart')
            return call
        small, large = self._scaling(make_call)
        self.assertConstant('register/payment (POST)', small, large)

    def test_a_dead_token_is_constant(self):
        """The 404 path is the one an attacker would spray."""
        def make_call(_tag):
            return lambda: self.client.get(
                '/api/vehicles/register/payment/',
                {'token': '11111111-2222-3333-4444-555555555555'})
        small, large = self._scaling(make_call)
        self.assertConstant('register/payment (bad token)', small, large)

    def test_the_payment_token_lookup_has_an_index(self):
        """unique=True should give it one; this pins that it stays that way.

        Same reasoning as LookupIndexTests: what matters is that an index path
        exists at all, not which one the planner picks on a tiny table.
        """
        seed_registrations(300, offset=9000)
        qs = VehicleRegistration.objects.filter(
            payment_token='11111111-2222-3333-4444-555555555555',
            status=VehicleRegistration.Status.PENDING)
        sql, params = qs.query.sql_with_params()
        with connection.cursor() as cur:
            cur.execute('SET enable_seqscan = off')
            try:
                cur.execute('EXPLAIN ' + sql, params)
                plan = '\n'.join(r[0] for r in cur.fetchall())
            finally:
                cur.execute('SET enable_seqscan = on')
        self.assertNotIn(
            'Seq Scan', plan,
            f'payment_token lookup has no index path — it is O(N):\n{plan}')


class CdsoQueueComplexityTests(APITestCase):
    """The reviewer's queue: query count must not grow with the table.

    Payload size is a separate question this does not answer — the endpoint
    returns every row of a status, and the table paginates in the browser.
    """

    def setUp(self):
        SystemSettings.get()
        self.admin = User.objects.create_user(
            email='queueperf@slc.edu.ph', full_name='Queue Perf',
            password='pw', role='admin', is_staff=True, is_superuser=True)
        self.client.force_authenticate(user=self.admin)

    def test_the_summary_is_constant(self):
        """One GROUP BY for the whole cross-tab, however many rows it spans.

        The status/type/payment grids are three accumulations over the same
        result set, so adding one must not add a query.
        """
        VehicleRegistration.objects.filter(email__startswith='perf').delete()

        def call():
            self.client.get('/api/vehicles/registrations/summary/')

        seed_registrations(SMALL)
        with CaptureQueriesContext(connection) as ctx:
            call()
        small = len(ctx.captured_queries)

        seed_registrations(LARGE, offset=SMALL)
        with CaptureQueriesContext(connection) as ctx:
            call()
        large = len(ctx.captured_queries)

        self.assertEqual(
            small, large,
            f"registrations/summary: {small} queries at N={SMALL} but {large} at "
            f"N={SMALL + LARGE} — the cross-tab is costing a query per group.")

    def test_the_pending_queue_is_constant(self):
        VehicleRegistration.objects.filter(email__startswith='perf').delete()

        def call():
            self.client.get('/api/vehicles/registrations/pending/?status=accepted')

        seed_registrations(SMALL)
        with CaptureQueriesContext(connection) as ctx:
            call()
        small = len(ctx.captured_queries)

        seed_registrations(LARGE, offset=SMALL)
        with CaptureQueriesContext(connection) as ctx:
            call()
        large = len(ctx.captured_queries)

        self.assertEqual(
            small, large,
            f"registrations/pending: {small} queries at N={SMALL} but {large} at "
            f"N={SMALL + LARGE} — serialising the page costs a query per row.")

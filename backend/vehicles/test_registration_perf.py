"""Registration must cost the same whether the table holds 10 rows or 10,000.

These are complexity tests, not speed tests: each endpoint is exercised against
a small dataset and then a much larger one, and the assertion is that the query
count does not grow with the row count. A query count that rises with N is the
signature of an O(N) path — usually a missing index, a per-row lookup in a loop,
or a filter Postgres cannot answer from an index.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
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
                'department': 'Services',
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

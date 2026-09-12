"""Who came through the gate, and the two ways a guard gets at a vehicle when
the plate alone will not do.

Three things are covered here:

  * every AccessLog is classified as it is written, so "how many students
    entered today" is answerable from the log rather than from today's
    accounts;
  * a vehicle can be looked up by its owner's NAME as well as by plate or
    conduction number;
  * a vehicle with no plate at all can still be recorded, counted as inside,
    and logged out again.
"""
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import User
from scanning.entry_logic import classify_entrant
from scanning.models import AccessLog, Office, VisitorPass
from vehicles.models import Supplier, SupplierPlate, Vehicle

LOOKUP = '/api/scan/owner-lookup/'
UNREC  = '/api/scan/unrecognized/'


def _owner(email, name, owner_type):
    return User.objects.create_user(
        email=email, full_name=name, password='x',
        role='vehicle_owner', owner_type=owner_type)


def _vehicle(plate='', user=None, conduction=''):
    return Vehicle.objects.create(
        plate_number=plate, conduction_number=conduction,
        user=user, is_authorized=True)


class ClassifyEntrantTests(TestCase):
    def test_owner_type_decides_it(self):
        for owner_type in ('student', 'employee', 'fetcher', 'visitor'):
            v = _vehicle(f'CLS{owner_type[:4].upper()}1',
                         _owner(f'{owner_type}@slc.edu.ph', 'OWNER', owner_type))
            self.assertEqual(classify_entrant(v), owner_type)

    def test_supplier_plate_without_an_account(self):
        supplier = Supplier.objects.create(company_name='ACME', is_active=True)
        SupplierPlate.objects.create(supplier=supplier, plate_number='SUP1234')
        v = _vehicle('SUP1234')
        self.assertEqual(classify_entrant(v), 'supplier')

    def test_a_pass_holder_is_a_visitor_not_unregistered(self):
        # A walk-in with a pass IS a visitor. Reading that row as
        # "unregistered" is what made the visitor count read zero.
        v = _vehicle('VIS1234')
        VisitorPass.objects.create(
            vehicle=v, plate_number='VIS1234',
            office=Office.objects.create(name='Registrar'),
            valid_date=timezone.localdate(),
        )
        self.assertEqual(classify_entrant(v), 'visitor')

    def test_nothing_known_is_unregistered(self):
        self.assertEqual(classify_entrant(_vehicle('NUL1234')), 'unknown')
        self.assertEqual(classify_entrant(None, 'NUL9999'), 'unknown')


class AccessLogClassificationTests(TestCase):
    def test_every_log_is_classified_as_it_is_written(self):
        # Classification happens in save(), not at the dozen create() call
        # sites, so a new scan path cannot forget to do it.
        v = _vehicle('STU1111', _owner('s1@slc.edu.ph', 'STUDENT', 'student'))
        log = AccessLog.objects.create(
            plate_number='STU1111', vehicle=v,
            status=AccessLog.Status.AUTHORIZED, gate_id='gate1')
        self.assertEqual(log.entrant_category, 'student')

    def test_a_category_the_caller_supplied_is_left_alone(self):
        log = AccessLog.objects.create(
            plate_number='', status=AccessLog.Status.AUTHORIZED,
            entrant_category='visitor', is_unrecognized=True,
            driver_name='JUAN', gate_id='gate1')
        self.assertEqual(log.entrant_category, 'visitor')

    def test_the_category_is_frozen_at_the_time_of_the_scan(self):
        # The whole reason it is stored rather than derived: a student who
        # later becomes an employee must not rewrite last term's log.
        owner = _owner('grad@slc.edu.ph', 'GRADUATE', 'student')
        v = _vehicle('GRD1111', owner)
        log = AccessLog.objects.create(
            plate_number='GRD1111', vehicle=v,
            status=AccessLog.Status.AUTHORIZED, gate_id='gate1')

        owner.owner_type = 'employee'
        owner.save(update_fields=['owner_type'])

        log.refresh_from_db()
        self.assertEqual(log.entrant_category, 'student')


class OwnerLookupTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.guard = User.objects.create_user(
            email='lookup-guard@slc.edu.ph', full_name='GUARD',
            password='x', role='security')
        cls.owner = _owner('juan@slc.edu.ph', 'JUAN DELA CRUZ', 'student')
        cls.vehicle = _vehicle('ABC1234', cls.owner)

    def setUp(self):
        self.client.force_authenticate(self.guard)

    def test_lookup_by_name(self):
        r = self.client.get(LOOKUP, {'q': 'dela cruz'})
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data['count'], 1)
        row = r.data['results'][0]
        self.assertEqual(row['identifier'], 'ABC1234')
        self.assertEqual(row['owner_name'], 'JUAN DELA CRUZ')
        self.assertEqual(row['classification'], 'student')

    def test_lookup_by_partial_name_is_case_insensitive(self):
        self.assertEqual(self.client.get(LOOKUP, {'q': 'juan'}).data['count'], 1)
        self.assertEqual(self.client.get(LOOKUP, {'q': 'JUAN'}).data['count'], 1)

    def test_lookup_by_plate(self):
        r = self.client.get(LOOKUP, {'q': 'abc 1234'})
        self.assertEqual(r.data['count'], 1)
        self.assertEqual(r.data['results'][0]['identifier'], 'ABC1234')

    def test_lookup_by_conduction_number(self):
        _vehicle(conduction='CS12345A678',
                 user=_owner('new@slc.edu.ph', 'NEW CAR', 'employee'))
        r = self.client.get(LOOKUP, {'q': 'CS12345A678'})
        self.assertEqual(r.data['count'], 1)
        self.assertEqual(r.data['results'][0]['identifier'], 'CS12345A678')

    def test_a_name_with_no_match_returns_an_empty_list_not_an_error(self):
        r = self.client.get(LOOKUP, {'q': 'nobody at all'})
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data['count'], 0)

    def test_too_short_a_query_is_refused(self):
        # One letter would match most of the roster, which is not a lookup.
        r = self.client.get(LOOKUP, {'q': 'j'})
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_it_requires_a_signed_in_user(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(LOOKUP, {'q': 'juan'}).status_code,
                         status.HTTP_401_UNAUTHORIZED)

    def test_the_inside_badge_does_not_cost_a_query_per_result(self):
        # The per-plate _inside_state() is two round trips each; decorating a
        # full page of results with it would be two dozen. Asserted as "the
        # same cost for eight results as for one" rather than as a magic
        # number, because it is the flatness that matters — a future join that
        # shifts the constant is fine, a per-row lookup is not.
        _vehicle('SOLO0001', _owner('solo@slc.edu.ph', 'SOLO SURNAME', 'student'))
        with CaptureQueriesContext(connection) as one_result:
            r = self.client.get(LOOKUP, {'q': 'SURNAME'})
        self.assertEqual(r.data['count'], 1)

        for i in range(7):
            _vehicle(f'BAT{i:04d}',
                     _owner(f'batch{i}@slc.edu.ph', f'BATCH {i} SURNAME', 'student'))
        with CaptureQueriesContext(connection) as eight_results:
            r = self.client.get(LOOKUP, {'q': 'SURNAME'})
        self.assertEqual(r.data['count'], 8)

        self.assertEqual(len(eight_results), len(one_result))

    def test_a_vehicle_inside_is_flagged_as_such(self):
        v = _vehicle('INS1234', _owner('inside@slc.edu.ph', 'INSIDE OWNER', 'student'))
        AccessLog.objects.create(
            plate_number='INS1234', vehicle=v,
            status=AccessLog.Status.AUTHORIZED, gate_id='gate1')
        row = self.client.get(LOOKUP, {'q': 'INSIDE OWNER'}).data['results'][0]
        self.assertTrue(row['is_inside'])

    def test_a_vehicle_that_has_left_is_not(self):
        v = _vehicle('OUT1234', _owner('outside@slc.edu.ph', 'OUTSIDE OWNER', 'student'))
        entry = AccessLog.objects.create(
            plate_number='OUT1234', vehicle=v,
            status=AccessLog.Status.AUTHORIZED, gate_id='gate1')
        AccessLog.objects.create(
            plate_number='OUT1234', vehicle=v, status=AccessLog.Status.EXITED,
            gate_id='gate1', paired_entry=entry)
        row = self.client.get(LOOKUP, {'q': 'OUTSIDE OWNER'}).data['results'][0]
        self.assertFalse(row['is_inside'])


class UnrecognizedVehicleTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.guard = User.objects.create_user(
            email='unrec-guard@slc.edu.ph', full_name='GUARD',
            password='x', role='security', gate_assignment='gate1')

    def setUp(self):
        self.client.force_authenticate(self.guard)

    def _record(self, **overrides):
        payload = {
            'driver_name': 'JUAN DELA CRUZ',
            'entrant_category': 'visitor',
            'vehicle_type': 'car',
            'vehicle_color': 'Red',
            'vehicle_model': 'Toyota Vios',
        }
        payload.update(overrides)
        return self.client.post(UNREC, payload, format='json')

    def test_recording_an_entry(self):
        r = self._record()
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertTrue(r.data['is_unrecognized'])
        self.assertEqual(r.data['driver_name'], 'JUAN DELA CRUZ')
        self.assertEqual(r.data['classification'], 'visitor')
        self.assertEqual(r.data['reference'], f"NP-{r.data['id']}")
        # It lands at the guard's own gate, not the orphan 'main' bucket.
        self.assertEqual(r.data['gate_id'], 'gate1')

    def test_the_driver_name_is_required(self):
        # Without a plate it is the only identifier the vehicle has.
        r = self._record(driver_name='')
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('driver_name', r.data)

    def test_the_colour_is_required(self):
        r = self._record(vehicle_color='')
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('vehicle_color', r.data)

    def test_the_category_must_be_one_a_guard_can_actually_tell(self):
        # 'supplier' is identified by a plate on the supplier roster, and there
        # is no plate here to check one against.
        self.assertEqual(self._record(entrant_category='supplier').status_code,
                         status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self._record(entrant_category='').status_code,
                         status.HTTP_400_BAD_REQUEST)

    def test_the_vehicle_type_must_be_a_real_one(self):
        r = self._record(vehicle_type='spaceship')
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('vehicle_type', r.data)

    def test_it_appears_in_the_inside_list_until_it_leaves(self):
        entry = self._record().data
        listed = self.client.get(UNREC).data
        self.assertEqual([row['id'] for row in listed], [entry['id']])

        exit_r = self.client.post(f"{UNREC}{entry['id']}/exit/", {}, format='json')
        self.assertEqual(exit_r.status_code, status.HTTP_200_OK)
        self.assertEqual(exit_r.data['reference'], entry['reference'])
        self.assertEqual(exit_r.data['driver_name'], 'JUAN DELA CRUZ')

        self.assertEqual(self.client.get(UNREC).data, [])

    def test_the_exit_is_paired_to_its_entry(self):
        entry = self._record().data
        self.client.post(f"{UNREC}{entry['id']}/exit/", {}, format='json')
        exit_log = AccessLog.objects.get(paired_entry_id=entry['id'])
        self.assertEqual(exit_log.status, AccessLog.Status.EXITED)
        # The description rides along, so the exit row is readable on its own.
        self.assertEqual(exit_log.driver_name, 'JUAN DELA CRUZ')
        self.assertEqual(exit_log.entrant_category, 'visitor')

    def test_logging_the_same_vehicle_out_twice_is_refused(self):
        entry = self._record().data
        self.client.post(f"{UNREC}{entry['id']}/exit/", {}, format='json')
        again = self.client.post(f"{UNREC}{entry['id']}/exit/", {}, format='json')
        self.assertEqual(again.status_code, status.HTTP_409_CONFLICT)

    def test_an_unknown_reference_is_a_404(self):
        self.assertEqual(
            self.client.post(f'{UNREC}999999/exit/', {}, format='json').status_code,
            status.HTTP_404_NOT_FOUND)

    def test_another_gate_does_not_see_it(self):
        self._record()
        other = User.objects.create_user(
            email='gate4-guard@slc.edu.ph', full_name='GUARD 4',
            password='x', role='security', gate_assignment='gate4')
        self.client.force_authenticate(other)
        self.assertEqual(self.client.get(UNREC).data, [])


class UnrecognizedOccupancyTests(TestCase):
    """Plateless vehicles have to take up room like anything else."""

    def _unrecognized(self, driver, vtype='car'):
        return AccessLog.objects.create(
            plate_number='', vehicle_type=vtype,
            status=AccessLog.Status.AUTHORIZED,
            entrant_category='visitor', is_unrecognized=True,
            driver_name=driver, vehicle_color='Red', gate_id='gate1')

    def test_each_plateless_vehicle_counts_separately(self):
        from scanning.occupancy import inside_counts

        # They all share an empty plate, so a DISTINCT count keyed on the plate
        # alone folded six vehicles into one and the lot reported five free
        # spaces that were not there.
        for i in range(6):
            self._unrecognized(f'DRIVER {i}')

        counts = inside_counts()
        # No vehicle row means no vehicle_type to read, so they are charged to
        # the uncategorised bucket — see UNCATEGORIZED_COUNTS_AS.
        self.assertEqual(counts['unknown'], 6)
        self.assertEqual(counts['total'], 6)

    def test_a_plated_vehicle_still_counts_once_per_plate(self):
        from scanning.occupancy import inside_counts

        v = _vehicle('DUP1234')
        for _ in range(2):
            AccessLog.objects.create(
                plate_number='DUP1234', vehicle=v,
                status=AccessLog.Status.AUTHORIZED, gate_id='gate1')
        self.assertEqual(inside_counts()['total'], 1)

    def test_an_exited_plateless_vehicle_stops_counting(self):
        from scanning.occupancy import inside_counts

        entry = self._unrecognized('LEAVER')
        self._unrecognized('STAYER')
        AccessLog.objects.create(
            plate_number='', status=AccessLog.Status.EXITED,
            is_unrecognized=True, gate_id='gate1', paired_entry=entry)
        self.assertEqual(inside_counts()['total'], 1)

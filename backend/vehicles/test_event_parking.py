"""Events that hold back part of the car park.

An event declares how much of parking it will fill and, optionally, when. Both
halves matter to the gate: the share decides how many bays stop being offered,
and the time decides *when* they stop — an evening event must not make the car
park read as half gone at nine in the morning.
"""
from datetime import date, time, timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import User
from vehicles.capacity import category_state, event_reservation
from vehicles.models import Event, ParkingZone

EVENTS = '/api/vehicles/events/'


def _event(**kw):
    kw.setdefault('name', 'Foundation Day')
    kw.setdefault('date', timezone.localdate())
    kw.setdefault('is_active', True)
    return Event.objects.create(**kw)


class ShareFractionTests(TestCase):
    def test_every_choice_has_a_fraction(self):
        # A share with no fraction behind it would silently reserve nothing,
        # which is the one failure mode nobody would notice.
        for value in Event.ParkingShare.values:
            self.assertIn(value, Event.SHARE_FRACTIONS, value)

    def test_fractions_read_as_their_labels(self):
        self.assertEqual(_event(parking_share='none').share_fraction, 0.0)
        self.assertEqual(_event(parking_share='half').share_fraction, 0.5)
        self.assertEqual(_event(parking_share='full').share_fraction, 1.0)


class TimeDisplayTests(TestCase):
    def test_no_times_reads_as_all_day(self):
        # Not "12:00 AM - 12:00 AM": a blank window means the whole day, and a
        # guard reading midnight-to-midnight would think it had already ended.
        self.assertEqual(_event().time_display, 'All day')

    def test_both_times(self):
        ev = _event(start_time=time(9, 0), end_time=time(15, 30))
        self.assertEqual(ev.time_display, '9:00 AM - 3:30 PM')

    def test_one_sided_windows(self):
        self.assertEqual(_event(start_time=time(9, 0)).time_display, 'From 9:00 AM')
        self.assertEqual(_event(end_time=time(15, 0)).time_display, 'Until 3:00 PM')


class UnderWayTests(TestCase):
    def test_all_day_event_is_under_way_all_day(self):
        self.assertTrue(_event().is_under_way())

    def test_before_the_start_time_it_is_not(self):
        now = timezone.localtime()
        ev = _event(start_time=(now + timedelta(hours=2)).time())
        self.assertFalse(ev.is_under_way())

    def test_after_the_end_time_it_is_not(self):
        now = timezone.localtime()
        ev = _event(end_time=(now - timedelta(hours=2)).time())
        self.assertFalse(ev.is_under_way())

    def test_inside_the_window_it_is(self):
        now = timezone.localtime()
        ev = _event(start_time=(now - timedelta(hours=1)).time(),
                    end_time=(now + timedelta(hours=1)).time())
        self.assertTrue(ev.is_under_way())

    def test_a_different_day_is_never_under_way(self):
        ev = _event(date=timezone.localdate() - timedelta(days=1))
        self.assertFalse(ev.is_under_way())

    def test_inactive_and_archived_events_reserve_nothing(self):
        self.assertFalse(_event(is_active=False).is_under_way())
        self.assertFalse(_event(archived=True).is_under_way())


class ReservationTests(TestCase):
    def setUp(self):
        ParkingZone.objects.create(
            name='Car Zone', vehicle_category='car', capacity_override=10)

    def test_no_event_reserves_nothing(self):
        self.assertIsNone(event_reservation())
        car = category_state()['car']
        self.assertEqual(car['reserved'], 0)
        self.assertEqual(car['available'], 10)

    def test_a_half_share_holds_back_half_the_bays(self):
        _event(parking_share='half')
        car = category_state()['car']
        self.assertEqual(car['reserved'], 5)
        self.assertEqual(car['occupied'], 0)      # nobody has parked in them
        self.assertEqual(car['available'], 5)
        self.assertEqual(car['fill_pct'], 50)
        self.assertFalse(car['is_full'])

    def test_a_full_share_fills_the_car_park(self):
        _event(parking_share='full')
        car = category_state()['car']
        self.assertEqual(car['available'], 0)
        self.assertTrue(car['is_full'])

    def test_an_event_outside_its_window_reserves_nothing(self):
        now = timezone.localtime()
        _event(parking_share='half', start_time=(now + timedelta(hours=3)).time())
        self.assertIsNone(event_reservation())
        self.assertEqual(category_state()['car']['reserved'], 0)

    def test_the_largest_share_wins_when_two_events_overlap(self):
        _event(name='Small', parking_share='quarter')
        _event(name='Big', parking_share='three_quarters')
        res = event_reservation()
        self.assertEqual(res['name'], 'Big')
        self.assertEqual(category_state()['car']['reserved'], 7)  # floor(10 * .75)

    def test_odd_capacity_rounds_down(self):
        # The spare bay stays usable rather than being quietly withheld.
        ParkingZone.objects.all().update(capacity_override=7)
        _event(parking_share='half')
        self.assertEqual(category_state()['car']['reserved'], 3)

    def test_reservation_is_reported_by_name(self):
        _event(name='Foundation Day', parking_share='half',
               start_time=time(9, 0), end_time=time(15, 0))
        state = category_state()
        self.assertEqual(state['event']['name'], 'Foundation Day')
        self.assertEqual(state['event']['time_display'], '9:00 AM - 3:00 PM')
        self.assertEqual(state['event']['share'], 'half')


class EventApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            email='ev-admin@slc.edu.ph', full_name='ADMIN', password='x', role='admin')

    def setUp(self):
        self.client.force_authenticate(self.admin)

    def test_create_with_times_and_share(self):
        r = self.client.post(EVENTS, {
            'name': 'Intramurals',
            'date': date.today().isoformat(),
            'start_time': '08:00',
            'end_time': '17:00',
            'parking_share': 'two_thirds',
        }, format='json')
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertEqual(r.data['start_time'], '08:00')
        self.assertEqual(r.data['time_display'], '8:00 AM - 5:00 PM')
        self.assertEqual(r.data['parking_share'], 'two_thirds')
        self.assertEqual(r.data['parking_share_label'], 'About 2/3 of parking')

    def test_times_are_optional(self):
        r = self.client.post(EVENTS, {
            'name': 'All Day Mass', 'date': date.today().isoformat(),
        }, format='json')
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(r.data['start_time'])
        self.assertEqual(r.data['time_display'], 'All day')
        self.assertEqual(r.data['parking_share'], 'none')

    def test_end_before_start_is_refused(self):
        r = self.client.post(EVENTS, {
            'name': 'Backwards', 'date': date.today().isoformat(),
            'start_time': '15:00', 'end_time': '09:00',
        }, format='json')
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('end_time', r.data)

    def test_unknown_share_is_refused(self):
        r = self.client.post(EVENTS, {
            'name': 'Bad Share', 'date': date.today().isoformat(),
            'parking_share': 'most_of_it',
        }, format='json')
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('parking_share', r.data)

    def test_patching_the_name_does_not_blank_the_times(self):
        ev = _event(start_time=time(9, 0), end_time=time(15, 0), parking_share='half')
        r = self.client.patch(f'{EVENTS}{ev.id}/', {'name': 'Renamed'}, format='json')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data['start_time'], '09:00')
        self.assertEqual(r.data['parking_share'], 'half')

    def test_times_can_be_cleared_back_to_all_day(self):
        ev = _event(start_time=time(9, 0), end_time=time(15, 0))
        r = self.client.patch(f'{EVENTS}{ev.id}/',
                              {'start_time': '', 'end_time': ''}, format='json')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data['time_display'], 'All day')

    def test_list_and_detail_report_the_same_shape(self):
        # The two views used to carry a byte-identical _serialize each, which is
        # exactly how one of them would end up without the new fields.
        ev = _event(start_time=time(9, 0), parking_share='third')
        listed = next(e for e in self.client.get(EVENTS).data if e['id'] == ev.id)
        patched = self.client.patch(f'{EVENTS}{ev.id}/',
                                    {'name': ev.name}, format='json').data
        self.assertEqual(set(listed), set(patched))


class EventAndLedgerTogetherTests(APITestCase):
    """The free-space number and the on-campus number, exercised through the
    real endpoint rather than through category_state() alone.

    Capacity is declared by an admin. Parked is the bays the cameras read as
    taken. An event's declared share holds bays back on top of both. The gate
    ledger is reported beside them as vehicles on campus, and must never move
    the free count.
    """
    AVAIL = '/api/vehicles/parking-availability/'

    @classmethod
    def setUpTestData(cls):
        cls.guard = User.objects.create_user(
            email='evcap-guard@slc.edu.ph', full_name='GUARD',
            password='x', role='security')

    def setUp(self):
        from vehicles.models import ParkingZone
        self.zone = ParkingZone.objects.create(
            name='Car Zone', vehicle_category='car', capacity_override=10)
        self.client.force_authenticate(self.guard)

    def _park(self, n):
        from vehicles.models import ParkingSpace
        return [ParkingSpace.objects.create(
                    zone=self.zone, space_number=f'EV{i}', is_occupied=True,
                    x1=0.1, y1=0.1, x2=0.2, y2=0.2)
                for i in range(n)]

    def _car(self, plate):
        from vehicles.models import Vehicle
        return Vehicle.objects.create(
            plate_number=plate, vehicle_type=Vehicle.Type.CAR, is_authorized=True)

    def _enter(self, vehicle):
        from scanning.models import AccessLog
        return AccessLog.objects.create(
            plate_number=vehicle.plate_number, vehicle=vehicle,
            status=AccessLog.Status.AUTHORIZED, gate_id='gate1')

    def _exit(self, vehicle, entry):
        from scanning.models import AccessLog
        return AccessLog.objects.create(
            plate_number=vehicle.plate_number, vehicle=vehicle,
            status=AccessLog.Status.EXITED, gate_id='gate1', paired_entry=entry)

    def _summary(self):
        return self.client.get(f'{self.AVAIL}?category=car').data['summary']['car']

    def test_the_ledger_moves_on_campus_but_not_the_free_count(self):
        start = self._summary()
        self.assertEqual((start['total'], start['occupied'], start['available'],
                          start['on_campus']), (10, 0, 10, 0))

        car = self._car('LED1111')
        entry = self._enter(car)
        after_entry = self._summary()
        self.assertEqual(after_entry['on_campus'], 1)
        self.assertEqual(after_entry['available'], 10)

        self._exit(car, entry)
        self.assertEqual(self._summary()['on_campus'], 0)

    def test_a_parked_bay_takes_a_space(self):
        self._park(1)
        s = self._summary()
        self.assertEqual(s['occupied'], 1)
        self.assertEqual(s['available'], 9)

    def test_the_event_share_comes_off_the_available_count(self):
        before = self._summary()
        self.assertEqual(before['reserved'], 0)
        self.assertEqual(before['available'], 10)

        _event(parking_share='half')

        after = self._summary()
        self.assertEqual(after['total'], 10)        # capacity is unchanged
        self.assertEqual(after['occupied'], 0)      # nobody has parked yet
        self.assertEqual(after['reserved'], 5)      # but five are spoken for
        self.assertEqual(after['available'], 5)

    def test_parked_bays_and_the_event_share_stack(self):
        # Three cars parked and half the lot reserved leaves two, not five.
        self._park(3)
        _event(parking_share='half')

        s = self._summary()
        self.assertEqual(s['occupied'], 3)
        self.assertEqual(s['reserved'], 5)
        self.assertEqual(s['available'], 2)
        self.assertFalse(s['is_full'])

    def test_organizers_who_arrived_come_off_the_hold_instead_of_counting_twice(self):
        """Ten bays, half held, two organizers in and parked: they fill two of
        the held bays, so the hold shrinks to three — not five held on top of
        the two parked, which read the lot as fuller than it is."""
        from scanning.models import AccessLog
        event = _event(parking_share='half', organizer_plates=['ORG0001', 'ORG0002'])
        for plate in ('ORG0001', 'ORG0002'):
            AccessLog.objects.create(plate_number=plate, status=AccessLog.Status.AUTHORIZED,
                                     gate_id='gate1', event=event,
                                     entrant_category=AccessLog.Category.EVENT)
        self._park(2)

        s = self._summary()
        self.assertEqual(s['occupied'], 2)
        self.assertEqual(s['reserved'], 3)
        self.assertEqual(s['available'], 5)

    def test_the_hold_never_goes_below_zero_when_more_organizers_arrive(self):
        from scanning.models import AccessLog
        plates = [f'ORG{i:04d}' for i in range(7)]
        event = _event(parking_share='half', organizer_plates=plates)
        for plate in plates:
            AccessLog.objects.create(plate_number=plate, status=AccessLog.Status.AUTHORIZED,
                                     gate_id='gate1', event=event,
                                     entrant_category=AccessLog.Category.EVENT)
        self._park(7)
        s = self._summary()
        self.assertEqual(s['reserved'], 0)
        self.assertEqual(s['available'], 3)

    def test_full_once_the_reserve_and_the_parked_cars_fill_it(self):
        self._park(5)
        _event(parking_share='half')

        s = self._summary()
        self.assertEqual(s['available'], 0)
        self.assertTrue(s['is_full'])

    def test_a_bay_freeing_gives_a_space_back_while_an_event_is_running(self):
        bay = self._park(1)[0]
        _event(parking_share='half')
        self.assertEqual(self._summary()['available'], 4)

        bay.is_occupied = False
        bay.save(update_fields=['is_occupied'])
        self.assertEqual(self._summary()['available'], 5)

    def test_the_event_is_named_on_the_response(self):
        _event(name='Foundation Day', parking_share='half')
        body = self.client.get(f'{self.AVAIL}?category=car').data
        self.assertEqual(body['event']['name'], 'Foundation Day')
        self.assertEqual(body['event']['share_label'], 'About 1/2 of parking')

    def test_no_event_reports_none_rather_than_an_empty_dict(self):
        body = self.client.get(f'{self.AVAIL}?category=car').data
        self.assertIsNone(body['event'])


class CategoryCoversEveryOwnerTypeTests(TestCase):
    """Every kind of vehicle owner has somewhere to be counted.

    An owner_type with no matching AccessLog.Category would fall through
    classify_entrant() to 'unknown', so a whole class of people would be
    reported as unregistered in the entries breakdown and in the reports.
    """

    def test_every_owner_type_has_a_category(self):
        from scanning.models import AccessLog

        missing = [t for t in User.OwnerType.values
                   if t not in AccessLog.Category.values]
        self.assertEqual(missing, [], f'owner types with no category: {missing}')

    def test_the_category_adds_the_three_that_are_not_account_types(self):
        from scanning.models import AccessLog

        # A supplier is a plate on the supplier roster and an event organizer a
        # plate on an event's list — neither is an owner account — and
        # 'unknown' is the catch-all for a plate nothing is known about.
        extra = set(AccessLog.Category.values) - set(User.OwnerType.values)
        self.assertEqual(extra, {'supplier', 'event', 'unknown'})

    def test_each_owner_type_classifies_to_its_own_category(self):
        from scanning.entry_logic import classify_entrant
        from vehicles.models import Vehicle

        for owner_type in User.OwnerType.values:
            owner = User.objects.create_user(
                email=f'cover-{owner_type}@slc.edu.ph', full_name='OWNER',
                password='x', role='vehicle_owner', owner_type=owner_type)
            vehicle = Vehicle.objects.create(
                plate_number=f'CV{owner_type[:5].upper()}', user=owner,
                is_authorized=True)
            self.assertEqual(classify_entrant(vehicle), owner_type)


class ZoneRowReportsTheReserveTests(APITestCase):
    """The guard's parking screen reads per-zone rows, not the availability
    summary, so the reserve has to reach that shape too — otherwise the free
    count drops there with nothing on the page to explain it."""
    ZONES = '/api/vehicles/parking-zones/'

    @classmethod
    def setUpTestData(cls):
        cls.guard = User.objects.create_user(
            email='zone-guard@slc.edu.ph', full_name='GUARD',
            password='x', role='security')

    def setUp(self):
        self.zone = ParkingZone.objects.create(
            name='Car Zone', vehicle_category='car', capacity_override=10)
        self.client.force_authenticate(self.guard)

    def test_zone_row_carries_the_reserve_and_names_the_event(self):
        _event(name='Foundation Day', parking_share='half')
        row = self.client.get(f'{self.ZONES}{self.zone.id}/').data
        self.assertEqual(row['category_capacity'], 10)
        self.assertEqual(row['category_occupied'], 0)
        self.assertEqual(row['category_reserved'], 5)
        self.assertEqual(row['category_available'], 5)
        self.assertEqual(row['category_event']['name'], 'Foundation Day')

    def test_zone_row_with_no_event_reserves_nothing(self):
        row = self.client.get(f'{self.ZONES}{self.zone.id}/').data
        self.assertEqual(row['category_reserved'], 0)
        self.assertEqual(row['category_available'], 10)
        self.assertIsNone(row['category_event'])

"""Event organizer plates at the gate, and the event slip they leave with.

An unregistered plate on the organizer list of an event under way is let in
the way a supplier is — logged, handed a slip to print, and let out on a
re-check or from the slip. A registered owner who is an organizer keeps their
own rules and only carries the label. Outside the event window the list means
nothing.
"""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from scanning.entry_logic import get_organizer_event
from scanning.models import AccessLog
from vehicles.models import Event, Vehicle

MANUAL = '/api/scan/manual-entry/'
SLIP = '/api/scan/slip/'
SLIP_EXIT = '/api/scan/slip/exit/'


def _event(**kw):
    kw.setdefault('name', 'FOUNDATION DAY')
    kw.setdefault('date', timezone.localdate())
    kw.setdefault('is_active', True)
    kw.setdefault('organizer_plates', ['ORG1234'])
    return Event.objects.create(**kw)


def _backdate(entry_id, seconds):
    """Seconds, never minutes: an entry pushed back far enough to cross
    midnight lands in yesterday and stops counting as inside, which would make
    these tests fail every night just after 00:00."""
    AccessLog.objects.filter(pk=entry_id).update(
        scanned_at=timezone.now() - timedelta(seconds=seconds))


# The entry window a re-check must clear before it records an exit, shortened
# so a test only has to backdate a few seconds (see _backdate).
short_entry_window = patch('scanning.views.ENTRY_BREATHING_SECONDS', 5)


class OrganizerEntryTests(TestCase):
    def setUp(self):
        self.guard = User.objects.create_user(
            email='event-guard@slc.edu.ph', full_name='EVENT GUARD',
            password='SecurePassword123!', role='security', gate_assignment='gate1')
        self.client = APIClient()
        self.client.force_authenticate(user=self.guard)

    def _check(self, plate='ORG1234'):
        return self.client.post(MANUAL, {'plate_number': plate}, format='json').data

    def test_organizer_plate_enters_during_the_event_with_an_event_slip(self):
        event = _event()
        data = self._check()
        self.assertEqual(data['status'], 'authorized')
        self.assertTrue(data['allowed'])
        self.assertTrue(data['is_event'])
        self.assertEqual(data['organizer_event']['name'], 'FOUNDATION DAY')

        slip = data['event_slip']
        self.assertNotIn('slip', data)   # that key opens the slip-status dialog instead
        self.assertEqual(slip['kind'], 'event')
        self.assertEqual(slip['title'], 'EVENT SLIP')
        self.assertTrue(slip['code'].startswith('SLC-EVENT:'))
        self.assertEqual(slip['headline'], 'ORG1234')
        self.assertIn(['Event', 'FOUNDATION DAY', True], slip['sections'][0])

        entry = AccessLog.objects.get(pk=slip['id'])
        self.assertEqual(entry.entrant_category, AccessLog.Category.EVENT)
        self.assertEqual(entry.event_id, event.id)

    def test_the_slip_looks_up_prints_and_records_the_exit(self):
        _event()
        slip = self._check()['event_slip']

        looked = self.client.get(SLIP, {'code': slip['code']})
        self.assertEqual(looked.status_code, 200)
        self.assertEqual(looked.data['state'], 'inside')

        with patch('scanning.slip_printer.find_printer', return_value='POS58 Printer'), \
             patch('scanning.slip_printer.send_raw') as send:
            printed = self.client.post('/api/scan/slip/print/', {'code': slip['code']}, format='json')
        self.assertEqual(printed.status_code, 200)
        send.assert_called_once()

        exited = self.client.post(SLIP_EXIT, {'code': slip['code']}, format='json')
        self.assertEqual(exited.status_code, 200)
        self.assertEqual(exited.data['slip']['state'], 'exited')
        exit_log = AccessLog.objects.get(paired_entry_id=slip['id'])
        self.assertEqual(exit_log.entrant_category, AccessLog.Category.EVENT)
        self.assertEqual(self.client.post(SLIP_EXIT, {'code': slip['code']}, format='json').status_code, 409)

    def test_a_recheck_past_the_entry_window_records_the_exit(self):
        _event()
        slip = self._check()['event_slip']
        self.assertEqual(self._check()['status'], 'duplicate')        # same instant: a double read
        _backdate(slip['id'], 4)
        self.assertEqual(self._check()['status'], 'already_inside')   # inside the entry window
        _backdate(slip['id'], 10)
        with short_entry_window:
            exited = self._check()
        self.assertEqual(exited['status'], 'exited')
        self.assertTrue(exited['is_event'])

    def test_plates_saved_through_the_event_api_match_the_scanned_plate(self):
        """Saved with a space, the list never matched what the gate reads;
        the event API now stores plates the way the gate compares them."""
        _event(organizer_plates=['ORG 1234'])
        self.assertEqual(self._check('ORG1234')['status'], 'unknown')

        admin = User.objects.create_user(
            email='event-admin@slc.edu.ph', full_name='EVENT ADMIN',
            password='SecurePassword123!', role='admin')
        admin_client = APIClient()
        admin_client.force_authenticate(user=admin)
        ev = Event.objects.get()
        resp = admin_client.patch(f'/api/vehicles/events/{ev.id}/',
                                  {'organizer_plates': ['org 1234', 'ORG1234', 'fm 7', 'FM-007']},
                                  format='json')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(Event.objects.get().organizer_plates, ['ORG1234', 'FM-007'])
        self.assertEqual(self._check('ORG1234')['status'], 'authorized')

    def test_outside_the_event_window_the_plate_is_unregistered(self):
        # Either not started yet or already over, whichever fits in today.
        now = timezone.localtime()
        later, earlier = now + timedelta(hours=2), now - timedelta(hours=2)
        if later.date() == now.date():
            _event(start_time=later.time().replace(microsecond=0))
        else:
            _event(end_time=earlier.time().replace(microsecond=0))
        self.assertEqual(self._check()['status'], 'unknown')

    def test_a_past_event_left_switched_on_no_longer_counts(self):
        _event(date=timezone.localdate() - timedelta(days=3))
        self.assertIsNone(get_organizer_event('ORG1234'))
        self.assertEqual(self._check()['status'], 'unknown')

    def test_an_organizer_still_inside_when_the_event_ends_can_still_leave(self):
        event = _event()
        slip = self._check()['event_slip']
        _backdate(slip['id'], 10)
        Event.objects.filter(pk=event.pk).update(is_active=False)   # event over
        self.assertIsNone(get_organizer_event('ORG1234'))
        with short_entry_window:
            exited = self._check()
        self.assertEqual(exited['status'], 'exited')

    def test_a_registered_organizer_keeps_their_own_rules(self):
        """Being on the list adds the label, not an entry — no event slip."""
        owner = User.objects.create_user(
            email='org-owner@slc.edu.ph', full_name='ORG OWNER', password='SecurePassword123!',
            role='vehicle_owner', owner_type='employee')
        Vehicle.objects.create(plate_number='ORG1234', vehicle_type=Vehicle.Type.CAR,
                               is_authorized=True, user=owner)
        _event()
        data = self._check()
        self.assertNotIn('event_slip', data)
        self.assertFalse(data.get('is_event', False))
        self.assertEqual(data['organizer_event']['name'], 'FOUNDATION DAY')
        entry = AccessLog.objects.filter(plate_number='ORG1234').latest('scanned_at')
        self.assertEqual(entry.entrant_category, 'employee')

    def test_a_supplier_on_the_list_enters_for_the_event_while_it_is_on(self):
        """The organizer list outranks the supplier roster during the event —
        even when supplier hours would turn the supplier away."""
        from vehicles.models import Supplier, SupplierPlate
        supplier = Supplier.objects.create(company_name='CATERER', category='delivery', is_active=True)
        SupplierPlate.objects.create(supplier=supplier, plate_number='ORG1234')
        event = _event()
        with patch('scanning.views._supplier_rule_denial', return_value='Supplier hours are over.'):
            data = self._check()
        self.assertEqual(data['status'], 'authorized')
        self.assertTrue(data['is_event'])
        self.assertIn('event_slip', data)
        self.assertNotIn('supplier_slip', data)

        # After the event, the same plate is an ordinary supplier again.
        slip_id = data['event_slip']['id']
        _backdate(slip_id, 10)
        with short_entry_window:
            self.assertEqual(self._check()['status'], 'exited')
        AccessLog.objects.filter(status='exited').update(scanned_at=timezone.now() - timedelta(seconds=120))
        Event.objects.filter(pk=event.pk).update(is_active=False)
        with patch('scanning.views._supplier_rule_denial', return_value=None):
            again = self._check()
        self.assertTrue(again.get('is_supplier'))
        self.assertIn('supplier_slip', again)

    def test_a_registered_ebike_listed_by_control_number_gets_the_label(self):
        owner = User.objects.create_user(
            email='ebike-org@slc.edu.ph', full_name='EBIKE ORG', password='SecurePassword123!',
            role='vehicle_owner', owner_type='employee')
        Vehicle.objects.create(plate_number='FM-007', vehicle_type=Vehicle.Type.EBIKE,
                               is_authorized=True, user=owner)
        _event(organizer_plates=['FM-007'])
        data = self._check('FM007')   # typed without the hyphen
        self.assertEqual(data['organizer_event']['name'], 'FOUNDATION DAY')
        self.assertNotIn('event_slip', data)

    def test_an_unregistered_car_on_a_conduction_sticker_can_be_listed(self):
        _event(organizer_plates=['CS12345X'])   # not a plate shape
        data = self._check('CS12345X')
        self.assertEqual(data['status'], 'authorized')
        self.assertIn('event_slip', data)

    def test_event_slip_code_does_not_open_other_entries(self):
        other = AccessLog.objects.create(plate_number='SUP9999', status='authorized')
        self.assertEqual(self.client.get(SLIP, {'code': f'SLC-EVENT:{other.pk}'}).status_code, 404)

    def test_staying_past_the_event_end_shows_as_overstay_on_the_slip(self):
        now = timezone.localtime()
        start = (now - timedelta(hours=2)).time().replace(microsecond=0)
        end = (now + timedelta(minutes=30)).time().replace(microsecond=0)
        if end <= start:   # too close to midnight for a same-day window
            self.skipTest('needs a same-day window around the current time')
        event = _event(start_time=start, end_time=end)
        slip = self._check()['event_slip']
        self.assertEqual(slip['overstay_minutes'], 0)
        self.assertIsNotNone(slip['expires_at'])

        Event.objects.filter(pk=event.pk).update(
            end_time=(now - timedelta(minutes=20)).time().replace(microsecond=0))
        looked = self.client.get(SLIP, {'code': slip['code']}).data
        self.assertGreaterEqual(looked['overstay_minutes'], 19)

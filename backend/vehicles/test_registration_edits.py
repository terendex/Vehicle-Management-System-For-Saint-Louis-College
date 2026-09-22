"""Correcting a registration's details, on both sides of CDSO's decision.

Two flows, one whitelist (vehicles/registration_edits.py):

  * while the application is PENDING the applicant edits it in place, with the
    token from their acknowledgement email;
  * once it is ACCEPTED the owner files a change request and CDSO approves it,
    and only then does the row move.

What these tests are really guarding is the join between the two: that the same
field set is offered on both sides, that neither can reach a field the review
process owns, and that an approval carries the change out to the Vehicle row and
the account — because a corrected dashboard over a stale Vehicle is an owner the
gate still refuses.
"""
from datetime import timedelta

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from accounts.models import AuditLog, Notification, User
from vehicles.models import (RegistrationChangeRequest, RegistrationPeriod,
                             ReferenceItem, Vehicle, VehicleRegistration)
from vehicles.registration_edits import (EDITABLE_FIELDS, READ_ONLY_REASONS,
                                         clean_changes, editable_for)

from .test_dpo_privacy_flow import (LOCMEM, employee_payload, fetcher_payload,
                                    student_payload)


@override_settings(EMAIL_BACKEND=LOCMEM, DEFAULT_FROM_EMAIL='slccdso@gmail.com',
                   PUBLIC_SITE_URL='https://slc.example.edu',
                   EMAIL_SEND_ASYNC=False)
class EditFlowTestCase(TestCase):
    """Shared scaffolding: an open registration window and a CDSO account."""

    def setUp(self):
        self.client = APIClient()
        today = timezone.localdate()
        RegistrationPeriod.objects.create(
            label='Edit flow', is_active=True,
            start_date=today - timedelta(days=1), end_date=today + timedelta(days=1))
        self.admin = User.objects.create_user(
            email='editadmin@slc.edu.ph', full_name='Edit Admin',
            password='pw', role='admin', is_staff=True, is_superuser=True)

    # ── the pending half ──

    def submit(self, payload=None):
        res = self.client.post('/api/vehicles/register/open/',
                               payload or student_payload(), format='json')
        self.assertEqual(res.status_code, 201, res.data)
        return VehicleRegistration.objects.get(pk=res.data['id'])

    def self_edit(self, reg, **fields):
        return self.client.post('/api/vehicles/register/details/',
                                {'token': str(reg.payment_token), **fields},
                                format='json')

    def self_edit_form(self, reg):
        return self.client.get('/api/vehicles/register/details/',
                               {'token': str(reg.payment_token)})

    # ── the accepted half ──

    def as_admin(self):
        self.client.force_authenticate(user=self.admin)

    def accept(self, reg, **body):
        self.as_admin()
        res = self.client.post(f'/api/vehicles/registrations/{reg.pk}/accept/',
                               body, format='json')
        self.client.force_authenticate(user=None)
        self.assertEqual(res.status_code, 200, res.data)
        reg.refresh_from_db()
        return res

    def approved_owner(self, payload=None, or_number='1380093'):
        """An accepted registration plus the owner account it created.

        Paid first, because an approval with no receipt on file demands a stated
        reason — that rule belongs to the accept flow, not to this one, and
        satisfying it is cheaper than working around it.
        """
        reg = self.submit(payload)
        self.client.post('/api/vehicles/register/payment/',
                         {'token': str(reg.payment_token), 'or_number': or_number,
                          'or_receipt_image': SimpleUploadedFile(
                              'receipt.jpg', b'x' * 64, content_type='image/jpeg')},
                         format='multipart')
        self.accept(reg, or_number=or_number)
        owner = User.objects.get(email=reg.email)
        mail.outbox.clear()
        return reg, owner

    def as_owner(self, owner):
        self.client.force_authenticate(user=owner)

    def request_change(self, owner, **fields):
        self.as_owner(owner)
        res = self.client.post('/api/vehicles/registrations/my/changes/',
                               fields, format='json')
        self.client.force_authenticate(user=None)
        return res

    def decide(self, request_id, decision, note=''):
        self.as_admin()
        res = self.client.post(
            f'/api/vehicles/registrations/changes/{request_id}/{decision}/',
            {'note': note} if note else {}, format='json')
        self.client.force_authenticate(user=None)
        return res


# ──────────────────────────────────────────────
# The whitelist itself
# ──────────────────────────────────────────────

class WhatIsEditableTests(EditFlowTestCase):

    def test_a_student_is_offered_program_year_and_not_department(self):
        reg = self.submit()
        names = {f.name for f in editable_for(reg)}
        self.assertIn('program_year', names)
        self.assertNotIn('department', names)

    def test_an_employee_is_offered_department_and_not_program_year(self):
        reg = self.submit(employee_payload())
        names = {f.name for f in editable_for(reg)}
        self.assertIn('department', names)
        self.assertNotIn('program_year', names)

    def test_a_fetcher_is_offered_neither(self):
        """A fetcher is not enrolled and is not staff — they have neither field,
        and must not be able to acquire one by posting it."""
        reg = self.submit(fetcher_payload())
        names = {f.name for f in editable_for(reg)}
        self.assertNotIn('program_year', names)
        self.assertNotIn('department', names)

    def test_only_the_identifier_actually_in_use_is_editable(self):
        """A registration holds a plate or a conduction number, never both —
        offering the empty one is how a row ends up holding two."""
        plated = self.submit()
        self.assertIn('plate_number', {f.name for f in editable_for(plated)})
        self.assertNotIn('conduction_number', {f.name for f in editable_for(plated)})

        new_car = self.submit(student_payload(
            email='newcar@slc-sflu.edu.ph', plate_number='', conduction_number='CS12345A678',
            drivers_license='N01-20-800044'))
        names = {f.name for f in editable_for(new_car)}
        self.assertIn('conduction_number', names)
        self.assertNotIn('plate_number', names)

    def test_a_body_number_is_offered_only_to_a_tricycle(self):
        car = self.submit()
        self.assertNotIn('body_number', {f.name for f in editable_for(car)})
        trike = self.submit(student_payload(
            email='trike@slc-sflu.edu.ph', plate_number='TRI 0001',
            vehicle_type='Tricycle', body_number='B-77',
            drivers_license='N01-20-800055'))
        self.assertIn('body_number', {f.name for f in editable_for(trike)})

    def test_the_review_process_owns_status_payment_and_schedule(self):
        """The whitelist is a whitelist so that this list cannot be reached by
        anything, from either flow."""
        reg = self.submit()
        for field in ('status', 'payment_status', 'or_number', 'campus_days',
                      'schedule', 'registrant_type', 'email', 'student_level',
                      'system_student_id', 'user', 'amount_paid'):
            self.assertNotIn(field, EDITABLE_FIELDS, field)

    def test_a_read_only_field_is_refused_by_name_rather_than_ignored(self):
        """A silently dropped edit reads to the person as one that was saved."""
        reg = self.submit()
        changes, errors = clean_changes(reg, {'email': 'someone.else@gmail.com'})
        self.assertEqual(changes, {})
        self.assertIn('email', errors)
        self.assertEqual(errors['email'], READ_ONLY_REASONS['email'])

    def test_an_unchanged_value_is_not_a_change(self):
        reg = self.submit()
        changes, errors = clean_changes(reg, {'vehicle_color': 'BLUE'})
        self.assertEqual(errors, {})
        self.assertEqual(changes, {})


# ──────────────────────────────────────────────
# PENDING — the applicant corrects it themselves
# ──────────────────────────────────────────────

class PendingSelfEditTests(EditFlowTestCase):

    def test_the_form_lists_what_can_be_edited_and_what_cannot(self):
        reg = self.submit()
        res = self.self_edit_form(reg)
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['reference'], f'REG-{str(reg.pk).zfill(6)}')
        self.assertEqual(res.data['values']['vehicle_color'], 'BLUE')
        self.assertEqual(res.data['locked']['email'], reg.email)
        self.assertIn('plate_number', [f['field'] for f in res.data['editable']])

    def test_a_typo_is_corrected_in_place_with_no_approval(self):
        reg = self.submit()
        res = self.self_edit(reg, vehicle_color='RED', program_year='BSIT - 4')
        self.assertEqual(res.status_code, 200, res.data)
        reg.refresh_from_db()
        self.assertEqual(reg.vehicle_color, 'RED')
        self.assertEqual(reg.program_year, 'BSIT - 4')
        self.assertEqual(reg.status, VehicleRegistration.Status.PENDING)
        # No approval queue involved on this side.
        self.assertFalse(RegistrationChangeRequest.objects.exists())

    def test_the_response_says_what_changed_and_from_what(self):
        reg = self.submit()
        res = self.self_edit(reg, vehicle_color='RED')
        changed = {row['field']: row for row in res.data['changed']}
        self.assertEqual(changed['vehicle_color']['old'], 'BLUE')
        self.assertEqual(changed['vehicle_color']['new'], 'RED')
        self.assertEqual(changed['vehicle_color']['label'], 'Vehicle Colour')

    def test_a_mistyped_plate_is_fixable_and_normalised(self):
        reg = self.submit()
        res = self.self_edit(reg, plate_number='abc 1243')
        self.assertEqual(res.status_code, 200, res.data)
        reg.refresh_from_db()
        self.assertEqual(reg.plate_number, 'ABC1243')

    def test_a_plate_that_belongs_to_someone_else_is_refused(self):
        self.submit(student_payload(email='other@slc-sflu.edu.ph',
                                    plate_number='XYZ 9999',
                                    drivers_license='N01-20-800077'))
        reg = self.submit()
        res = self.self_edit(reg, plate_number='XYZ 9999')
        self.assertEqual(res.status_code, 400, res.data)
        self.assertIn('plate_number', res.data['errors'])
        reg.refresh_from_db()
        self.assertEqual(reg.plate_number, 'ABC1234')

    def test_a_malformed_licence_is_refused_with_the_format(self):
        reg = self.submit()
        res = self.self_edit(reg, drivers_license='not-a-licence')
        self.assertEqual(res.status_code, 400, res.data)
        self.assertIn('A00-00-000000', res.data['errors']['drivers_license'])

    def test_nothing_is_written_when_any_field_fails(self):
        """One bad field refuses the whole submission — a half-applied
        correction is worse than a refused one, because the person is told it
        worked."""
        reg = self.submit()
        res = self.self_edit(reg, vehicle_color='RED', drivers_license='rubbish')
        self.assertEqual(res.status_code, 400, res.data)
        reg.refresh_from_db()
        self.assertEqual(reg.vehicle_color, 'BLUE')

    def test_dropping_a_tricycle_type_clears_its_body_number(self):
        reg = self.submit(student_payload(
            email='trike2@slc-sflu.edu.ph', plate_number='TRI 0002',
            vehicle_type='Tricycle', body_number='B-12',
            drivers_license='N01-20-800088'))
        res = self.self_edit(reg, vehicle_type='Sedan')
        self.assertEqual(res.status_code, 200, res.data)
        reg.refresh_from_db()
        self.assertEqual(reg.vehicle_type, 'Sedan')
        self.assertEqual(reg.body_number, '')

    def test_an_employee_moving_department_moves_the_fee_with_it(self):
        """department_type is the column the fee reads — writing the label
        alone would move the department without the exemption following."""
        reg = self.submit(employee_payload())
        self.assertEqual(reg.payment_status, VehicleRegistration.PaymentStatus.UNPAID)
        res = self.self_edit(reg, department='Cleaning and Services')
        self.assertEqual(res.status_code, 200, res.data)
        reg.refresh_from_db()
        self.assertEqual(reg.department_type, 'cleaning_services')
        self.assertEqual(reg.pass_fee(), 0)

    def test_the_department_fk_follows_when_a_reference_item_exists(self):
        ReferenceItem.objects.get_or_create(category='department',
                                            name='Non-Teaching',
                                            defaults={'is_active': True})
        reg = self.submit(employee_payload())
        self.self_edit(reg, department='Non-Teaching')
        reg.refresh_from_db()
        self.assertEqual(reg.department_type, 'non_teaching')
        self.assertEqual(reg.department.name, 'Non-Teaching')

    def test_a_minor_cannot_have_their_authorized_driver_cleared(self):
        reg = self.submit(student_payload(
            email='minor@gmail.com', plate_number='MIN 0001',
            student_level='jhs', program_year='JHS - Grade 7',
            student_program='', student_year='',
            driver_name='DELA CRUZ, PEDRO', driver_relationship='parent',
            schedule='MWF', drivers_license='N01-20-800099'))
        res = self.self_edit(reg, driver_name='')
        self.assertEqual(res.status_code, 400, res.data)
        self.assertIn('driver_name', res.data['errors'])
        reg.refresh_from_db()
        self.assertEqual(reg.driver_name, 'DELA CRUZ, PEDRO')

    def test_a_named_driver_still_needs_a_relationship(self):
        reg = self.submit()
        res = self.self_edit(reg, driver_name='SANTOS, ANA')
        self.assertEqual(res.status_code, 400, res.data)
        self.assertIn('driver_relationship', res.data['errors'])

    def test_editing_stays_open_after_the_receipt_is_filed(self):
        """Paying is not the review, and no field on the whitelist changes the
        fee that was paid."""
        reg = self.submit()
        paid = self.client.post('/api/vehicles/register/payment/',
                                {'token': str(reg.payment_token),
                                 'or_number': '1380093',
                                 'or_receipt_image': SimpleUploadedFile(
                                     'receipt.jpg', b'x' * 64, content_type='image/jpeg')},
                                format='multipart')
        self.assertEqual(paid.status_code, 200, paid.data)
        res = self.self_edit(reg, vehicle_color='GREEN')
        self.assertEqual(res.status_code, 200, res.data)

    def test_cdsos_decision_closes_the_link(self):
        reg, _owner = self.approved_owner()
        res = self.self_edit(reg, vehicle_color='GREEN')
        self.assertEqual(res.status_code, 404, res.data)
        self.assertIn('CDSO Office', res.data['error'])

    def test_a_bad_token_is_a_404_not_a_500(self):
        for token in ('', 'not-a-uuid', '11111111-1111-1111-1111-111111111111'):
            res = self.client.post('/api/vehicles/register/details/',
                                   {'token': token, 'vehicle_color': 'RED'},
                                   format='json')
            self.assertEqual(res.status_code, 404, token)

    def test_a_correction_re_sends_the_acknowledgement(self):
        """The PDF is rebuilt from the row, so the copy the applicant is holding
        still shows the typo until this mail replaces it."""
        reg = self.submit()
        mail.outbox.clear()
        self.self_edit(reg, vehicle_color='RED')
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertIn('Updated', message.subject)
        self.assertEqual(message.to, [reg.email])
        self.assertIn('BLUE', message.body)
        self.assertIn('RED', message.body)
        self.assertTrue(any(name.lower().endswith('.pdf')
                            for name, _content, _type in message.attachments))

    def test_cdso_is_told_the_row_moved_under_them(self):
        reg = self.submit()
        Notification.objects.all().delete()
        self.self_edit(reg, vehicle_color='RED')
        self.assertTrue(Notification.objects.filter(event='registration_edited').exists())

    def test_an_empty_edit_is_a_no_op_rather_than_an_error(self):
        reg = self.submit()
        mail.outbox.clear()
        res = self.self_edit(reg, vehicle_color='BLUE')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['changed'], [])
        self.assertEqual(len(mail.outbox), 0)


# ──────────────────────────────────────────────
# ACCEPTED — the owner asks, CDSO decides
# ──────────────────────────────────────────────

class OwnerChangeRequestTests(EditFlowTestCase):

    def test_an_owners_edit_does_not_touch_the_registration_yet(self):
        reg, owner = self.approved_owner()
        res = self.request_change(owner, vehicle_color='RED')
        self.assertEqual(res.status_code, 201, res.data)
        reg.refresh_from_db()
        self.assertEqual(reg.vehicle_color, 'BLUE')
        change_request = RegistrationChangeRequest.objects.get(pk=res.data['id'])
        self.assertEqual(change_request.status,
                         RegistrationChangeRequest.Status.PENDING)
        self.assertEqual(change_request.changes, {'vehicle_color': 'RED'})
        self.assertEqual(change_request.requested_by, owner)

    def test_the_filed_request_records_what_it_was_changing_from(self):
        _reg, owner = self.approved_owner()
        res = self.request_change(owner, vehicle_color='RED')
        change_request = RegistrationChangeRequest.objects.get(pk=res.data['id'])
        self.assertEqual(change_request.previous, {'vehicle_color': 'BLUE'})

    def test_the_owner_dashboard_can_see_its_own_pending_request(self):
        _reg, owner = self.approved_owner()
        self.request_change(owner, vehicle_color='RED')
        self.as_owner(owner)
        res = self.client.get('/api/vehicles/registrations/my/changes/')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(len(res.data['requests']), 1)
        self.assertEqual(res.data['requests'][0]['status'], 'pending')
        self.assertEqual(res.data['values']['vehicle_color'], 'BLUE')

    def test_only_one_request_may_be_open_at_a_time(self):
        """Stacking them means approving in any order leaves the row holding
        whichever was decided last rather than what the reviewer read."""
        _reg, owner = self.approved_owner()
        self.assertEqual(self.request_change(owner, vehicle_color='RED').status_code, 201)
        second = self.request_change(owner, vehicle_color='GREEN')
        self.assertEqual(second.status_code, 409, second.data)
        self.assertEqual(RegistrationChangeRequest.objects.count(), 1)

    def test_cancelling_frees_the_owner_to_file_a_corrected_one(self):
        _reg, owner = self.approved_owner()
        first = self.request_change(owner, vehicle_color='RED')
        self.as_owner(owner)
        cancelled = self.client.post(
            f"/api/vehicles/registrations/my/changes/{first.data['id']}/cancel/")
        self.client.force_authenticate(user=None)
        self.assertEqual(cancelled.status_code, 200, cancelled.data)
        self.assertEqual(cancelled.data['status'], 'cancelled')
        self.assertEqual(self.request_change(owner, vehicle_color='GREEN').status_code, 201)

    def test_an_owner_cannot_cancel_somebody_elses_request(self):
        _reg, owner = self.approved_owner()
        filed = self.request_change(owner, vehicle_color='RED')
        _reg2, other = self.approved_owner(student_payload(
            email='second@slc-sflu.edu.ph', plate_number='SEC 2222',
            drivers_license='N01-20-800111'), or_number='1380094')
        self.as_owner(other)
        res = self.client.post(
            f"/api/vehicles/registrations/my/changes/{filed.data['id']}/cancel/")
        self.client.force_authenticate(user=None)
        self.assertEqual(res.status_code, 404, res.data)

    def test_an_empty_request_is_refused_rather_than_queued(self):
        _reg, owner = self.approved_owner()
        res = self.request_change(owner, vehicle_color='BLUE')
        self.assertEqual(res.status_code, 400, res.data)
        self.assertFalse(RegistrationChangeRequest.objects.exists())

    def test_a_guard_cannot_file_one(self):
        guard = User.objects.create_user(
            email='guard@slc.edu.ph', full_name='Guard', password='pw',
            role='security')
        self.as_owner(guard)
        res = self.client.post('/api/vehicles/registrations/my/changes/',
                               {'vehicle_color': 'RED'}, format='json')
        self.client.force_authenticate(user=None)
        self.assertEqual(res.status_code, 403, res.data)

    def test_an_anonymous_caller_cannot_file_one(self):
        res = self.client.post('/api/vehicles/registrations/my/changes/',
                               {'vehicle_color': 'RED'}, format='json')
        self.assertIn(res.status_code, (401, 403))

    def test_cdso_is_told_a_request_is_waiting(self):
        _reg, owner = self.approved_owner()
        Notification.objects.all().delete()
        self.request_change(owner, vehicle_color='RED')
        note = Notification.objects.filter(event='change_requested').first()
        self.assertIsNotNone(note)
        self.assertIn('Vehicle Colour', note.message)


class ReviewQueueTests(EditFlowTestCase):

    def test_the_queue_shows_pending_requests_with_who_and_what(self):
        reg, owner = self.approved_owner()
        self.request_change(owner, vehicle_color='RED')
        self.as_admin()
        res = self.client.get('/api/vehicles/registrations/changes/')
        self.client.force_authenticate(user=None)
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(len(res.data), 1)
        row = res.data[0]
        self.assertEqual(row['full_name'], reg.full_name)
        self.assertEqual(row['plate_number'], reg.plate_number)
        self.assertEqual(row['requested_by'], owner.full_name)
        self.assertEqual(row['changes'][0]['old'], 'BLUE')
        self.assertEqual(row['changes'][0]['new'], 'RED')

    def test_the_queue_is_admin_only(self):
        _reg, owner = self.approved_owner()
        self.as_owner(owner)
        res = self.client.get('/api/vehicles/registrations/changes/')
        self.client.force_authenticate(user=None)
        self.assertEqual(res.status_code, 403, res.data)

    def test_a_decided_request_leaves_the_pending_queue(self):
        _reg, owner = self.approved_owner()
        filed = self.request_change(owner, vehicle_color='RED')
        self.decide(filed.data['id'], 'approve')
        self.as_admin()
        pending = self.client.get('/api/vehicles/registrations/changes/')
        everything = self.client.get('/api/vehicles/registrations/changes/?status=all')
        self.client.force_authenticate(user=None)
        self.assertEqual(len(pending.data), 0)
        self.assertEqual(len(everything.data), 1)

    def test_an_unknown_status_filter_is_a_400(self):
        self.as_admin()
        res = self.client.get('/api/vehicles/registrations/changes/?status=banana')
        self.client.force_authenticate(user=None)
        self.assertEqual(res.status_code, 400, res.data)


class ApprovalAppliesTheChangeTests(EditFlowTestCase):

    def test_approval_writes_the_change_onto_the_registration(self):
        reg, owner = self.approved_owner()
        filed = self.request_change(owner, vehicle_color='RED')
        res = self.decide(filed.data['id'], 'approve')
        self.assertEqual(res.status_code, 200, res.data)
        reg.refresh_from_db()
        self.assertEqual(reg.vehicle_color, 'RED')
        self.assertEqual(res.data['status'], 'approved')

    def test_approval_carries_a_name_change_onto_the_account(self):
        """The portal and every notice address the owner from User.full_name."""
        reg, owner = self.approved_owner()
        filed = self.request_change(owner, full_name='DELA CRUZ, JUAN, S.')
        self.decide(filed.data['id'], 'approve')
        reg.refresh_from_db()
        owner.refresh_from_db()
        self.assertEqual(reg.full_name, 'DELA CRUZ, JUAN, S.')
        self.assertEqual(owner.full_name, 'DELA CRUZ, JUAN, S.')

    def test_approval_carries_a_colour_change_onto_the_vehicle(self):
        reg, owner = self.approved_owner()
        filed = self.request_change(owner, vehicle_color='RED')
        self.decide(filed.data['id'], 'approve')
        reg.refresh_from_db()
        self.assertEqual(reg.vehicle.color, 'RED')

    def test_approval_moves_the_plate_the_gate_resolves(self):
        """A corrected registration over a stale Vehicle row is an owner the
        gate still refuses."""
        reg, owner = self.approved_owner()
        old_vehicle_pk = reg.vehicle_id
        filed = self.request_change(owner, plate_number='ABC 1243')
        self.decide(filed.data['id'], 'approve')
        reg.refresh_from_db()

        self.assertEqual(reg.plate_number, 'ABC1243')
        resolved = Vehicle.resolve('ABC 1243')
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.user_id, owner.pk)
        self.assertTrue(resolved.is_authorized)
        self.assertEqual(reg.vehicle_id, resolved.pk)

        # The row the old plate lived on is released, not deleted — whatever
        # access history hangs off it stays — and stops admitting the car.
        self.assertNotEqual(old_vehicle_pk, resolved.pk)
        old = Vehicle.objects.get(pk=old_vehicle_pk)
        self.assertEqual(old.plate_number, 'ABC1234')
        self.assertIsNone(old.user_id)
        self.assertFalse(old.is_authorized)

    def test_a_vehicle_type_change_maps_onto_the_vehicles_own_choices(self):
        """The form's vocabulary is richer than Vehicle.Type — Tricycle is a
        motorcycle as far as parking is concerned."""
        reg, owner = self.approved_owner()
        filed = self.request_change(owner, vehicle_type='Motorcycle')
        self.decide(filed.data['id'], 'approve')
        reg.refresh_from_db()
        self.assertEqual(reg.vehicle.vehicle_type, Vehicle.Type.MOTORCYCLE)

    def test_approval_is_re_validated_against_the_row_as_it_stands(self):
        """A plate that was free when the request was filed may have been taken
        while it sat in the queue — that has to be a readable refusal, not an
        IntegrityError."""
        reg, owner = self.approved_owner()
        filed = self.request_change(owner, plate_number='TAK 3333')

        # Somebody else registers the plate in the meantime.
        self.submit(student_payload(email='taken@slc-sflu.edu.ph',
                                    plate_number='TAK 3333',
                                    drivers_license='N01-20-800222'))

        res = self.decide(filed.data['id'], 'approve')
        self.assertEqual(res.status_code, 409, res.data)
        self.assertIn('plate_number', res.data['errors'])
        reg.refresh_from_db()
        self.assertEqual(reg.plate_number, 'ABC1234')
        self.assertEqual(
            RegistrationChangeRequest.objects.get(pk=filed.data['id']).status,
            RegistrationChangeRequest.Status.PENDING)

    def test_a_request_whose_change_already_happened_is_simply_closed(self):
        reg, owner = self.approved_owner()
        filed = self.request_change(owner, vehicle_color='RED')
        # CDSO made the same edit another way in the meantime.
        reg.vehicle_color = 'RED'
        reg.save(update_fields=['vehicle_color'])

        res = self.decide(filed.data['id'], 'approve')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['status'], 'approved')

    def test_approval_tells_the_owner_what_is_now_true(self):
        _reg, owner = self.approved_owner()
        filed = self.request_change(owner, vehicle_color='RED')
        mail.outbox.clear()
        self.decide(filed.data['id'], 'approve')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Approved', mail.outbox[0].subject)
        self.assertIn('RED', mail.outbox[0].body)

    def test_a_plate_approval_warns_that_the_emailed_qr_is_stale(self):
        """The QR the owner saved from their approval mail encodes the old
        plate; the portal's is rebuilt from the record every time."""
        _reg, owner = self.approved_owner()
        filed = self.request_change(owner, plate_number='ABC 1243')
        mail.outbox.clear()
        self.decide(filed.data['id'], 'approve')
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn('Use your portal QR from now on', html)
        self.assertIn('no longer matches your record', html)

    def test_a_colour_approval_carries_no_stale_qr_warning(self):
        """The counterpart: the QR only goes stale when the identifier moves."""
        _reg, owner = self.approved_owner()
        filed = self.request_change(owner, vehicle_color='RED')
        mail.outbox.clear()
        self.decide(filed.data['id'], 'approve')
        html = mail.outbox[0].alternatives[0][0]
        self.assertNotIn('Use your portal QR from now on', html)

    def test_an_approved_request_still_reads_as_the_change_it_made(self):
        """Applying the change makes the row hold the new value, so a diff read
        live off it would render "RED -> RED" and tell the owner nothing. A
        decided request is rendered from its filing snapshot instead."""
        _reg, owner = self.approved_owner()
        filed = self.request_change(owner, vehicle_color='RED')
        self.decide(filed.data['id'], 'approve')

        self.as_admin()
        history = self.client.get('/api/vehicles/registrations/changes/?status=approved')
        self.client.force_authenticate(user=None)
        row = history.data[0]['changes'][0]
        self.assertEqual(row['old'], 'BLUE')
        self.assertEqual(row['new'], 'RED')

        # And the same on the owner's own view of their history.
        self.as_owner(owner)
        mine = self.client.get('/api/vehicles/registrations/my/changes/')
        self.client.force_authenticate(user=None)
        self.assertEqual(mine.data['requests'][0]['changes'][0]['old'], 'BLUE')
        self.assertEqual(mine.data['requests'][0]['changes'][0]['new'], 'RED')

    def test_a_waiting_request_reads_against_the_row_as_it_stands(self):
        """The counterpart: while it is still waiting, the diff has to describe
        what approving would overwrite — even if CDSO moved the row since."""
        reg, owner = self.approved_owner()
        filed = self.request_change(owner, vehicle_color='RED')
        reg.vehicle_color = 'GREEN'
        reg.save(update_fields=['vehicle_color'])

        self.as_admin()
        queue = self.client.get('/api/vehicles/registrations/changes/')
        self.client.force_authenticate(user=None)
        row = queue.data[0]['changes'][0]
        self.assertEqual(row['old'], 'GREEN')
        self.assertEqual(row['new'], 'RED')

    def test_approval_is_audited_with_the_before_and_after(self):
        _reg, owner = self.approved_owner()
        filed = self.request_change(owner, vehicle_color='RED')
        AuditLog.objects.all().delete()
        self.decide(filed.data['id'], 'approve')
        entry = AuditLog.objects.filter(details__contains='change request').first()
        self.assertIsNotNone(entry)
        self.assertIn('BLUE', entry.details)
        self.assertIn('RED', entry.details)

    def test_approval_is_admin_only(self):
        _reg, owner = self.approved_owner()
        filed = self.request_change(owner, vehicle_color='RED')
        self.as_owner(owner)
        res = self.client.post(
            f"/api/vehicles/registrations/changes/{filed.data['id']}/approve/")
        self.client.force_authenticate(user=None)
        self.assertEqual(res.status_code, 403, res.data)
        self.assertEqual(
            RegistrationChangeRequest.objects.get(pk=filed.data['id']).status,
            RegistrationChangeRequest.Status.PENDING)


class RejectionTests(EditFlowTestCase):

    def test_a_decline_needs_a_reason(self):
        """An owner told 'no' with no reason has nothing to correct and will
        file the same request again."""
        _reg, owner = self.approved_owner()
        filed = self.request_change(owner, vehicle_color='RED')
        res = self.decide(filed.data['id'], 'reject')
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(
            RegistrationChangeRequest.objects.get(pk=filed.data['id']).status,
            RegistrationChangeRequest.Status.PENDING)

    def test_a_decline_leaves_the_registration_alone_and_says_why(self):
        reg, owner = self.approved_owner()
        filed = self.request_change(owner, vehicle_color='RED')
        mail.outbox.clear()
        res = self.decide(filed.data['id'], 'reject',
                          note='Bring your OR/CR to the CDSO Office first.')
        self.assertEqual(res.status_code, 200, res.data)
        reg.refresh_from_db()
        self.assertEqual(reg.vehicle_color, 'BLUE')
        self.assertEqual(res.data['status'], 'rejected')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Declined', mail.outbox[0].subject)
        self.assertIn('OR/CR', mail.outbox[0].body)

    def test_a_declined_request_frees_the_owner_to_file_another(self):
        _reg, owner = self.approved_owner()
        filed = self.request_change(owner, vehicle_color='RED')
        self.decide(filed.data['id'], 'reject', note='Not this one.')
        self.assertEqual(self.request_change(owner, vehicle_color='GREEN').status_code, 201)

    def test_a_request_cannot_be_decided_twice(self):
        _reg, owner = self.approved_owner()
        filed = self.request_change(owner, vehicle_color='RED')
        self.decide(filed.data['id'], 'approve')
        again = self.decide(filed.data['id'], 'reject', note='changed my mind')
        self.assertEqual(again.status_code, 400, again.data)

    def test_deciding_a_request_that_does_not_exist_is_a_404(self):
        res = self.decide(999999, 'approve')
        self.assertEqual(res.status_code, 404, res.data)

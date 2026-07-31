"""Lifting a false-alarm violation renumbers what is left.

`offense_number` is stamped at creation and never revisited, so without
resequencing, lifting the 1st of two warnings leaves the survivor still reading
"offense 2" — wrong in the owner's list, and wrong for the ladder that decides
the 3rd-offense fee.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from vehicles.models import Vehicle
from violations.models import Violation, FEE_THIRD_OFFENSE

User = get_user_model()
UE = Violation.Type.UNAUTHORIZED_ENTRY


class LiftViolationTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            email='lift-admin@slc.edu.ph', full_name='ADMIN', password='x', role='admin')
        cls.guard = User.objects.create_user(
            email='lift-guard@slc.edu.ph', full_name='GUARD', password='x', role='security')
        cls.owner = User.objects.create_user(
            email='lift-owner@slc.edu.ph', full_name='OWNER', password='x', role='vehicle_owner')

    def setUp(self):
        self.vehicle = Vehicle.objects.create(
            plate_number='LIFT001', vehicle_type='car', user=self.owner)

    def _issue(self, n, vtype=UE, status_=Violation.Status.WARNING, fine='0.00'):
        # is_released=True matches how the API issues them — MyViolationsView
        # filters on it, so leaving it default hides the row from the owner.
        return Violation.objects.create(
            vehicle=self.vehicle, violation_type=vtype, offense_number=n,
            status=status_, fine_amount=Decimal(fine),
            is_released=True, issued_at=timezone.now(),
        )

    def _lift(self, v, reason='False alarm — camera artefact', as_user=None):
        self.client.force_authenticate(as_user or self.admin)
        return self.client.post(f'/api/violations/{v.id}/lift/', {'reason': reason}, format='json')

    # ── permissions ─────────────────────────────────────────────────────────
    def test_guard_cannot_lift(self):
        v = self._issue(1)
        self.assertEqual(self._lift(v, as_user=self.guard).status_code,
                         status.HTTP_403_FORBIDDEN)
        v.refresh_from_db()
        self.assertEqual(v.status, Violation.Status.WARNING)

    def test_owner_cannot_lift(self):
        v = self._issue(1)
        self.assertEqual(self._lift(v, as_user=self.owner).status_code,
                         status.HTTP_403_FORBIDDEN)

    def test_reason_is_required(self):
        v = self._issue(1)
        r = self._lift(v, reason='   ')
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        v.refresh_from_db()
        self.assertEqual(v.status, Violation.Status.WARNING)

    # ── the renumbering the user asked for ──────────────────────────────────
    def test_two_warnings_lift_first_survivor_becomes_warning_one(self):
        first, second = self._issue(1), self._issue(2)

        r = self._lift(first)
        self.assertEqual(r.status_code, status.HTTP_200_OK)

        first.refresh_from_db(); second.refresh_from_db()
        self.assertEqual(first.status, Violation.Status.LIFTED)
        self.assertEqual(second.offense_number, 1)          # stepped back down
        self.assertEqual(second.status, Violation.Status.WARNING)

    def test_two_warnings_lift_second_first_stays_warning_one(self):
        first, second = self._issue(1), self._issue(2)
        self._lift(second)
        first.refresh_from_db()
        self.assertEqual(first.offense_number, 1)
        self.assertEqual(first.status, Violation.Status.WARNING)

    def test_next_violation_after_a_lift_reuses_the_freed_number(self):
        first, second = self._issue(1), self._issue(2)
        self._lift(first)
        self.assertEqual(Violation.compute_offense_number(self.vehicle, UE), 2)

    def test_lifting_drops_a_third_offense_fee_back_to_a_warning(self):
        v1, v2 = self._issue(1), self._issue(2)
        v3 = self._issue(3, status_=Violation.Status.FEE_IMPOSED, fine=str(FEE_THIRD_OFFENSE))
        v3.registration_blocked = True
        v3.save(update_fields=['registration_blocked'])

        self._lift(v1)

        v3.refresh_from_db()
        self.assertEqual(v3.offense_number, 2)
        self.assertEqual(v3.status, Violation.Status.WARNING)
        self.assertEqual(v3.fine_amount, Decimal('0.00'))
        self.assertFalse(v3.registration_blocked)   # hold released with the fee

    def test_lifted_violation_is_zeroed_and_unblocked(self):
        v = self._issue(3, status_=Violation.Status.FEE_IMPOSED, fine=str(FEE_THIRD_OFFENSE))
        v.registration_blocked = True
        v.save(update_fields=['registration_blocked'])

        self._lift(v)

        v.refresh_from_db()
        self.assertEqual(v.status, Violation.Status.LIFTED)
        self.assertTrue(v.is_resolved)
        self.assertEqual(v.fine_amount, Decimal('0.00'))
        self.assertFalse(v.registration_blocked)
        self.assertEqual(v.lifted_by, self.admin)
        self.assertIsNotNone(v.lifted_at)
        self.assertIn('camera artefact', v.lifted_reason)

    # ── guard rails ─────────────────────────────────────────────────────────
    def test_cannot_lift_twice(self):
        v = self._issue(1)
        self.assertEqual(self._lift(v).status_code, status.HTTP_200_OK)
        self.assertEqual(self._lift(v).status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_lift_a_settled_violation(self):
        """Money changed hands — this action cannot invent a refund."""
        v = self._issue(3, status_=Violation.Status.CLEARED, fine=str(FEE_THIRD_OFFENSE))
        r = self._lift(v)
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        v.refresh_from_db()
        self.assertEqual(v.status, Violation.Status.CLEARED)

    def test_lifting_one_type_leaves_another_type_alone(self):
        ue = self._issue(1)
        dp = self._issue(1, vtype=Violation.Type.DOUBLE_PARKING)
        self._lift(ue)
        dp.refresh_from_db()
        self.assertEqual(dp.offense_number, 1)
        self.assertEqual(dp.status, Violation.Status.WARNING)

    def test_lifting_does_not_touch_another_vehicle(self):
        other_v = Vehicle.objects.create(plate_number='LIFT002', vehicle_type='car')
        mine  = self._issue(1)
        theirs = Violation.objects.create(
            vehicle=other_v, violation_type=UE, offense_number=1,
            status=Violation.Status.WARNING)
        self._lift(mine)
        theirs.refresh_from_db()
        self.assertEqual(theirs.offense_number, 1)
        self.assertEqual(theirs.status, Violation.Status.WARNING)

    # ── what the owner sees ─────────────────────────────────────────────────
    def test_owner_list_reflects_the_lift(self):
        first, second = self._issue(1), self._issue(2)
        self._lift(first)

        self.client.force_authenticate(self.owner)
        r = self.client.get('/api/violations/my/')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        rows = r.data if isinstance(r.data, list) else r.data.get('results', [])
        by_id = {row['id']: row for row in rows}
        self.assertEqual(by_id[first.id]['status'], 'lifted')
        self.assertEqual(by_id[second.id]['offense_number'], 1)

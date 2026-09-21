import logging
import os
import re
from django.conf import settings
from rest_framework import generics, permissions, status, filters
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView
from django.shortcuts import get_object_or_404
from django.db.models import Q
from django.utils import timezone
from time_utils import day_range, day_start, day_end, filter_local_date_range
from .email_utils import notify_password_set
from .models import User, AuditLog, Notification
from .twofa_api import HasRecentTwoFactor
from .serializers import (
    UserSerializer,
    UserUpdateSerializer,
    RegisterSerializer,
    AdminReplaceSerializer,
    GuardCreateSerializer,
    AdminOwnerCreateSerializer,
    CustomTokenObtainPairSerializer,
    AuditLogSerializer,
    NotificationSerializer,
)

logger = logging.getLogger(__name__)     # messages appear under "accounts.views"

# =============================================================================
# HOW TO READ THIS FILE
#
# Accounts: who exists, what they may do, and how they prove who they are.
# Roughly in the order it appears:
#
#   1. Permissions and audit helpers          (everything below uses these)
#   2. User administration                    create, edit, disable, delete
#   3. DashboardStatsView                     the admin home screen's numbers
#   4. Audit log, backup and RESTORE          the record, and the database itself
#   5. Self-service                           password change, own registration
#   6. Password reset                         request, email, confirm
#   7. Logins and notifications               guard QR, guard credentials
#
# Two things run through all of it. Every staff action writes an AuditLog row
# through `log_action` below — that trail is the point of most of this file.
# And "admin" IS the CDSO: there is no separate role, so the two permission
# classes below are the same check under two names.
# =============================================================================


# Where the request actually came from, for the audit trail.
def get_client_ip(request):
    """Extract client IP from request."""
    # Behind a proxy REMOTE_ADDR is the proxy; the forwarded header is a chain
    # "client, proxy1, proxy2", so the first entry is the original caller.
    x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded:
        return x_forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


# Records what a staff member did. Every administrative endpoint in this file
# calls it.
def log_action(request, action, target_user=None, details=''):
    """Create an audit log entry."""
    # No try/except, unlike scanning/views.py's _audit() which swallows
    # failures: at the gate a broken audit table must not stop a vehicle, but
    # here an unrecorded account change is worse than a failed request — and
    # inside an atomic block (see AdminReplaceView) the raise is what rolls the
    # whole action back.
    AuditLog.objects.create(
        actor=request.user,                  # who did it
        action=action,
        target_user=target_user,             # who it was done TO, when that differs
        details=details,                     # the sentence a reviewer will read
        ip_address=get_client_ip(request),
    )


# Paging for the user list. `max_page_size` is the guard that matters: without
# it a caller could ask for every account in one response.
class StandardResultsSetPagination(PageNumberPagination):
    page_size = 10                           # what a screen shows by default
    page_size_query_param = 'page_size'      # ...and the caller may ask for more
    max_page_size = 100                      # but no more than this


# The two permission classes, and they are the SAME CHECK under two names:
# admin IS the CDSO since the separate cdso role was removed. The second name
# is kept because it reads correctly at the endpoints that are about CDSO work.
#
# Noted, with no code changed: both classes are ALSO defined again in
# vehicles/views.py, and the two copies have drifted in form — that pair wraps
# the result in bool() and carries comments instead of docstrings. The effect
# is the same, because DRF only tests the returned value for truthiness and
# this chain yields None/False/bool in the cases that matter. The risk is not
# today's behaviour but tomorrow's: a real change to one copy would not reach
# the other. scanning/views.py imports IsAdminRole from HERE.
class IsAdminRole(permissions.BasePermission):
    """Allow access only to users with admin role."""
    def has_permission(self, request, view):
        # All three parts matter: an anonymous caller has no role, and reading
        # .role off one without the guard would raise.
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role == 'admin'
        )


class IsAdminOrCdso(permissions.BasePermission):
    """Allow access to the CDSO (admin) role."""
    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role == 'admin'    # identical to IsAdminRole above, deliberately — see the block comment
        )


# The ordinary email-and-password login. Only the serializer is replaced — it
# is what puts the role and the name into the token, so the frontend knows who
# it is dealing with without a second request.
class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer


# "Register" here means an ADMIN creating an account, not self-signup: note the
# permission class. Nobody creates their own account in this system.
class RegisterView(generics.CreateAPIView):
    queryset            = User.objects.all()
    serializer_class    = RegisterSerializer
    permission_classes  = [IsAdminRole]

    # perform_create rather than create(): the serializer does the work, and
    # this hook exists purely so the creation reaches the audit trail.
    def perform_create(self, serializer):
        user = serializer.save()
        log_action(self.request, AuditLog.Action.USER_CREATED, target_user=user)


# "Who am I?" — every signed-in screen calls this on load.
class MeView(generics.RetrieveAPIView):
    serializer_class   = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        # The caller's own row, never one named in the URL — so this endpoint
        # cannot be turned into a way to read somebody else's account.
        return self.request.user


# ──────────────────────────────────────────────
#  User Management (admin only)
# ──────────────────────────────────────────────

# The admin's user table: everyone except admins, searchable and filterable.
class UserListView(generics.ListAPIView):
    """List all users except admins, with optional ?search= by name."""
    serializer_class   = UserSerializer
    permission_classes = [IsAdminRole]
    pagination_class   = StandardResultsSetPagination

    def get_queryset(self):
        from django.db.models import Prefetch
        from vehicles.models import VehicleRegistration

        # The serializer only reads registrant_type off the earliest
        # registration, so fetch just those columns instead of every field
        # (registrations carry image/document fields we'd otherwise pull down).
        qs = (
            User.objects
            # Admins are excluded from their own management screen — the ways
            # to change an admin are AdminReplaceView and nothing else.
            .exclude(role='admin')
            .prefetch_related(Prefetch(
                'registrations',
                queryset=VehicleRegistration.objects.only(
                    'id', 'user_id', 'registrant_type',
                ).order_by('id'),
            ))
            .order_by('-id')             # newest accounts first, which is what an admin is usually looking for
        )
        # One box, three columns: whichever of the three the admin happens to
        # have — a name, an address, or the code printed on a badge.
        search = self.request.query_params.get('search', '').strip()
        if search:
            qs = qs.filter(
                Q(full_name__icontains=search) |
                Q(email__icontains=search) |
                Q(user_code__icontains=search)
            )

        # Each filter is checked against a fixed list rather than passed
        # through, so an unrecognised value is IGNORED rather than returning an
        # empty table that reads as "no such users".
        role = self.request.query_params.get('role', '').strip()
        if role in ['security', 'vehicle_owner']:   # 'admin' is deliberately not offered: they are excluded above
            qs = qs.filter(role=role)

        registrant_type = self.request.query_params.get('registrant_type', '').strip()
        if registrant_type in ['student', 'employee', 'fetcher']:
            # .distinct() because this filters through a to-many relation: an
            # owner with two registrations of the same type would appear twice.
            qs = qs.filter(registrations__registrant_type=registrant_type).distinct()

        # Written as two explicit branches rather than a boolean cast, so any
        # third value (or none) leaves the list unfiltered.
        status_param = self.request.query_params.get('status', '').strip()
        if status_param == 'active':
            qs = qs.filter(is_active=True)
        elif status_param == 'disabled':
            qs = qs.filter(is_active=False)

        return qs                            # unevaluated; the pagination class applies the LIMIT


class UserDetailView(generics.RetrieveAPIView):
    """Get a single user by ID."""
    queryset           = User.objects.all()
    serializer_class   = UserSerializer
    permission_classes = [IsAdminRole]


# Editing an account. Most of the body below is not the edit — it is working
# out what CHANGED, so the audit line can say so.
class UserUpdateView(generics.UpdateAPIView):
    """Edit user details (full_name, email, role, photo)."""
    queryset           = User.objects.all()
    serializer_class   = UserUpdateSerializer
    permission_classes = [IsAdminRole]

    def get_serializer_context(self):
        return {**super().get_serializer_context(), 'request': self.request}

    def perform_update(self, serializer):
        # Read BEFORE the save, while the old values are still on the instance.
        old_user = serializer.instance
        changes = []
        for field in ['full_name', 'email', 'role']:
            old_val = getattr(old_user, field)
            # Defaulting to the old value means a field the request did not
            # mention compares equal and is not reported as a change.
            new_val = serializer.validated_data.get(field, old_val)
            if old_val != new_val:
                changes.append(f"{field}: '{old_val}' → '{new_val}'")
        # The photo is noted as changed without quoting it — a file path in an
        # audit line tells a reader nothing.
        if 'photo' in serializer.validated_data:
            changes.append('photo updated')

        user = serializer.save()
        log_action(self.request, AuditLog.Action.USER_UPDATED, target_user=user, details='; '.join(changes))


class UserDeleteView(generics.DestroyAPIView):
    """Hard-delete a user, along with data that belongs to them (vehicles,
    registrations). Records that merely reference the user as an actor
    (audit logs, issued violations, scans, created events/notices) are left
    intact for accountability history — their FK just goes null."""
    queryset           = User.objects.all()
    serializer_class   = UserSerializer
    permission_classes = [IsAdminRole]

    def destroy(self, request, *args, **kwargs):
        from .models import delete_user_with_owned_records

        user = self.get_object()
        # An admin cannot be deleted here even by another admin. Replacing one
        # is AdminReplaceView's job, and it does the create and the delete
        # together so the system is never left without an admin.
        if user.role == 'admin':
            return Response(
                {'detail': 'Cannot delete an admin from this endpoint.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Logged before the delete: log_action reads the user it is pointed at.
        log_action(request, AuditLog.Action.USER_DELETED, target_user=user)
        # Sweeps what BELONGS to them (vehicles, registrations) and leaves what
        # merely references them as an actor — see the class docstring, and the
        # helper itself for which is which.
        delete_user_with_owned_records(user)
        return Response(status=status.HTTP_204_NO_CONTENT)   # 204: gone, nothing left to return


class UserToggleStatusView(APIView):
    """Toggle a user's is_active flag."""
    permission_classes = [IsAdminRole]

    def post(self, request, pk):
        user = get_object_or_404(User, pk=pk)
        if user.role == 'admin':
            return Response(
                {'detail': 'Cannot disable an admin account.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        old_status = user.is_active          # read but not used further; the new value is what the audit action is chosen from
        # A toggle, not a set: the caller says "flip this account" rather than
        # sending the state it wants, so two admins clicking at once cannot
        # both write the same value and think they each did something.
        user.is_active = not user.is_active
        user.save(update_fields=['is_active'])   # one column; nothing else on the account is touched
        # Two distinct audit actions rather than one with a detail string, so
        # the trail can be filtered for disablings specifically.
        action = AuditLog.Action.USER_ENABLED if user.is_active else AuditLog.Action.USER_DISABLED
        log_action(request, action, target_user=user)
        return Response(UserSerializer(user).data)


# The only way an admin account changes hands. The whole point is that it is
# ONE step: create the replacement and remove the incumbent together.
class AdminReplaceView(APIView):
    """Create a new admin and delete the current admin."""
    permission_classes = [IsAdminRole]

    def post(self, request):
        from django.db import transaction

        serializer = AdminReplaceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        old_admin = request.user
        # Create, log and delete are one unit: a failure partway through used to
        # leave the system with two admin accounts (new one created, old one
        # never removed). The credentials email is sent inside save() and cannot
        # be recalled on a rollback, but credentials for an account that no
        # longer exists are unusable — a stray mail beats a split admin state.
        with transaction.atomic():
            new_admin = serializer.save()
            # Inside the transaction on purpose: log_action does not swallow
            # failures, so a failed audit write rolls the replacement back
            # rather than leaving an unrecorded change of admin.
            log_action(request, AuditLog.Action.ADMIN_REPLACED, target_user=new_admin,
                       details=f"Replaced admin: {old_admin.email}")
            old_admin.delete()               # the incumbent, captured before the save above
        return Response(
            {
                'detail': 'Admin replaced successfully.',
                'user': UserSerializer(new_admin).data,
            },
            status=status.HTTP_201_CREATED,
        )


class AdminCreateGuardView(APIView):
    """Admin creates a security-guard account with email + password credentials.
    Guards log in at the dedicated guard gate login page (credentials or QR badge)."""
    permission_classes = [IsAdminRole]

    def post(self, request):
        serializer = GuardCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        guard = serializer.save()            # the serializer generates the password and emails it
        log_action(request, AuditLog.Action.USER_CREATED, target_user=guard,
                   details=f'Guard account created: {guard.full_name}')
        # context={'request': ...} so the serializer can build an absolute URL
        # for the photo; without it the frontend gets a path it cannot fetch.
        return Response(UserSerializer(guard, context={'request': request}).data,
                        status=status.HTTP_201_CREATED)


class AdminCreateOwnerView(APIView):
    """Admin creates a vehicle-owner account directly.  Password is auto-generated
    and emailed to the owner; must_change_password is set."""
    permission_classes = [IsAdminRole]

    def post(self, request):
        serializer = AdminOwnerCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        owner = serializer.save()
        log_action(request, AuditLog.Action.USER_CREATED, target_user=owner,
                   details=f'Vehicle-owner account created by admin: {owner.full_name}')
        return Response(UserSerializer(owner, context={'request': request}).data,
                        status=status.HTTP_201_CREATED)


class DashboardStatsView(APIView):
    """Return dashboard stats. Admin gets full overview; security gets personal scan stats."""
    permission_classes = [permissions.IsAuthenticated]

    # One endpoint, two completely different answers, chosen by role below.
    #
    # Read this method as a performance exercise as much as a reporting one.
    # The admin branch answers roughly forty separate questions, and at ~40ms
    # per round trip to Neon a naive version would take seconds. Almost every
    # block is therefore ONE aggregate using Count(filter=...) instead of a
    # series of .count() calls, and the comments below mark where that matters.
    #
    # Note, factually: the `else` branch is labelled 'security', but it is
    # reached by ANY non-admin role. A vehicle owner calling this gets
    # role='security' and their own (empty) scan figures rather than an error.
    # Recorded, not changed: this pass comments code.
    def get(self, request):
        from django.db.models import Count
        from django.utils import timezone
        from datetime import timedelta
        from scanning.models import AccessLog

        user = request.user
        today = timezone.localdate()         # campus-local, so "today" means today here
        week_ago = today - timedelta(days=7)
        # Half-open UTC bounds so the timestamp indexes are usable — a
        # `__date` lookup would force a per-row timezone conversion instead.
        today_start, today_end = day_range(today)
        week_start = day_start(week_ago)

        if user.role == 'admin':
            from django.db.models.functions import ExtractWeekDay
            from vehicles.models import Vehicle, VehicleRegistration

            # Every query is a ~40ms round-trip to the DB, so each block below is
            # collapsed into ONE aggregate using conditional Count(filter=...)
            # instead of a series of separate .count() calls.
            from django.db.models import Q

            # Eleven figures from one table in one statement. Written out as
            # eleven .count() calls this block alone would have been eleven
            # round trips before the page could render.
            u_agg = User.objects.aggregate(
                total=Count('id'),
                security=Count('id', filter=Q(role='security')),
                owners=Count('id', filter=Q(role='vehicle_owner')),
                active=Count('id', filter=Q(is_active=True)),
                disabled=Count('id', filter=Q(is_active=False)),
                # Archived (auto-expired) owners are also is_active=False; keep the
                # manually-disabled slice distinct from the archived slice so the
                # dashboard donut doesn't double-count them.
                owners_disabled=Count('id', filter=Q(role='vehicle_owner', is_active=False, is_archived=False)),
                owners_archived=Count('id', filter=Q(role='vehicle_owner', is_archived=True)),
                owners_banned=Count('id', filter=Q(role='vehicle_owner', registration_banned=True)),
                own_student=Count('id', filter=Q(role='vehicle_owner', is_active=True, owner_type='student')),
                own_employee=Count('id', filter=Q(role='vehicle_owner', is_active=True, owner_type='employee')),
                own_fetcher=Count('id', filter=Q(role='vehicle_owner', is_active=True, owner_type='fetcher')),
                own_visitor=Count('id', filter=Q(role='vehicle_owner', is_active=True, owner_type='visitor')),
            )
            # The names below are unpacked purely for readability further down;
            # nothing else happens here.
            total_users         = u_agg['total']
            security_count      = u_agg['security']
            vehicle_owner_count = u_agg['owners']
            active_users        = u_agg['active']
            disabled_users      = u_agg['disabled']
            owners_disabled     = u_agg['owners_disabled']

            # Vehicle-owner category breakdown (active owners split by owner_type,
            # plus a disabled slice) for the owners pie. Active-by-type + disabled
            # are mutually exclusive so they sum cleanly in a donut.
            owners_active_by_type = {
                'student':  u_agg['own_student'],
                'employee': u_agg['own_employee'],
                'fetcher':  u_agg['own_fetcher'],
                'visitor':  u_agg['own_visitor'],
            }

            v_agg = Vehicle.objects.aggregate(
                total=Count('id'),
                authorized=Count('id', filter=Q(is_authorized=True)),
            )
            total_vehicles        = v_agg['total']
            authorized_vehicles   = v_agg['authorized']
            # Subtracted rather than counted: two halves derived from one total
            # always sum to it, where a third COUNT could disagree with the
            # other two if a row changed between queries.
            unauthorized_vehicles = total_vehicles - authorized_vehicles

            # Vehicle-type breakdown for the vehicle-types chart.
            #
            # vehicle_type is free text and has been written with inconsistent
            # casing ("Motorcycle" and "motorcycle" both exist, plus values like
            # "SUV" that are not in any fixed list). Grouping on the raw column
            # therefore returned split buckets, and the dashboard — which matched
            # a hardcoded lowercase set — silently dropped everything it did not
            # recognise. The chart showed 4 of 9 vehicles while its centre label
            # read the true total. Fold to lowercase here so each type is counted
            # once and the slices always add up to `total`.
            vehicles_by_type = {}
            for row in Vehicle.objects.values('vehicle_type').annotate(count=Count('id')):
                # Two fallbacks: `or ''` for a NULL column, then `or 'unknown'`
                # for one that is blank or only whitespace. Everything lands in
                # a named bucket, which is what makes the slices sum to total.
                key = (row['vehicle_type'] or '').strip().lower() or 'unknown'
                # Accumulated with += rather than assigned, because "Motorcycle"
                # and "motorcycle" both fold onto the same key here.
                vehicles_by_type[key] = vehicles_by_type.get(key, 0) + row['count']

            # Suppliers are their own model (not User owners) but form a registered
            # vehicle category alongside students/employees/fetchers. Counted by
            # plate (supplier vehicles), scoped to active supplier companies.
            from vehicles.models import SupplierPlate
            active_suppliers = SupplierPlate.objects.filter(supplier__is_active=True).count()

            # One aggregate query gives the full per-status picture for today
            today_by_status = {
                row['status']: row['count']
                for row in AccessLog.objects.filter(scanned_at__gte=today_start,
                                                    scanned_at__lt=today_end)
                                            .values('status').annotate(count=Count('id'))
            }
            # Every figure below is derived from that one dict, so the parts
            # cannot disagree with the whole. .get(..., 0) throughout because a
            # status with no rows today simply does not appear in the result.
            today_scans      = sum(today_by_status.values())
            authorized_today = today_by_status.get('authorized', 0)
            denied_today     = today_by_status.get('denied', 0) + today_by_status.get('wrong_day', 0)   # a wrong-day refusal is still a refusal
            unknown_today    = today_by_status.get('unknown', 0)

            # Week total + today's visitor-pass entries in a single pass.
            # (Visitor passes create an ownerless vehicle, so user is null.)
            al_agg = AccessLog.objects.filter(scanned_at__gte=week_start).aggregate(
                week=Count('id'),
                visitor_today=Count('id', filter=Q(
                    scanned_at__gte=today_start, scanned_at__lt=today_end,
                    status='authorized', vehicle__user__isnull=True,
                )),
            )
            week_scans       = al_agg['week']
            visitor_today    = al_agg['visitor_today']
            # Subtracted, so the two always add up to authorized_today rather
            # than being two counts that might drift apart.
            registered_today = authorized_today - visitor_today

            # Day distribution: authorized entries per weekday (Mon–Sat)
            # Django ExtractWeekDay: 1=Sunday, 2=Monday, …, 7=Saturday
            # Sunday (1) is deliberately absent: the campus is closed, so it
            # is not a bar on the chart rather than a bar reading zero.
            DAY_MAP = {2: 'Mon', 3: 'Tue', 4: 'Wed', 5: 'Thu', 6: 'Fri', 7: 'Sat'}
            day_rows = (
                AccessLog.objects
                .filter(status='authorized', scanned_at__gte=week_start)
                .annotate(wd=ExtractWeekDay('scanned_at'))
                .filter(wd__in=DAY_MAP.keys())
                .values('wd')
                .annotate(count=Count('id'))
            )
            day_dist_map = {row['wd']: row['count'] for row in day_rows}
            # Built by walking DAY_MAP rather than the rows, so a weekday with
            # no scans still appears as a zero bar instead of a gap in the chart.
            day_distribution = [
                {'day': DAY_MAP[wd], 'count': day_dist_map.get(wd, 0)}
                for wd in sorted(DAY_MAP.keys())
            ]
            # Note, factually: this sums the Mon–Sat rows only, because the
            # query above filtered to DAY_MAP's weekdays. `week_scans` a few
            # lines up counts EVERY day. On a campus closed Sunday the two
            # normally agree, but they are not the same measure — anything
            # scanned on a Sunday is in `week` and not in `authorized_week`.
            # Recorded, not changed.
            authorized_week = sum(day_dist_map.values())

            # Registration totals + per-day load (Mon–Sat) in ONE query. This
            # previously ran 6 days x 2 statuses = 12 separate count queries.
            from vehicles.views import SCHEDULE_SLOT_LIMIT
            WEEK_DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
            _ACC, _PEN = VehicleRegistration.Status.ACCEPTED, VehicleRegistration.Status.PENDING
            # The aggregate is BUILT rather than written out: three status
            # totals plus two per weekday, so fifteen figures from one
            # statement where this once ran twelve separate counts.
            _reg_expr = {
                'pending':  Count('id', filter=Q(status=_PEN)),
                'accepted': Count('id', filter=Q(status=_ACC)),
                'rejected': Count('id', filter=Q(status=VehicleRegistration.Status.REJECTED)),
            }
            for _i, _day in enumerate(WEEK_DAYS):
                # Keyed by INDEX (d0_acc, d1_acc…), not by day name: aggregate
                # aliases must be valid identifiers and must not collide with a
                # model field, and an index is guaranteed to be both.
                _reg_expr[f'd{_i}_acc'] = Count('id', filter=Q(campus_days__contains=[_day], status=_ACC))
                _reg_expr[f'd{_i}_pen'] = Count('id', filter=Q(campus_days__contains=[_day], status=_PEN))
            # campus_days is JSON; `__contains` is answered by the GIN index on
            # that column (see the VehicleRegistration Meta).
            reg_agg = VehicleRegistration.objects.aggregate(**_reg_expr)

            pending_registrations  = reg_agg['pending']
            accepted_registrations = reg_agg['accepted']
            rejected_registrations = reg_agg['rejected']
            day_registrations = [
                {
                    'day':      _day,            # the name goes out here; the index was only an alias
                    'accepted': reg_agg[f'd{_i}_acc'],
                    'pending':  reg_agg[f'd{_i}_pen'],
                    # Sent with every row so the chart draws its capacity line
                    # from the same constant the submit handler enforces.
                    'capacity': SCHEDULE_SLOT_LIMIT,
                }
                for _i, _day in enumerate(WEEK_DAYS)
            ]

            # Violations & visitor passes — surfaced on the dashboard KPI strip
            from violations.models import Violation
            from scanning.models import VisitorPass
            viol_agg = Violation.objects.aggregate(
                open=Count('id', filter=Q(is_resolved=False)),
                fee=Count('id', filter=Q(status=Violation.Status.FEE_IMPOSED)),
            )
            open_violations = viol_agg['open']
            fee_imposed     = viol_agg['fee']
            active_passes   = VisitorPass.objects.filter(
                valid_date=today, status=VisitorPass.Status.ACTIVE   # today's, and not yet exited
            ).count()

            # Violation breakdown by type (last 30 days) for the violations trend chart
            month_ago = today - timedelta(days=30)
            # Note, factually: this uses `issued_at__date__gte`, the exact
            # lookup the comment at the top of this method says is avoided
            # because it forces a per-row timezone conversion and stops the
            # timestamp index being used. Everything else here takes half-open
            # bounds from day_range()/day_start(); this one query does not.
            # Recorded, not changed: this pass comments code.
            violations_by_type = {
                row['violation_type']: row['count']
                for row in Violation.objects.filter(issued_at__date__gte=month_ago)
                                            .values('violation_type').annotate(count=Count('id'))
            }

            # Two separate lists rather than one mixed feed: the dashboard
            # shows staff activity and guard activity side by side, and they
            # answer different questions.
            recent_admin_logs    = AuditLog.objects.select_related('actor', 'target_user').filter(
                actor__role='admin'          # both relations are rendered per row, so they are joined in
            ).order_by('-created_at')[:10]
            recent_security_logs = AuditLog.objects.select_related('actor', 'target_user').filter(
                actor__role='security'
            ).order_by('-created_at')[:10]

            data = {
                'role': 'admin',
                'users': {
                    'total':         total_users,
                    'security':      security_count,
                    'vehicle_owner': vehicle_owner_count,
                    'active':        active_users,
                    'disabled':      disabled_users,
                },
                'vehicles': {
                    'total':        total_vehicles,
                    'authorized':   authorized_vehicles,
                    'unauthorized': unauthorized_vehicles,
                    'by_type':      vehicles_by_type,
                },
                'registrations': {
                    'pending':  pending_registrations,
                    'accepted': accepted_registrations,
                    'rejected': rejected_registrations,
                    # Summed from the three above rather than counted, so the
                    # total can never disagree with the parts on the screen.
                    # Note this means EXPIRED registrations are not included.
                    'total':    pending_registrations + accepted_registrations + rejected_registrations,
                },
                'owners': {
                    'student':  owners_active_by_type.get('student', 0),
                    'employee': owners_active_by_type.get('employee', 0),
                    'fetcher':  owners_active_by_type.get('fetcher', 0),
                    'visitor':  owners_active_by_type.get('visitor', 0),
                    'disabled': owners_disabled,
                    'archived': u_agg['owners_archived'],
                    'banned':   u_agg['owners_banned'],
                    'total':    vehicle_owner_count,
                },
                'suppliers': {
                    'active': active_suppliers,
                },
                'scans': {
                    'today':            today_scans,
                    'week':             week_scans,
                    'authorized_today': authorized_today,
                    'authorized_week':  authorized_week,
                    'registered_today': registered_today,
                    'visitor_today':    visitor_today,
                    'denied_today':     denied_today,
                    'unknown_today':    unknown_today,
                    'today_by_status':  today_by_status,
                },
                'violations': {
                    'open':        open_violations,
                    'fee_imposed': fee_imposed,
                    'by_type':     violations_by_type,
                },
                'visitor_passes': {
                    'active_today': active_passes,
                },
                'day_distribution': day_distribution,
                'day_registrations': day_registrations,
                'recent_activity': {
                    'admin':    AuditLogSerializer(recent_admin_logs, many=True).data,
                    'security': AuditLogSerializer(recent_security_logs, many=True).data,
                },
            }
        else:
            # Every non-admin role, not just guards — see the note at the top
            # of this method. Scoped to `scanned_by=user` throughout, so this
            # branch can only ever report the caller's own work.
            #
            # All five figures in one pass instead of five round-trips.
            from django.db.models import Q
            my_agg = AccessLog.objects.filter(scanned_by=user).aggregate(
                total=Count('id'),
                today=Count('id', filter=Q(scanned_at__gte=today_start, scanned_at__lt=today_end)),
                week=Count('id', filter=Q(scanned_at__gte=week_start)),
                authorized=Count('id', filter=Q(scanned_at__gte=today_start, scanned_at__lt=today_end,
                                                status='authorized')),
                denied=Count('id', filter=Q(scanned_at__gte=today_start, scanned_at__lt=today_end,
                                            status__in=['denied', 'wrong_day'])),
            )
            my_scans_today = my_agg['today']
            my_scans_week  = my_agg['week']
            my_total_scans = my_agg['total']
            my_authorized  = my_agg['authorized']
            my_denied      = my_agg['denied']

            # select_related: the serializer reads scanned_by / on_duty_guard /
            # vehicle.user, which would otherwise fire ~3 extra queries per row.
            my_access_logs = (
                AccessLog.objects
                .select_related('scanned_by', 'on_duty_guard', 'vehicle', 'vehicle__user')
                .filter(scanned_by=user)
                .order_by('-scanned_at')[:10]
            )

            # Imported here, shadowing the AuditLogSerializer-era import at
            # the top of the file — a different serializer for a different model.
            from scanning.serializers import AccessLogSerializer
            data = {
                'role': 'security',          # what the frontend switches its layout on
                'scans': {
                    'today':            my_scans_today,
                    'week':             my_scans_week,
                    'total':            my_total_scans,
                    'authorized_today': my_authorized,
                    'denied_today':     my_denied,
                },
                'recent_scans': AccessLogSerializer(my_access_logs, many=True).data,
            }

        return Response(data)


# ──────────────────────────────────────────────
#  Audit Log Views
# ──────────────────────────────────────────────

# ── The audit log, and reports of it ────────────────────────────────────────
# The screen and the two exports all read through _filter_audit_logs below, so
# a downloaded report cannot disagree with the table it was taken from.

# A thin wrapper, kept so the audit views name the column once. The real work
# is in time_utils: campus-local dates turned into half-open UTC bounds, which
# an index can serve and which ignore an unparseable date rather than raising.
def _apply_created_at_range(qs, date_from, date_to):
    """Inclusive local-date range filter on AuditLog.created_at."""
    return filter_local_date_range(qs, 'created_at', date_from, date_to)


class AuditLogListView(generics.ListAPIView):
    """List audit logs - admin only.

    This lists administrative actions only. Vehicle-owner gate movement is
    deliberately not recorded here (see AuditLog's docstring); the operational
    gate history lives in scanning.AccessLog.
    """
    serializer_class   = AuditLogSerializer
    permission_classes = [IsAdminRole]
    pagination_class   = StandardResultsSetPagination

    def get_queryset(self):
        # [0] discards filters_desc — that exists for the report subtitles, and
        # a paginated table has nowhere to put it.
        return _filter_audit_logs(self.request)[0]


def _filter_audit_logs(request):
    """Apply the same filters the Audit Log UI uses.

    Returns (ordered_queryset, filters_desc) so the Excel and PDF exports stay
    identical to what the operator sees on screen.
    """
    # Both relations are rendered on every row and every report line, so they
    # are joined in rather than fetched one query per row.
    qs = AuditLog.objects.select_related('actor', 'target_user').all()
    action    = request.query_params.get('action', '').strip()
    date_from = request.query_params.get('date_from', '').strip()
    date_to   = request.query_params.get('date_to', '').strip()
    search    = request.query_params.get('search', '').strip()
    if action:
        # Not checked against the choices: an unrecognised action simply
        # matches nothing, and an empty audit log for a filter nobody set is
        # harmless — unlike a 400 on a screen an admin is trying to read.
        qs = qs.filter(action=action)
    qs = _apply_created_at_range(qs, date_from, date_to)
    if search:
        # Three ways to name the person who ACTED, plus the free text.
        #
        # Note, factually: target_user is joined above and rendered, but is not
        # searched. Looking somebody up by name therefore finds what they did,
        # and finds what was done TO them only where their name happens to
        # appear in `details` — which log_action often includes, but not
        # always. Recorded, not changed: this pass comments code.
        qs = qs.filter(
            Q(actor__user_code__icontains=search) |
            Q(actor__full_name__icontains=search) |
            Q(actor__email__icontains=search) |
            Q(details__icontains=search)
        )

    # The filter written out in words, so a printed report states on its face
    # what was excluded from it.
    action_labels = dict(AuditLog.Action.choices)
    filters_desc = []
    if action:
        filters_desc.append(f"Action: {action_labels.get(action, action)}")   # the readable label, falling back to the raw value
    if date_from or date_to:
        filters_desc.append(f"Period: {date_from or 'start'} to {date_to or 'today'}")
    if search:
        filters_desc.append(f"Search: '{search}'")
    # Newest first, so the row cap each caller applies keeps the most recent.
    return qs.order_by('-created_at'), filters_desc


# One column set for both formats, so the Excel and the PDF cannot drift apart.
AUDIT_REPORT_HEADERS = ['#', 'Date & Time', 'Actor', 'Role', 'Action', 'Details']


# Turns audit rows into the flat cells both report formats take.
def _audit_report_rows(qs):
    from django.utils import timezone as tz
    action_labels = dict(AuditLog.Action.choices)   # built once, outside the loop
    rows = []
    for i, log in enumerate(qs, start=1):    # start=1 so '#' reads as a human numbering
        # 'System' for a null actor: AuditLog.actor is nullable, both because
        # scheduled jobs write rows with nobody behind them and because
        # deleting an account nulls the FK while leaving its history. An empty
        # cell would read as a rendering fault rather than as "no person".
        actor = log.actor.full_name if log.actor else 'System'
        role  = (log.actor.role if log.actor else '').replace('_', ' ').title()   # 'vehicle_owner' -> 'Vehicle Owner'
        rows.append([
            i,
            tz.localtime(log.created_at).strftime('%b %d, %Y %I:%M:%S %p'),   # campus-local, to the second: ordering matters in an audit trail
            actor, role,
            action_labels.get(log.action, log.action),   # falls back to the stored value for an action since renamed
            log.details or '',               # '' not None, so the cell renders empty rather than as the word "None"
        ])
    return rows


class AuditLogExportView(APIView):
    """Download the (filtered) audit log as a branded Excel report — admin only."""
    permission_classes = [IsAdminRole]

    def get(self, request):
        from django.utils import timezone as tz
        from report_utils import branded_excel_response, report_filename
        qs, filters_desc = _filter_audit_logs(request)
        # The slice reaches the database as a LIMIT, so len(rows) below counts
        # what is actually in the file — which is what the subtitle states.
        rows = _audit_report_rows(qs[:5000])
        # The Excel subtitle carries who generated it and when; the PDF below
        # does not, because branded_pdf_response takes generated_by separately
        # and prints it itself.
        subtitle = (f"Generated {tz.localtime().strftime('%B %d, %Y %I:%M %p')} "
                    f"by {getattr(request.user, 'full_name', '')} · "   # getattr with a default: an unnamed account must not break a download
                    + ('; '.join(filters_desc) if filters_desc else 'All records')
                    + f" · {len(rows)} entries")
        return branded_excel_response(
            filename=report_filename('Audit Log Report', 'xlsx'),
            sheet_title='Audit Log',
            report_title='Audit Log Report',
            subtitle=subtitle,
            headers=AUDIT_REPORT_HEADERS,
            rows=rows,
            col_widths=[5, 21, 24, 12, 20, 95],   # characters; Details takes most of it, being the free-text column
        )


class AuditLogPdfExportView(APIView):
    """Download the (filtered) audit log as a branded PDF report — admin only."""
    permission_classes = [IsAdminRole]

    def get(self, request):
        from django.utils import timezone as tz
        from report_utils import branded_pdf_response, report_filename
        qs, filters_desc = _filter_audit_logs(request)
        rows = _audit_report_rows(qs[:5000])
        subtitle = (('; '.join(filters_desc) if filters_desc else 'All records')
                    + f" · {len(rows)} entries")
        return branded_pdf_response(
            filename=report_filename('Audit Log Report', 'pdf'),
            report_title='Audit Log Report',
            subtitle=subtitle,
            generated_by=getattr(request.user, 'full_name', ''),
            headers=AUDIT_REPORT_HEADERS,
            rows=rows,
            # Date & Time needs 91pt but only had 86pt, so every single row
            # wrapped to two lines — doubling the height of the whole report.
            # Actor was using 64pt of its 109pt, so 5mm moves across and both
            # fit comfortably. Total is unchanged at 267mm (the printable width).
            col_widths_mm=[10, 39, 37, 22, 38, 121],
        )


# ── System Backup & Restore ─────────────────────────────
# The app list, the exclusions and the on-disk layout live in backup_utils so
# the scheduled job (vehicles.tasks.auto_backup) and the buttons on this page
# produce exactly the same kind of file. Re-exported here because tests and
# other modules have imported these names from this module since before that
# helper existed.
# noqa: E402 silences "module level import not at top of file" — this one is
# deliberately here, under the header above, rather than with the other imports.
from .backup_utils import (                                        # noqa: E402
    AUTO_PREFIX, BACKUP_APPS, BACKUP_EXCLUDE, MANUAL_PREFIX, SAFETY_PREFIX,
    dump_backup, list_backups, load_backup, prune_backups, safe_path, stamp,
    write_backup,
)


class SystemBackupView(APIView):
    """Download a JSON snapshot of all application data — admin (CDSO) only.

    Step-up protected: the file contains every account, plate and registration
    in the system, so one click on a borrowed session is a full data breach.
    `step_up_on_read` opts this GET into the check, which otherwise exempts
    safe methods.
    """
    # ── Backup and restore ──────────────────────────────────────────────
    # Read the four classes from here to SystemRestoreView as one unit. They
    # are the only endpoints that can lose data, and each safety measure below
    # is load-bearing — the comments say what a missing line would cost.
    #
    # The step-up pattern across them is worth noticing: it is keyed to what
    # the DATA can do, not to the HTTP verb. Reading a backup is a GET and is
    # step-up protected, because the file is every account and plate in the
    # system. Listing filenames is a GET and is not, because filenames are not
    # data.
    permission_classes = [IsAdminRole, HasRecentTwoFactor]
    # Without this line the step-up would be skipped: HasRecentTwoFactor
    # exempts safe methods unless a view opts in, and this GET hands over the
    # entire database.
    step_up_on_read = True

    def get(self, request):
        from django.http import HttpResponse
        from vehicles.models import SystemSettings

        payload = dump_backup()              # the whole fixture, built once and used for both the file and the download

        # Keep a copy on the server as well. A manual download is the moment the
        # data was known-good enough for someone to want it saved, and keeping
        # it means the restore list is not empty on a system where the automatic
        # schedule has only just been switched on. Rotated by the same keep count
        # as the automatic ones, so repeated downloads cannot fill the disk. A
        # disk problem here must not cost the admin the download they asked for.
        # The SAME payload is written and returned, so the server's copy and
        # the admin's download are provably the same bytes rather than two
        # dumps taken a moment apart.
        try:
            write_backup(MANUAL_PREFIX, payload)
            prune_backups(SystemSettings.get().auto_backup_keep)
        except OSError:
            # Caught narrowly (OSError = the disk), and deliberately swallowed:
            # the copy is a convenience, and failing the request would cost the
            # admin the download they actually asked for. Logged with the
            # traceback so a full disk is visible rather than silent.
            logger.warning("Could not keep a server-side copy of the manual backup", exc_info=True)

        response = HttpResponse(payload, content_type='application/json')
        response['Content-Disposition'] = (
            f'attachment; filename="slc-vms-backup-{stamp(seconds=False)}.json"')   # attachment, so a browser saves it rather than rendering megabytes of JSON
        # Audited even though nothing changed: this is the request that puts
        # every account and plate onto somebody's laptop, so it is exactly the
        # kind of read that has to leave a trace.
        log_action(request, AuditLog.Action.RECORD_UPDATED, details='System backup downloaded')
        return response


class SystemBackupListView(APIView):
    """The backup files sitting on the server — admin (CDSO) only.

    Covers the automatic backups, the copy kept on each manual download, and the
    pre-restore safety snapshots. This reads filenames and sizes, not the data
    inside them, so it needs admin but no step-up; downloading one of them does.
    """
    permission_classes = [IsAdminRole]

    def get(self, request):
        from vehicles.models import SystemSettings

        cfg = SystemSettings.get()
        return Response({
            'backups': list_backups(),
            'auto_backup_frequency': cfg.auto_backup_frequency,
            'auto_backup_keep': cfg.auto_backup_keep,
        })


class SystemBackupFileView(APIView):
    """Download or delete one saved backup file — admin (CDSO) only.

    Same step-up as taking a fresh backup, and for the same reason: the file
    holds every account and plate in the system whether it was written a minute
    ago or last term.
    """
    permission_classes = [IsAdminRole, HasRecentTwoFactor]
    step_up_on_read = True

    def get(self, request, name):
        from django.http import FileResponse

        # `name` comes straight from the URL. safe_path is the only thing
        # standing between that and the filesystem: it rejects anything with a
        # separator by regex AND re-checks the resolved realpath is still
        # directly inside the backups directory, which is what catches a
        # symlink. Without it this endpoint reads any file the server can.
        path = safe_path(name)
        if not path:
            # One message for "not a safe name" and "does not exist" alike, so
            # a caller cannot probe the filesystem by reading the difference.
            return Response({'error': 'Backup file not found.'},
                            status=status.HTTP_404_NOT_FOUND)
        log_action(request, AuditLog.Action.RECORD_UPDATED,
                   details=f'Saved backup downloaded ({name})')
        return FileResponse(open(path, 'rb'), as_attachment=True,
                            filename=name, content_type='application/json')

    def delete(self, request, name):
        import os

        # The same guard as the download, and it matters more here: this call
        # removes a file. safe_path is what stops `name` naming anything
        # outside the backups directory.
        path = safe_path(name)
        if not path:
            return Response({'error': 'Backup file not found.'},
                            status=status.HTTP_404_NOT_FOUND)
        try:
            os.remove(path)
        except OSError as exc:
            # Reported rather than swallowed: unlike the convenience copy in
            # SystemBackupView, the admin asked for THIS file to be gone and
            # must be told if it is still there.
            return Response({'error': f'Could not delete the file. ({exc})'},
                            status=status.HTTP_400_BAD_REQUEST)
        log_action(request, AuditLog.Action.RECORD_UPDATED,
                   details=f'Saved backup deleted ({name})')
        return Response({'deleted': name}, status=status.HTTP_200_OK)


class SystemRestoreView(APIView):
    """Merge a JSON backup into the live data — admin (CDSO) only.

    Takes either an uploaded file (`file`) or the name of a backup already on
    the server (`filename`) — an automatic one, or a pre-restore snapshot from
    an earlier attempt. Both paths run the same validation, the same safety
    snapshot and the same atomic load; a saved file simply skips the download /
    re-upload round trip.

    "Restore" overstates it, and the difference matters. The load writes each
    record by primary key: rows in the file overwrite the matching live rows and
    missing ones are inserted, but nothing is ever deleted. An account or
    vehicle created after the backup was taken therefore survives the restore
    untouched. This is a merge, not a rewind to the backup's date, and the
    confirmation dialog says so in those words.

    Making it a true rewind would mean emptying every backed-up table first.
    That is technically possible — the excluded ML tables carry no foreign keys
    into these, so nothing would cascade unnoticed — but it turns a recoverable
    operation into one that destroys everything created since the file was
    written, including the row of the admin performing it. It is not a change to
    make quietly; the honest description above is the safer half of the trade.

    Safety measures: admin-only, a fresh two-factor step-up, file validated as a
    JSON fixture, an automatic pre-restore snapshot of current data saved to
    disk, and a load that runs in a single transaction and rolls back completely
    on any error.

    The step-up is the one that matters most on this endpoint: a restore
    overwrites live data wholesale, and a crafted fixture can rewrite the admin
    account itself.
    """
    permission_classes = [IsAdminRole, HasRecentTwoFactor]
    # No step_up_on_read here, and none needed: this view only answers POST,
    # which HasRecentTwoFactor checks without being asked.

    # The order of this method IS the safety. Read it as five steps, and note
    # what each one costs if it were not there:
    #
    #   1. get the bytes            (upload, or a named file via safe_path)
    #   2. VALIDATE them            — before anything is written, so a garbage
    #                                 file cannot trigger a pointless snapshot
    #                                 or a half-applied load
    #   3. SAFETY SNAPSHOT          — the only undo. Without this line a bad
    #                                 restore is unrecoverable
    #   4. LOAD, in one transaction — without the atomic block a failure
    #                                 halfway leaves the database part-merged,
    #                                 which is worse than either end state
    #   5. broadcast, then audit    — after the commit, so nothing is announced
    #                                 that did not actually land
    def post(self, request):
        import json
        from django.db import transaction

        from realtime.broadcast import broadcast_change

        # Two sources, one path afterwards: an uploaded file, or the name of
        # one already on the server (an automatic backup, or a safety snapshot
        # from an earlier attempt).
        upload = request.FILES.get('file')
        filename = (request.data.get('filename') or '').strip()

        if upload:
            source = upload.name             # recorded in the audit line, so the restore says what it came from
            # Checked BEFORE .read(): the size limit is worthless if the file
            # has already been pulled into memory to measure it.
            if upload.size > 50 * 1024 * 1024:
                return Response({'error': 'Backup file is too large (max 50 MB).'},
                                status=status.HTTP_400_BAD_REQUEST)
            raw = upload.read()
        elif filename:
            path = safe_path(filename)       # the same traversal guard the download uses — a caller-supplied name never reaches open() unchecked
            if not path:
                return Response({'error': 'Saved backup not found.'},
                                status=status.HTTP_404_NOT_FOUND)
            source = filename
            with open(path, 'rb') as fh:
                raw = fh.read()
        else:
            return Response({'error': 'No backup file provided.'},
                            status=status.HTTP_400_BAD_REQUEST)

        # Step 2. Cheap structural validation, and it happens BEFORE the
        # snapshot below on purpose: a file that is not a fixture at all should
        # cost nothing and write nothing. Note what this does and does not
        # check — that the bytes are UTF-8 and parse as a JSON *list*. It does
        # not verify the contents are rows this system knows; that is left to
        # the deserializer inside the transaction, where failure rolls back.
        try:
            text = raw.decode('utf-8')
            if not isinstance(json.loads(text), list):
                raise ValueError('not a fixture list')
        except Exception:                    # broad on purpose: decode, parse and type errors all mean the same thing to the caller
            return Response({'error': 'Invalid backup file — expected a JSON data fixture.'},
                            status=status.HTTP_400_BAD_REQUEST)

        # 1) Auto safety snapshot of current data before overwriting anything.
        #
        # THE undo. Deliberately not wrapped in try/except, unlike the
        # convenience copy in SystemBackupView: if this cannot be written the
        # exception propagates and the restore never runs. Failing to start is
        # the correct outcome — proceeding would mean overwriting live data
        # with no way back.
        safety_name, _ = write_backup(SAFETY_PREFIX)

        # 2) Load the fixture atomically (rolls back on any error).
        #
        # The atomic block is what makes the error message below TRUE. Without
        # it, "no changes were applied" would be a lie: load_backup writes per
        # model, so a failure on the fifth table would leave four tables merged
        # and the rest not. load_backup's own docstring states it must be
        # called inside a transaction for exactly this reason — the rollback is
        # the caller's job, and this line is the caller doing it.
        try:
            with transaction.atomic():
                result = load_backup(text)
        except Exception as exc:
            # The safety snapshot's name goes back with the failure, so the
            # admin holding a broken system is told where the undo is in the
            # same response that tells them it went wrong.
            return Response(
                {'error': f'Restore failed and was rolled back. No changes were applied. ({exc})',
                 'safety_backup': safety_name},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # The bulk load writes rows without firing per-row save signals, so no
        # live-update broadcasts went out with them. Send one per model touched
        # — a page subscribes by resource name and drops anything else, so a
        # single catch-all message would reach nobody. Twenty-odd messages is
        # also what the pages want: under `loaddata` they got one per row and
        # spent the minute after a restore refetching thousands of times.
        for resource in result.resources:
            broadcast_change(resource, 'restored')

        # Audited after the commit, so the trail records a restore that
        # actually happened. The record count and the source file are both in
        # the line, because "a restore was performed" on its own would not let
        # anyone reconstruct what the system was made to look like.
        log_action(request, AuditLog.Action.RECORD_UPDATED,
                   details=f'System restore from backup ({result.records} records, source: {source})')
        # The snapshot name is returned on SUCCESS too, not just on failure: a
        # restore that worked mechanically can still be the wrong restore, and
        # this is the handle for undoing it.
        return Response({'restored': result.records, 'safety_backup': safety_name},
                        status=status.HTTP_200_OK)


# Wipes the accountability record itself.
#
# Recorded here, with no code changed, because the contrast with the four
# backup views above is stark and worth a decision rather than an accident:
#
#   * NO step-up. SystemBackupView requires a fresh two-factor to READ the
#     data; this endpoint destroys the log of who did what, with none.
#   * NO log_action call. Every other administrative action in this file
#     writes an AuditLog row — this one does not, so clearing the trail leaves
#     no trace that it was cleared, or by whom.
#   * NO scoping. It is all-or-nothing: there is no date range, so "tidy up
#     last year" is not expressible and the only option is total deletion.
#   * NO safety copy, unlike the restore path, which snapshots first.
#
# Judged against the rest of this file's own standards, this is the single
# most destructive endpoint with the fewest guards on it.
class AuditLogClearView(APIView):
    """Delete all audit log records — admin only."""
    permission_classes = [IsAdminRole]

    def delete(self, request):
        # .all().delete() — every row, unconditionally. The count comes back
        # so the caller can report it; nothing else survives the call.
        deleted_count, _ = AuditLog.objects.all().delete()
        return Response({'deleted': deleted_count}, status=status.HTTP_200_OK)


class AuditLogStatsView(APIView):
    """Get audit log statistics."""
    permission_classes = [IsAdminRole]

    def get(self, request):
        from django.db.models import Count
        from django.utils import timezone
        from datetime import timedelta
        
        today = timezone.localdate()
        week_ago = today - timedelta(days=7)
        
        # Four separate queries, where DashboardStatsView folds this shape of
        # question into one Count(filter=...) aggregate. Left as it is; noted
        # because the two files answer the same kind of question differently.
        stats = {
            'total_logs': AuditLog.objects.count(),
            # Half-open bounds from day_start/day_end rather than a __date
            # lookup, so the created_at index is usable.
            'today_logs': AuditLog.objects.filter(
                created_at__gte=day_start(today), created_at__lt=day_end(today)).count(),
            'week_logs': AuditLog.objects.filter(created_at__gte=day_start(week_ago)).count(),
            # A queryset, not a dict: DRF serialises the rows as they are, so
            # this goes out as a list of {action, count} objects.
            'by_action': AuditLog.objects.values('action').annotate(count=Count('action')),
        }
        return Response(stats)


# ──────────────────────────────────────────────
#  Password Change (any authenticated user)
# ──────────────────────────────────────────────

class ChangePasswordView(APIView):
    """Allow any authenticated user to change their own password.
    Clears the must_change_password flag after a successful change.

    Step-up protected for accounts that carry two-factor: the current password
    alone is not enough, because a session left open on an unlocked machine
    already has it. Guards, who carry no second factor, are unaffected — and so
    is anyone still completing their first-login enrollment, since a confirmed
    device is what arms the check.
    """
    permission_classes = [permissions.IsAuthenticated, HasRecentTwoFactor]

    def post(self, request):
        user = request.user
        current_password = request.data.get('current_password', '').strip()
        new_password = request.data.get('new_password', '').strip()
        confirm_password = request.data.get('confirm_password', '').strip()

        if not current_password:
            return Response({'error': 'Current password is required.'}, status=status.HTTP_400_BAD_REQUEST)
        if not user.check_password(current_password):
            return Response({'error': 'Current password is incorrect.'}, status=status.HTTP_400_BAD_REQUEST)
        if not new_password:
            return Response({'error': 'New password is required.'}, status=status.HTTP_400_BAD_REQUEST)
        if new_password != confirm_password:
            return Response({'error': 'New passwords do not match.'}, status=status.HTTP_400_BAD_REQUEST)
        if current_password == new_password:
            return Response({'error': 'New password must be different from current password.'}, status=status.HTTP_400_BAD_REQUEST)

        # Validate strength
        import re
        errors = []
        if len(new_password) < 8:
            errors.append('Password must be at least 8 characters.')
        if not re.search(r'[A-Z]', new_password):
            errors.append('Password must contain at least one uppercase letter.')
        if not re.search(r'[a-z]', new_password):
            errors.append('Password must contain at least one lowercase letter.')
        if not re.search(r'[0-9]', new_password):
            errors.append('Password must contain at least one number.')
        if not re.search(r'[!@#$%^&*()_+\-=\[\]{};\'\"\\|,.<>\/?]', new_password):
            errors.append('Password must contain at least one special character.')
        if errors:
            return Response({'errors': errors}, status=status.HTTP_400_BAD_REQUEST)

        # Read before the flag is cleared: a user still carrying
        # must_change_password is replacing the temporary password they were
        # issued, which is the moment the account becomes theirs — that gets the
        # welcome. Every later change gets the security notice instead.
        was_first_change = user.must_change_password

        user.set_password(new_password)
        user.must_change_password = False
        user.save(update_fields=['password', 'must_change_password'])

        notify_password_set(user, was_first_change)
        return Response({'message': 'Password changed successfully.'})


# ──────────────────────────────────────────────
#  Vehicle Owner: own registration record
# ──────────────────────────────────────────────

class MyRegistrationView(APIView):
    """Returns the VehicleRegistration record for the logged-in vehicle owner."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if request.user.role != 'vehicle_owner':
            return Response({'error': 'Only vehicle owners can access this endpoint.'}, status=status.HTTP_403_FORBIDDEN)
        from vehicles.models import VehicleRegistration
        from vehicles.serializers import VehicleRegistrationSerializer
        registration = (
            VehicleRegistration.objects
            .filter(Q(user=request.user) | Q(email=request.user.email), status='accepted')
            .order_by('-reviewed_at')
            .first()
        )
        if not registration:
            return Response({'error': 'No accepted registration found for this account.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(VehicleRegistrationSerializer(registration).data)


class MyPlateSwapView(APIView):
    """One-time, self-service replacement of a conduction number with the real
    plate, for owners whose brand-new car has since received its plate. Updates
    both the Vehicle and the accepted registration, and can only happen once —
    afterwards the account has a plate and no conduction number."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        if request.user.role != 'vehicle_owner':
            return Response({'error': 'Only vehicle owners can update their plate.'},
                            status=status.HTTP_403_FORBIDDEN)

        from django.db import transaction
        from vehicles.models import VehicleRegistration, _normalize_plate
        from vehicles.views import _plate_conflict
        from scanning.ml.validator import is_valid_ph_plate

        new_plate = _normalize_plate(request.data.get('plate_number') or '')
        if not new_plate:
            return Response({'plate_number': 'A plate number is required.'}, status=status.HTTP_400_BAD_REQUEST)
        if not is_valid_ph_plate(new_plate):
            return Response({'plate_number': 'Enter a valid Philippine plate number.'}, status=status.HTTP_400_BAD_REQUEST)

        registration = (
            VehicleRegistration.objects
            .filter(Q(user=request.user) | Q(email=request.user.email), status='accepted')
            .order_by('-reviewed_at')
            .first()
        )
        if not registration:
            return Response({'error': 'No accepted registration found for this account.'},
                            status=status.HTTP_404_NOT_FOUND)
        # Only a conduction-only registration is eligible; once a real plate is set
        # the option is spent and must not run again.
        if registration.plate_number or not registration.conduction_number:
            return Response({'error': 'This account already has a plate number on file.'},
                            status=status.HTTP_400_BAD_REQUEST)

        # The new plate must not belong to anyone else (active registration or owned vehicle).
        active = (VehicleRegistration.objects
                  .filter(status__in=['pending', 'accepted']).exclude(pk=registration.pk))
        conflict = _plate_conflict(new_plate, active)
        if conflict:
            return Response({'plate_number': conflict}, status=status.HTTP_400_BAD_REQUEST)

        old_conduction = registration.conduction_number
        with transaction.atomic():
            vehicle = registration.vehicle
            if vehicle is not None:
                vehicle.plate_number = new_plate
                vehicle.conduction_number = ''
                vehicle.save(update_fields=['plate_number', 'conduction_number'])
            registration.plate_number = new_plate
            registration.conduction_number = ''
            registration.save()  # normalizes and persists

        AuditLog.objects.create(
            actor=request.user,
            action=AuditLog.Action.USER_UPDATED,
            target_user=request.user,
            details=(f"Plate number set by owner | Conduction {old_conduction} -> Plate {new_plate} | "
                     f"{request.user.email}"),
        )
        return Response({'plate_number': new_plate,
                         'message': 'Your plate number has been saved and verified.'})


# ──────────────────────────────────────────────
#  Guard QR Login (passwordless, for gate stations)
# ──────────────────────────────────────────────

class GuardQrLoginView(APIView):
    """
    Authenticate a security guard by scanning their QR badge.
    QR format: SLC-GUARD:{user_code}:{guard_qr_secret}
    Logs out any previously active guard session implicitly — the new JWT supersedes the old one.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        import uuid as _uuid
        from rest_framework_simplejwt.tokens import RefreshToken

        qr_data = (request.data.get('qr_data') or '').strip()
        if not qr_data.startswith('SLC-GUARD:'):
            return Response({'detail': 'Invalid QR code.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            _, user_code, secret_str = qr_data.split(':', 2)
            secret = _uuid.UUID(secret_str)
        except (ValueError, AttributeError):
            return Response({'detail': 'Malformed QR code.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = User.objects.get(user_code=user_code, guard_qr_secret=secret, role='security', is_active=True)
        except User.DoesNotExist:
            return Response({'detail': 'QR code not recognised or guard account is disabled.'}, status=status.HTTP_401_UNAUTHORIZED)

        # QR login is passwordless — refuse it until the guard has completed
        # their first credentials login and replaced the temporary password.
        if user.must_change_password:
            return Response(
                {'detail': 'QR login is disabled until you sign in with your credentials and change your temporary password.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        refresh = RefreshToken.for_user(user)
        refresh['role'] = user.role
        refresh['full_name'] = user.full_name
        refresh['email'] = user.email
        refresh['must_change_password'] = user.must_change_password

        AuditLog.objects.create(
            actor=user,
            action=AuditLog.Action.GUARD_LOGIN,
            details=f'Guard QR login: {user.full_name} ({user.user_code})',
            ip_address=get_client_ip(request),
        )

        return Response({
            'access':  str(refresh.access_token),
            'refresh': str(refresh),
            'user': {
                'id':                 user.id,
                'user_code':          user.user_code,
                'full_name':          user.full_name,
                'email':              user.email,
                'role':               user.role,
                'must_change_password': user.must_change_password,
                'photo_url': request.build_absolute_uri(user.photo.url) if user.photo else None,
            },
        })


class GuardQrCodeView(APIView):
    """
    Generate (or retrieve) a guard's QR secret.
    Admin: GET /accounts/guard-qr/{pk}/ — returns the QR payload string for that guard.
    The guard themselves can also hit this endpoint to get their own code.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        import uuid as _uuid

        if request.user.role == 'admin':
            user = get_object_or_404(User, pk=pk, role='security')
        elif request.user.pk == int(pk) and request.user.role == 'security':
            user = request.user
        else:
            return Response({'detail': 'Not authorised.'}, status=status.HTTP_403_FORBIDDEN)

        if user.must_change_password:
            return Response(
                {'detail': 'QR badge is locked — this guard must log in with their credentials and change their temporary password first.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        if not user.guard_qr_secret:
            user.guard_qr_secret = _uuid.uuid4()
            User.objects.filter(pk=user.pk).update(guard_qr_secret=user.guard_qr_secret)

        qr_payload = f'SLC-GUARD:{user.user_code}:{user.guard_qr_secret}'
        return Response({
            'user_code':   user.user_code,
            'full_name':   user.full_name,
            'qr_payload':  qr_payload,
            'photo_url':   request.build_absolute_uri(user.photo.url) if user.photo else None,
        })


def _text(value):
    """A request field as text — '' when it is missing or not a string.

    `request.data.get('email', '').strip()` assumed JSON always carries strings.
    A null, a number or a list reached `.strip()` and came back as a 500 from an
    endpoint anyone on the internet can call.
    """
    return value if isinstance(value, str) else ''


def _count_towards_limit(key, window_seconds):
    """Add one to a rolling counter and return the new total.

    Returns None when the cache cannot answer (the Redis client is configured to
    swallow errors and return None) — a dead cache must not lock people out of
    resetting their password, so callers treat None as "under the limit".
    """
    from django.core.cache import cache
    if cache.add(key, 1, window_seconds):
        return 1
    try:
        return cache.incr(key)
    except ValueError:          # expired between add() and incr()
        cache.set(key, 1, window_seconds)
        return 1


class PasswordResetRequestView(APIView):
    """Step 1: accept an email, generate a token, send a reset link."""
    permission_classes = [permissions.AllowAny]
    # No authentication at all. With the default JWT class, a browser still
    # holding an expired or revoked access token sent it along, DRF rejected the
    # request with a 401 before AllowAny was consulted, and the frontend's
    # refresh-then-logout handling threw the user back to the login page — the
    # people most likely to carry a dead session are exactly the ones who have
    # been away long enough to forget their password.
    authentication_classes = []

    # A reset email costs a send against the provider's daily quota (Brevo's
    # free tier is 300 a day, shared with every registration receipt), and
    # nothing stopped anyone from requesting one for the same address in a loop.
    # The per-address cap is silent, so it cannot be used to learn whether an
    # account exists; the per-client cap answers 429, which says nothing about
    # any address.
    RESET_WINDOW_SECONDS      = 15 * 60
    RESET_EMAILS_PER_ADDRESS  = 3
    RESET_REQUESTS_PER_CLIENT = 10

    def post(self, request):
        import hashlib
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.http import urlsafe_base64_encode
        from django.utils.encoding import force_bytes
        from django.conf import settings as django_settings
        from vehicles.email_utils import send_in_background

        email = _text(request.data.get('email')).strip().lower()
        if not email:
            return Response({'error': 'Email is required.'}, status=status.HTTP_400_BAD_REQUEST)

        client_hits = _count_towards_limit(
            f'pwreset:client:{get_client_ip(request)}', self.RESET_WINDOW_SECONDS)
        if client_hits is not None and client_hits > self.RESET_REQUESTS_PER_CLIENT:
            return Response(
                {'error': 'Too many password reset requests. Please wait a few minutes and try again.'},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        # Always return the same message to avoid leaking which emails exist
        SAFE_MSG = 'If an account with that email exists, a password reset link has been sent.'

        # A list, not .get(): live-account uniqueness is enforced case-sensitively
        # by the database and only case-insensitively by the serializers, so two
        # live rows can differ only in case. .get() raised MultipleObjectsReturned
        # on those — a 500 — and each of them is a real account that may need
        # its own link.
        users = list(User.objects.filter(email__iexact=email, is_active=True, is_archived=False))
        if not users:
            return Response({'message': SAFE_MSG})

        address_key = hashlib.sha256(email.encode()).hexdigest()
        address_hits = _count_towards_limit(f'pwreset:address:{address_key}', self.RESET_WINDOW_SECONDS)
        if address_hits is not None and address_hits > self.RESET_EMAILS_PER_ADDRESS:
            logger.warning('Password-reset email for user(s) %s withheld: more than %d requests '
                           'in %d minutes.', [u.pk for u in users], self.RESET_EMAILS_PER_ADDRESS,
                           self.RESET_WINDOW_SECONDS // 60)
            return Response({'message': SAFE_MSG})

        # PUBLIC_SITE_URL: a reset link built from the campus half's LAN address
        # is unreachable for anyone resetting their password from off campus.
        frontend_url = getattr(django_settings, 'PUBLIC_SITE_URL', '') or 'http://localhost:5173'
        lifetime = _reset_link_lifetime()

        for user in users:
            token = default_token_generator.make_token(user)
            uid   = urlsafe_base64_encode(force_bytes(user.pk))
            reset_link = f"{frontend_url}/reset-password?uid={uid}&token={token}"
            send_in_background(_send_password_reset_email, user, reset_link, lifetime)

        return Response({'message': SAFE_MSG})


def _reset_link_lifetime():
    """How long a reset link lives, in the words the email uses.

    Read from PASSWORD_RESET_TIMEOUT, the setting Django's token generator
    actually enforces. The email used to promise "1 hour" as a literal while the
    setting was left at Django's default of three days.
    """
    from django.conf import settings as django_settings
    seconds = int(getattr(django_settings, 'PASSWORD_RESET_TIMEOUT', 3600))
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return f'{hours} hour' + ('' if hours == 1 else 's')
    minutes = max(1, seconds // 60)
    return f'{minutes} minute' + ('' if minutes == 1 else 's')


def _send_password_reset_email(user, reset_link, lifetime):
    """Send one reset email; log, never raise.

    Runs on a background thread (send_in_background). Sending inline made the
    request for a real account take as long as the mail server did while an
    unknown address answered at once — the response time alone told a caller
    which addresses have accounts, which the neutral message exists to hide.

    fail_silently=False + an explicit log. The response stays SAFE_MSG either
    way, but the send itself must not fail invisibly: with fail_silently=True an
    expired SMTP credential produced a cheerful "a reset link has been sent" for
    every request while nothing was delivered and nothing was written to the log.
    """
    import html
    from django.core.mail import send_mail
    from django.conf import settings as django_settings

    # Escaped: the name is user-supplied and lands inside the HTML body.
    name = html.escape(user.full_name or user.email)
    html_message = f"""
        <html>
          <body style="font-family:Arial,sans-serif;color:#1A1D2E;background:#F0F2F7;padding:20px;margin:0;">
            <div style="max-width:540px;margin:0 auto;background:#fff;border-radius:12px;border-top:4px solid #2A2B61;box-shadow:0 4px 20px rgba(0,0,0,.08);overflow:hidden;">
              <div style="padding:28px 32px 24px;">
                <h2 style="color:#2A2B61;margin:0 0 8px;">Password Reset Request</h2>
                <p style="color:#5A5F72;font-size:14px;margin:0 0 20px;">
                  We received a request to reset the password for your SLC Smart Parking and Vehicle Verification System account.
                </p>
                <p style="margin:0 0 8px;">Hello, <strong>{name}</strong>,</p>
                <p style="color:#5A5F72;font-size:14px;margin:0 0 24px;">
                  Click the button below to set a new password. This link expires in <strong>{lifetime}</strong>.
                </p>
                <div style="text-align:center;margin:0 0 24px;">
                  <a href="{reset_link}"
                     style="display:inline-block;padding:13px 32px;background:#2A2B61;color:#fff;
                            border-radius:10px;font-size:15px;font-weight:600;text-decoration:none;">
                    Reset My Password
                  </a>
                </div>
                <p style="color:#9CA3B0;font-size:12px;margin:0 0 8px;">
                  If the button doesn't work, copy and paste this link into your browser:
                </p>
                <p style="word-break:break-all;font-size:12px;color:#2A2B61;margin:0 0 24px;">{reset_link}</p>
                <p style="color:#9CA3B0;font-size:12px;margin:0;">
                  If you did not request a password reset, you can safely ignore this email.
                  Your password will not change.
                </p>
              </div>
              <div style="background:#F8FAFC;border-top:1px solid #E2E6EE;padding:14px 32px;text-align:center;">
                <p style="font-size:12px;color:#7C80A3;margin:0;">Saint Louis College Smart Parking and Vehicle Verification System</p>
                <p style="font-size:11px;color:#B0B4C7;margin:4px 0 0;">This is an automated message. Please do not reply.</p>
              </div>
            </div>
          </body>
        </html>
        """
    try:
        send_mail(
            subject='SPVVS — Password Reset',
            message=(
                f"Hello {user.full_name or user.email},\n\n"
                f"Reset your password by visiting:\n{reset_link}\n\n"
                f"This link expires in {lifetime}.\n\n"
                f"If you did not request this, ignore this email."
            ),
            from_email=django_settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            html_message=html_message,
            fail_silently=False,
        )
    except Exception:
        logger.exception(
            "Failed to send the password-reset email to user %s — they were "
            "told a link was sent, but none was delivered.", user.pk,
        )


class PasswordResetConfirmView(APIView):
    """Step 2: validate the token and set the new password."""
    permission_classes = [permissions.AllowAny]
    # See PasswordResetRequestView: a stale token in the browser must not turn
    # the reset into a 401 and a bounce to the login page.
    authentication_classes = []

    def post(self, request):
        import re
        from django.contrib.auth.tokens import default_token_generator
        from django.db import transaction
        from django.utils.http import urlsafe_base64_decode
        from django.utils.encoding import force_str

        uid              = _text(request.data.get('uid')).strip()
        token            = _text(request.data.get('token')).strip()
        # Stripped to match login, which trims the password it is given — a
        # password saved with its surrounding spaces could never be typed back.
        new_password     = _text(request.data.get('new_password')).strip()
        confirm_password = _text(request.data.get('confirm_password')).strip()

        if not all([uid, token, new_password, confirm_password]):
            return Response({'error': 'All fields are required.'}, status=status.HTTP_400_BAD_REQUEST)

        if new_password != confirm_password:
            return Response({'error': 'Passwords do not match.'}, status=status.HTTP_400_BAD_REQUEST)

        # Decode UID and fetch user. Only an account that may still sign in: a
        # link emailed before the account was disabled or archived otherwise went
        # on setting a password on it for as long as the token lived.
        try:
            pk   = force_str(urlsafe_base64_decode(uid))
            user = User.objects.get(pk=pk, is_active=True, is_archived=False)
        except (User.DoesNotExist, ValueError, TypeError, Exception):
            return Response({'error': 'Invalid reset link.'}, status=status.HTTP_400_BAD_REQUEST)

        if not default_token_generator.check_token(user, token):
            return Response(
                {'error': 'This reset link has expired or is invalid. Please request a new one.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if user.check_password(new_password):
            return Response(
                {'error': 'New password must be different from your current password.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate password strength (same rules as ChangePasswordView)
        errors = []
        if len(new_password) < 8:
            errors.append('Password must be at least 8 characters.')
        if not re.search(r'[A-Z]', new_password):
            errors.append('Password must contain at least one uppercase letter.')
        if not re.search(r'[a-z]', new_password):
            errors.append('Password must contain at least one lowercase letter.')
        if not re.search(r'[0-9]', new_password):
            errors.append('Password must contain at least one number.')
        if not re.search(r'[!@#$%^&*()_+\-=\[\]{};\'\"\\|,.<>\/?]', new_password):
            errors.append('Password must contain at least one special character.')
        if errors:
            return Response({'errors': errors}, status=status.HTTP_400_BAD_REQUEST)

        # Same split as ChangePasswordView. A brand-new user who never logged in
        # with their temporary password and used "forgot password" instead still
        # arrives here for their first change, so they get the welcome too.
        was_first_change = user.must_change_password

        with transaction.atomic():
            user.set_password(new_password)
            user.must_change_password = False
            # Demand the second factor on the next login. A reset proves control of
            # the mailbox, nothing more — and the mailbox is exactly what an attacker
            # takes first. Guards are skipped because they carry no second factor to
            # ask for; the flag would sit unread and never be cleared.
            from . import twofa
            user.must_verify_2fa = twofa.requires_2fa(user)
            user.save(update_fields=['password', 'must_change_password', 'must_verify_2fa'])
            _end_sessions(user)

        notify_password_set(user, was_first_change)

        return Response({
            'message': 'Password reset successfully. You can now log in with your new password.',
            'role': user.role,
            'twofa_required_next_login': user.must_verify_2fa,
        })


def _end_sessions(user):
    """Revoke every refresh token issued to `user` before this moment.

    A password reset is what someone does when they think the account is no
    longer only theirs. Changing the password stopped new logins but left every
    existing session running: a stolen refresh token kept renewing for up to
    REFRESH_TOKEN_LIFETIME afterwards. Access tokens already handed out still
    live out their own short lifetime — JWTs cannot be recalled — but none of
    them can be renewed.
    """
    from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
    live = OutstandingToken.objects.filter(
        user=user, expires_at__gt=timezone.now(), blacklistedtoken__isnull=True,
    )
    BlacklistedToken.objects.bulk_create(
        [BlacklistedToken(token=t) for t in live], ignore_conflicts=True,
    )


# ──────────────────────────────────────────────
#  Guard QR Login & Shift Management
# ──────────────────────────────────────────────

class QRLoginView(APIView):
    """Guard scans their QR badge — clocks out previous shift, creates new shift, issues JWT."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        from scanning.models import GuardShift
        from scanning.serializers import GuardShiftSerializer
        from rest_framework_simplejwt.tokens import RefreshToken

        token_str  = (request.data.get('qr_token') or '').strip()
        gate_param = (request.data.get('gate')     or '').strip()

        if not token_str:
            return Response({'error': 'qr_token is required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            guard = User.objects.get(qr_token=token_str, role='security', is_active=True)
        except (User.DoesNotExist, ValueError):
            return Response({'error': 'Invalid or unrecognized QR code.'}, status=status.HTTP_401_UNAUTHORIZED)

        # QR login is passwordless — refuse it until the guard has completed
        # their first credentials login and replaced the temporary password.
        if guard.must_change_password:
            return Response(
                {'error': 'QR login is disabled until you sign in with your credentials and change your temporary password.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        from scanning.models import Gate, open_shift_for
        valid_gates = Gate.active_ids()
        gate = gate_param if gate_param in valid_gates else guard.gate_assignment
        if not gate or gate not in valid_gates:
            return Response(
                {'error': 'Gate selection required. Please choose a gate before scanning.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Persist the shift gate on the guard's profile. Server-side scan
        # attribution (manual entry, override, exit, HTTP/WS scans) reads
        # request.user.gate_assignment to tag each log; without this it stays
        # None and those scans fall to the orphan 'main' gate, invisible in
        # every gate's Audit Log.
        if guard.gate_assignment != gate:
            User.objects.filter(pk=guard.pk).update(gate_assignment=gate)
            guard.gate_assignment = gate

        # Closes both the guard being relieved here AND this guard's own stale
        # session at another gate — see scanning.models.open_shift_for.
        shift, displaced = open_shift_for(guard, gate)
        refresh = RefreshToken.for_user(guard)

        return Response({
            'access':  str(refresh.access_token),
            'refresh': str(refresh),
            'user': {
                'id':                   guard.id,
                'user_code':            guard.user_code,
                'full_name':            guard.full_name,
                'email':                guard.email,
                'role':                 guard.role,
                'gate_assignment':      gate,   # current shift gate (also persisted above)
                'must_change_password': guard.must_change_password,
            },
            'shift': GuardShiftSerializer(shift).data,
            # Gates this sign-in signed the same guard out of. Empty in the
            # ordinary case; non-empty means a session they had left open
            # elsewhere has just been closed, and the terminal says so.
            'signed_out_of': displaced,
        })


class GuardCredentialLoginView(APIView):
    """Guard logs in with email + password at the gate station — clocks out the
    previous shift at the selected gate, creates a new shift, issues JWT.
    Only accounts with role='security' may use this endpoint; everyone else
    logs in through the regular /api/auth/login/ endpoint."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        from scanning.models import GuardShift
        from scanning.serializers import GuardShiftSerializer
        from rest_framework_simplejwt.tokens import RefreshToken

        email      = (request.data.get('email')    or '').strip().lower()
        password   = request.data.get('password')  or ''
        gate_param = (request.data.get('gate')     or '').strip()

        if not email or not password:
            return Response({'error': 'Email and password are required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            guard = User.objects.get(email__iexact=email, role='security', is_archived=False)
        except User.DoesNotExist:
            return Response({'error': 'Incorrect email or password.'}, status=status.HTTP_401_UNAUTHORIZED)

        if not guard.check_password(password):
            return Response({'error': 'Incorrect email or password.'}, status=status.HTTP_401_UNAUTHORIZED)

        if not guard.is_active:
            return Response(
                {'error': 'Your account has been disabled. Please contact the administrator.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        from scanning.models import Gate, open_shift_for
        valid_gates = Gate.active_ids()
        gate = gate_param if gate_param in valid_gates else guard.gate_assignment
        if not gate or gate not in valid_gates:
            return Response(
                {'error': 'Gate selection required. Please choose a gate before logging in.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Persist the shift gate on the guard's profile. Server-side scan
        # attribution (manual entry, override, exit, HTTP/WS scans) reads
        # request.user.gate_assignment to tag each log; without this it stays
        # None and those scans fall to the orphan 'main' gate, invisible in
        # every gate's Audit Log.
        if guard.gate_assignment != gate:
            User.objects.filter(pk=guard.pk).update(gate_assignment=gate)
            guard.gate_assignment = gate

        # Closes both the guard being relieved here AND this guard's own stale
        # session at another gate — see scanning.models.open_shift_for.
        shift, displaced = open_shift_for(guard, gate)
        refresh = RefreshToken.for_user(guard)

        AuditLog.objects.create(
            actor=guard,
            action=AuditLog.Action.GUARD_LOGIN,
            details=f'Guard credential login: {guard.full_name} ({guard.user_code}) at {gate}',
            ip_address=get_client_ip(request),
        )

        return Response({
            'access':  str(refresh.access_token),
            'refresh': str(refresh),
            'user': {
                'id':                   guard.id,
                'user_code':            guard.user_code,
                'full_name':            guard.full_name,
                'email':                guard.email,
                'role':                 guard.role,
                'gate_assignment':      gate,   # current shift gate (also persisted above)
                'must_change_password': guard.must_change_password,
            },
            'shift': GuardShiftSerializer(shift).data,
            # Gates this sign-in signed the same guard out of. Empty in the
            # ordinary case; non-empty means a session they had left open
            # elsewhere has just been closed, and the terminal says so.
            'signed_out_of': displaced,
        })


class GuardQrAvailabilityView(APIView):
    """Public: whether a guard can log in by QR badge (i.e. has completed
    their first credentials login and password change). With ?email= the
    check is for that specific guard; without it, whether any guard can.
    The gate login page shows the QR Badge tab only when this is true.
    Returns only a boolean — no user data is exposed."""
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        email = (request.query_params.get('email') or '').strip()
        qs = User.objects.filter(role='security', is_active=True, must_change_password=False)
        if email:
            qs = qs.filter(email__iexact=email)
        return Response({'qr_available': qs.exists()})


class GuardQRView(APIView):
    """Admin only: return a guard's QR token for badge printing."""
    permission_classes = [IsAdminRole]

    def get(self, request, pk):
        guard = get_object_or_404(User, pk=pk, role='security')
        if guard.must_change_password:
            return Response(
                {'detail': 'QR badge is locked — this guard must log in with their credentials and change their temporary password first.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response({
            'id':       guard.id,
            'full_name': guard.full_name,
            'qr_token': str(guard.qr_token),
        })


class NotificationListView(APIView):
    """Admin/CDSO: notification-bell feed with unread count."""
    permission_classes = [IsAdminOrCdso]

    def get(self, request):
        qs = Notification.objects.all()
        if request.query_params.get('unread_only') in ('1', 'true'):
            qs = qs.filter(is_read=False)
        try:
            limit = min(int(request.query_params.get('limit', 30)), 100)
        except (TypeError, ValueError):
            limit = 30
        unread_count = Notification.objects.filter(is_read=False).count()
        return Response({
            'results':      NotificationSerializer(qs[:limit], many=True).data,
            'unread_count': unread_count,
        })


class NotificationMarkReadView(APIView):
    """Admin/CDSO: mark notifications read — {"ids": [...]} or {"all": true}."""
    permission_classes = [IsAdminOrCdso]

    def post(self, request):
        if request.data.get('all'):
            qs = Notification.objects.filter(is_read=False)
        else:
            ids = request.data.get('ids') or []
            if not isinstance(ids, list) or not ids:
                return Response({'error': 'Provide "ids" (list) or "all": true.'}, status=400)
            qs = Notification.objects.filter(pk__in=ids, is_read=False)
        updated = qs.update(is_read=True)
        # queryset.update() skips post_save, so tell open pages explicitly
        if updated:
            try:
                from realtime.broadcast import broadcast_change
                broadcast_change('notification', 'updated')
            except Exception:
                pass
        return Response({'updated': updated})


class NotificationClearView(APIView):
    """Admin/CDSO: clear (delete) notifications from the bell feed.
    Body: {"read_only": true} deletes only already-read items; otherwise all."""
    permission_classes = [IsAdminOrCdso]

    def post(self, request):
        qs = Notification.objects.all()
        if request.data.get('read_only'):
            qs = qs.filter(is_read=True)
        deleted, _ = qs.delete()
        if deleted:
            try:
                from realtime.broadcast import broadcast_change
                broadcast_change('notification', 'updated')
            except Exception:
                pass
        return Response({'deleted': deleted})




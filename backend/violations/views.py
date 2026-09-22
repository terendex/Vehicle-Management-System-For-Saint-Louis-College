# =============================================================================
#  Violations - the HTTP surface
#
#  What a violation IS, and what it costs, lives in models.py and penalty.py.
#  This file is only the ways in and out of that: issuing one at the gate,
#  listing them for a guard or an owner, exporting them, and the confiscation
#  machinery that a third offence triggers.
#
#  Three audiences read from here and they see different things, which is the
#  single rule that shapes most of the file:
#
#    staff (admin/CDSO, security)  every violation on record
#    a vehicle owner               only violations against their own plates
#    nobody else                   nothing
#
#  The owner case is the awkward one throughout. A violation outlives the
#  vehicle it was issued against - the FK is SET_NULL so the record survives
#  archiving and account deletion - so "is this mine?" cannot be answered from
#  the foreign key alone, and every owner-facing query here also matches on the
#  plate string that was snapshotted onto the row.
# =============================================================================
import logging
from decimal import Decimal
from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status as http_status
from django.shortcuts import get_object_or_404
from django.db.models import Q
from django.utils import timezone
from .models import Violation, NEW_STYLE_TYPES
from .penalty import apply_penalty, notify_owner, recompute_for_owner
from .serializers import ViolationSerializer
from vehicles.models import Vehicle, VehicleRegistration
from accounts.audit import audit
from accounts.models import AuditLog
from time_utils import day_range, filter_local_date_range

logger = logging.getLogger(__name__)


# The two permission classes this module uses. They are separate because
# issuing and reviewing are not the same authority: a guard at the gate writes
# violations all day and must never be able to lift one.
# The wide one: anybody working the system rather than owning a vehicle. Used
# for issuing, listing and exporting - the day-to-day of the module.
class IsStaffRole(permissions.BasePermission):
    """Allow access only to the CDSO (admin) or security roles."""
    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role in ('admin', 'security')
        )


# CDSO only - which is the same thing as admin, and why this tests one role.
# The name reads as two roles and the check names one. That is not a bug: the
# separate `cdso` role was removed and the admin role relabelled to CDSO
# (accounts migration 0024), so 'admin' IS the CDSO. The name survives because
# it reads correctly at the endpoints that are about CDSO work - lifting a
# confiscation, deciding whether a plate may register again - which are exactly
# the decisions a guard must not be able to make.
class IsCDSOOrAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role == 'admin'
        )


# Issuing, listing and settling violations.
#
# Two kinds of violation live behind this one set of endpoints, and telling
# them apart is what most of perform_create is doing.
#
# NEW_STYLE_TYPES are the offence ladder: a first, second and third strike
# against the OWNER rather than against the vehicle, each one counted by
# compute_offense_number and each carrying a penalty the third of which holds
# their registration. Those rows care about who owns the plate.
#
# Everything else is the older per-incident kind, where the row carries a fine
# amount and nothing accumulates. They are kept because the records already
# exist and still have to be listed, corrected and settled; nothing issues a
# new one deliberately.
#
# Who may do what is split three ways and is not the same as who may read:
# only a guard ISSUES (perform_create refuses anyone else), only the CDSO
# SETTLES (the actions below carry IsCDSOOrAdmin), and both can list.
class ViolationViewSet(viewsets.ModelViewSet):
    # issued_by / on_duty_guard are read by the serializer too — without them
    # here each row costs an extra user lookup.
    queryset           = Violation.objects.select_related(
        'vehicle__user', 'issued_by', 'on_duty_guard',
    ).all()
    serializer_class   = ViolationSerializer
    permission_classes = [IsStaffRole]

    def get_serializer_context(self):
        return {**super().get_serializer_context(), 'request': self.request}

    # Issue a violation. Guard only, and the plate has to resolve first.
    #
    # The order here is the point: refuse the wrong role, then find the
    # vehicle, THEN write. A violation that named no vehicle would be a record
    # nobody could act on and nobody could appeal.
    def perform_create(self, serializer):
        # Issuing a violation is the guard's job — the admin (CDSO) handles events,
        # parking-box placement, and clearing/lifting violations, but does not
        # issue them. (CDSO management actions live on separate endpoints.)
        #
        # Checked here rather than with a permission class because the class
        # guarding this viewset (IsStaffRole) is deliberately wider: the CDSO
        # must still be able to list and settle through the same endpoints.
        if self.request.user.role != 'security':
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('Only security personnel can issue violations.')

        # Normalised the same way the gate normalises a scanned plate, because
        # this is the same plate arriving by a different route - a guard typing
        # what they are looking at. " abc 123 " and "ABC123" are one vehicle.
        plate = self.request.data.get('plate_number', '').strip().upper().replace(' ', '')
        vehicle = serializer.validated_data.get('vehicle')
        if vehicle is None and plate:
            # A guard may type a plate or a conduction number (brand-new car).
            vehicle = Vehicle.resolve(plate)
            if vehicle is None:
                from rest_framework.exceptions import ValidationError
                raise ValidationError({'vehicle': 'No vehicle found for that plate or conduction number.'})
        if vehicle is None:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'vehicle': 'Provide a vehicle id or plate_number.'})

        vtype = serializer.validated_data.get('violation_type', '')

        if vtype in NEW_STYLE_TYPES:
            # The ladder. Counted against the OWNER, not the vehicle: somebody
            # with two cars does not get two first offences, and the count has
            # to survive them changing vehicle.
            owner       = vehicle.user
            offense_num = Violation.compute_offense_number(owner)

            instance = serializer.save(
                vehicle              = vehicle,
                owner                = owner,
                offense_number       = offense_num,
                status               = Violation.Status.WARNING,
                # Only the 3rd strike holds registration.
                registration_blocked = offense_num >= 3,
                # Always visible immediately. The release/unrelease pair
                # further down predates that decision and is what the owner
                # portal used to be gated on; nothing issued now is ever
                # hidden, which is why those two actions are marked legacy.
                is_released          = True,   # always visible to owner immediately
                issued_by            = self.request.user,
            )
            audit(self.request, AuditLog.Action.RECORD_CREATED,
                  f"Violation issued | Plate: {vehicle.plate_number} | "
                  f"Type: {instance.get_violation_type_display()} | Offense: {offense_num} | "
                  f"Status: {instance.get_status_display()} | By: {self.request.user.full_name}")
            self._notify_new_offense(instance)
        else:
            # Legacy violation types — fine from the caller, or computed.
            # Every violation notifies the owner immediately, so these are
            # released (visible) and emailed at creation like new-style ones.
            save_kwargs = {'vehicle': vehicle, 'is_released': True,
                           'issued_by': self.request.user}
            if not self.request.data.get('fine_amount'):
                save_kwargs['fine_amount'] = Violation.compute_fine(vehicle)
            instance = serializer.save(**save_kwargs)
            audit(self.request, AuditLog.Action.RECORD_CREATED,
                  f"Violation issued | Plate: {vehicle.plate_number} | "
                  f"Type: {instance.get_violation_type_display()} | "
                  f"Fine: {instance.fine_amount} | By: {self.request.user.full_name}")
            try:
                from .email_utils import send_violation_notified_email
                send_violation_notified_email(instance)
            except Exception:
                pass

    # The exception is logged rather than swallowed, unlike the mail-only helper
    # below: apply_penalty is what confiscates a pass and holds a registration,
    # so a failure here leaves the ladder out of step with the row and somebody
    # has to be able to find out why.
    def _notify_new_offense(self, instance):
        """Impose the penalty, then tell the owner. Both are best-effort — the
        violation is already recorded and must not be rolled back because a mail
        server is down."""
        try:
            penalty = apply_penalty(instance)
            notify_owner(instance, penalty)
        except Exception:
            logger.exception('Could not apply penalty for violation %s', instance.pk)

    # Tell the owner their violation is settled. Mail only, so failure is
    # swallowed: the row is already correct, and a dead SMTP host must not turn
    # a successful settlement into an error the CDSO has to retry.
    def _notify_resolved(self, instance):
        try:
            from .email_utils import send_violation_resolved_email
            send_violation_resolved_email(instance)
        except Exception:
            pass

    # Both write paths read is_resolved BEFORE the write and compare after,
    # rather than trusting the incoming payload. A PATCH that sets
    # is_resolved=true on an already-resolved row must not email the owner a
    # second time, and only the before/after pair can tell those apart.
    #
    # Noted, with no code changed: partial_update writes an audit line for the
    # transition and update does not, so a full PUT that settles a violation
    # leaves no trail. The UI only ever sends PATCH, which is why this has not
    # surfaced.
    def update(self, request, *args, **kwargs):
        was_resolved = self.get_object().is_resolved
        response = super().update(request, *args, **kwargs)
        if not was_resolved and response.data.get('is_resolved'):
            instance = self.get_object()
            self._notify_resolved(instance)
        return response

    def partial_update(self, request, *args, **kwargs):
        was_resolved = self.get_object().is_resolved
        response = super().partial_update(request, *args, **kwargs)
        if not was_resolved and response.data.get('is_resolved'):
            instance = self.get_object()
            audit(request, AuditLog.Action.RECORD_UPDATED,
                  f"Violation resolved | Plate: {instance.identifier} | "
                  f"Type: {instance.get_violation_type_display()} | By: {request.user.full_name}")
            self._notify_resolved(instance)
        return response

    # ── Legacy release/unrelease actions ──────────────────────────────────────
    #
    # From when a violation was written first and shown to the owner later, so
    # the CDSO could review it before the owner saw it. Everything issued now
    # is released at creation (see perform_create), so these act on history
    # rather than on anything new.

    @action(detail=True, methods=['post'], url_path='release')
    def release(self, request, pk=None):
        """CDSO/admin releases a violation so the owner can see it, and sends them an email."""
        violation = self.get_object()
        was_released = violation.is_released
        violation.is_released = True
        violation.save(update_fields=['is_released'])
        if not was_released:
            try:
                from .email_utils import send_violation_notified_email
                send_violation_notified_email(violation)
            except Exception:
                pass
        return Response(ViolationSerializer(violation, context={'request': request}).data)

    @action(detail=True, methods=['post'], url_path='unrelease')
    def unrelease(self, request, pk=None):
        """Undo a release (revert to hidden)."""
        violation = self.get_object()
        violation.is_released = False
        violation.save(update_fields=['is_released'])
        return Response(ViolationSerializer(violation, context={'request': request}).data)

    # ── New CDSO workflow actions ──────────────────────────────────────────────
    #
    # The settlement side, and every one of them carries IsCDSOOrAdmin
    # explicitly. The viewset's own permission class is the wider IsStaffRole,
    # so without that line on each action a guard could clear the violation
    # they had just issued.

    @action(detail=True, methods=['post'], url_path='issue-report',
            permission_classes=[IsCDSOOrAdmin])
    def issue_cdso_report(self, request, pk=None):
        """CDSO marks that they've issued the official violation report to the owner."""
        violation = self.get_object()
        if violation.status in Violation.INACTIVE_STATUSES:
            return Response(
                {'detail': 'This violation is already settled or lifted.'},
                status=http_status.HTTP_400_BAD_REQUEST,
            )
        violation.cdso_report_issued = True
        violation.save(update_fields=['cdso_report_issued'])
        audit(request, AuditLog.Action.RECORD_UPDATED,
              f"CDSO violation report issued | Plate: {violation.identifier} | "
              f"Type: {violation.get_violation_type_display()} | By: {request.user.full_name}")
        return Response(ViolationSerializer(violation, context={'request': request}).data)

    @action(detail=True, methods=['post'], url_path='clear',
            permission_classes=[IsCDSOOrAdmin])
    def clear_violation(self, request, pk=None):
        """
        CDSO settles an offence — the owner has reported to the office and the
        matter is closed.

        There is no fee to collect any more, so this no longer demands an
        Official Receipt; a free-text `note` is accepted instead. Clearing takes
        the offence out of the ladder, which pulls the account's confiscation
        back down a rung (or lifts it entirely if nothing is left).
        """
        violation = self.get_object()
        if violation.status in Violation.INACTIVE_STATUSES:
            return Response(
                {'detail': 'This violation is already settled or lifted.'},
                status=http_status.HTTP_400_BAD_REQUEST,
            )
        note = (request.data.get('note') or request.data.get('official_receipt') or '').strip()

        owner = violation.owner or (violation.vehicle.user if violation.vehicle_id else None)
        violation.official_receipt = note
        violation.status           = Violation.Status.CLEARED
        violation.is_resolved      = True
        violation.save(update_fields=['official_receipt', 'status', 'is_resolved'])

        # The ladder is shorter now — re-derive the penalty from what is left.
        recompute_for_owner(owner)

        audit(request, AuditLog.Action.RECORD_UPDATED,
              f"Violation cleared | Plate: {violation.identifier} | "
              f"Type: {violation.get_violation_type_display()}"
              + (f" | Note: {note}" if note else "")
              + f" | By: {request.user.full_name}")
        self._notify_resolved(violation)
        return Response(ViolationSerializer(violation, context={'request': request}).data)


    @action(detail=True, methods=['post'], url_path='lift',
            permission_classes=[IsCDSOOrAdmin])
    def lift_violation(self, request, pk=None):
        """Void a violation as a false alarm and renumber what is left.

        Distinct from `clear`: clearing means the offence happened and the fee
        was settled against an Official Receipt. Lifting means it should never
        have been issued — an auto-logged camera artefact, a misread plate —
        so it stops counting and the owner's remaining violations of that type
        step back down (two warnings, lift one, the survivor becomes warning 1).

        A reason is required: this erases an offence from someone's record and
        the decision has to be answerable.
        """
        violation = self.get_object()

        if violation.status == Violation.Status.LIFTED:
            return Response({'detail': 'This violation has already been lifted.'},
                            status=http_status.HTTP_400_BAD_REQUEST)
        if violation.status == Violation.Status.CLEARED:
            return Response(
                {'detail': 'This violation was already settled with an Official Receipt. '
                           'Lifting it would imply a refund that this action cannot make.'},
                status=http_status.HTTP_400_BAD_REQUEST)

        reason = (request.data.get('reason') or '').strip()
        if not reason:
            return Response({'detail': 'A reason is required to lift a violation.'},
                            status=http_status.HTTP_400_BAD_REQUEST)

        vehicle, vtype = violation.vehicle, violation.violation_type
        prev_offense   = violation.offense_number

        violation.status               = Violation.Status.LIFTED
        violation.is_resolved          = True
        violation.lifted_reason        = reason
        violation.lifted_at            = timezone.now()
        violation.lifted_by            = request.user
        violation.registration_blocked = False
        violation.save(update_fields=[
            'status', 'is_resolved', 'lifted_reason', 'lifted_at', 'lifted_by',
            'registration_blocked',
        ])

        # A lifted offence never happened, so the account's penalty is
        # re-derived from what remains — dropping from 3 strikes to 2 shortens
        # the confiscation, and dropping to 0 lifts it.
        owner = violation.owner or (vehicle.user if vehicle else None)
        recompute_for_owner(owner)
        resequenced = Violation.active_for_owner(owner).count() if owner else 0

        audit(request, AuditLog.Action.RECORD_UPDATED,
              f"Violation lifted (false alarm) | Plate: {violation.identifier} | "
              f"Type: {violation.get_violation_type_display()} | "
              f"Was offense {prev_offense} | Reason: {reason} | "
              f"{resequenced} remaining renumbered | By: {request.user.full_name}")

        # The owner's list and the admin table both read from this.
        try:
            from realtime.broadcast import broadcast_change
            broadcast_change('violation', 'updated')
        except Exception:
            pass

        return Response(ViolationSerializer(violation, context={'request': request}).data)


# What one guard wrote, for their own shift review - "what did I issue today".
# Scoped by issued_by rather than by role, so it answers the same question for
# whoever asks it; a CDSO calling this sees the violations they personally
# issued, which is normally none.
class GuardViolationsView(APIView):
    """Returns violations issued by the currently authenticated security guard."""
    permission_classes = [IsStaffRole]

    def get(self, request):
        from django.utils import timezone
        date_str = request.query_params.get('date', '')
        qs = Violation.objects.filter(issued_by=request.user).select_related('vehicle__user').order_by('-issued_at')
        if date_str:
            try:
                from datetime import date as _date
                d = _date.fromisoformat(date_str)
                _start, _end = day_range(d)
                # day_range, not a bare __date lookup: the column is UTC and
                # the guard means their own local day, so the boundaries are
                # computed in campus time and compared as a half-open range.
                qs = qs.filter(issued_at__gte=_start, issued_at__lt=_end)
            except ValueError:
                # An unparseable ?date= falls through to the unfiltered list
                # rather than erroring. The parameter is a convenience on a
                # read-only view, and a shift review that returns everything is
                # more use than one that returns a 400.
                pass
        return Response(ViolationSerializer(qs, many=True, context={'request': request}).data)


# The owner's own list, and the awkward case the module header warns about.
# "Mine" cannot be answered from the foreign key alone, so this asks it twice -
# once by FK and once by plate - and unions the two.
class MyViolationsView(APIView):
    """Returns violations visible to the authenticated vehicle owner."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        # Every vehicle linked to this owner by FK, plus plate-matched vehicles
        # from any accepted registration (covers legacy rows never linked by FK).
        plates = VehicleRegistration.objects.filter(
            user=request.user,
            status=VehicleRegistration.Status.ACCEPTED,
        ).values_list('plate_number', flat=True)
        vehicles = Vehicle.objects.filter(
            Q(user=request.user) | Q(plate_number__in=list(plates))
        )

        # New-style: always visible (is_released=True on create)
        # Legacy: show released + all resolved/cleared (history stays visible)
        #
        # The OR is what keeps an owner's history honest. A legacy row that was
        # never released would otherwise vanish the moment it was settled,
        # leaving somebody who HAS been penalised with a clean-looking page and
        # no way to see what they were told about.
        violations = Violation.objects.filter(
            vehicle__in=vehicles,
        ).filter(
            Q(is_released=True) | Q(is_resolved=True)
        ).select_related('vehicle__user', 'issued_by').order_by('-issued_at')
        return Response(ViolationSerializer(violations, many=True, context={'request': request}).data)


# ── Violations Report (CDSO/admin — branded PDF & Excel) ─────────────────────
#
# The three helpers below exist so the PDF and the Excel cannot disagree. Both
# report views call the same filter, the same row builder and the same subtitle,
# so two files downloaded from one screen describe the same set of rows and
# state the same thing about what was excluded. The same arrangement is used by
# the registration and audit reports.
VIOLATION_REPORT_HEADERS = ['#', 'Date & Time', 'Plate', 'Owner', 'Violation', 'Fee (PHP)', 'Status', 'Issued By']


# A violation stops counting in three different ways, because three endpoints
# end one: clearing sets CLEARED, lifting sets LIFTED, and the plain resolve
# PATCH only flips is_resolved and leaves the status at 'warning'. Anything that
# asks "is this still standing?" has to test all three or it counts a resolved
# warning as an active one.
_SETTLED_Q = Q(is_resolved=True) | Q(status__in=(Violation.Status.CLEARED,
                                                 Violation.Status.LIFTED))

# The management screen's status buttons are buckets, not raw model statuses:
# "Cleared / Resolved" spans the three endings above, and "Confiscated (3rd)" is
# a rung of the offence ladder rather than a status at all. FEE_IMPOSED is only
# in that test so a legacy row that escaped migration 0016 still lands
# somewhere; nothing has set it since the fine system was removed.
_STATUS_GROUPS = {
    'warning':     (~_SETTLED_Q & Q(status=Violation.Status.WARNING),
                    'Active warnings'),
    'confiscated': (~_SETTLED_Q & (Q(offense_number=3)
                                   | Q(status=Violation.Status.FEE_IMPOSED)),
                    'Confiscated (3rd offence)'),
    'resolved':    (_SETTLED_Q, 'Cleared / resolved'),
}


def _filter_violations_report(request):
    """Filter the violations for a report — same knobs as the management page.

    Every filter on that screen has to be readable here, or a narrowed table
    still exports the whole list and the file reads as wrong rather than as
    unfiltered.
    """
    qs = Violation.objects.select_related('vehicle', 'vehicle__user', 'issued_by').all()
    date_from = request.query_params.get('date_from', '').strip()
    date_to   = request.query_params.get('date_to', '').strip()
    status_f  = request.query_params.get('status', '').strip()
    type_f    = request.query_params.get('violation_type', '').strip()
    search    = request.query_params.get('search', '').strip()

    status_labels = dict(Violation.Status.choices)
    type_labels   = dict(Violation.Type.choices)
    desc = []

    qs = filter_local_date_range(qs, 'issued_at', date_from, date_to)
    if date_from or date_to:
        desc.append(f"Period: {date_from or 'start'} to {date_to or 'today'}")

    if status_f:
        group = _STATUS_GROUPS.get(status_f)
        if group is not None:
            condition, label = group
        else:
            # A raw model status still works, so an older saved link keeps
            # resolving to the same rows it always did.
            condition = Q(status=status_f)
            label     = status_labels.get(status_f, status_f)
        qs = qs.filter(condition)
        desc.append(f"Status: {label}")

    if type_f:
        qs = qs.filter(violation_type=type_f)
        desc.append(f"Type: {type_labels.get(type_f, type_f)}")

    if search:
        # Searches the identity snapshot, so a violation whose vehicle or owner
        # account has since been removed is still findable by the plate and name
        # it was issued under. Email and notes are here because the screen's own
        # search box matches them — a term that narrows the table to four rows
        # must not export forty.
        qs = qs.filter(Q(plate_number__icontains=search) |
                       Q(conduction_number__icontains=search) |
                       Q(owner_name__icontains=search) |
                       Q(owner_email__icontains=search) |
                       Q(notes__icontains=search))
        desc.append(f"Search: '{search}'")

    return qs.order_by('-issued_at'), desc


# Turns rows into the flat cells both report formats take.
def _violation_report_rows(qs):
    from django.utils import timezone as tz
    # Built once outside the loop: get_..._display() per row would repeat this
    # lookup for every violation in the file.
    type_labels   = dict(Violation.Type.choices)
    status_labels = dict(Violation.Status.choices)
    rows = []
    for i, v in enumerate(qs, start=1):
        # Snapshot first, live record second, dash last - in that order on
        # purpose. A violation outlives the vehicle and the account it was
        # issued against (both FKs are SET_NULL), so the name and plate written
        # onto the row at the time are the only ones guaranteed to survive; the
        # live objects are the fallback for older rows that predate the
        # snapshot, not the other way round.
        plate     = v.identifier or '—'
        owner     = v.owner_name or (v.vehicle.user.full_name
                                     if (v.vehicle and v.vehicle.user) else '') or '—'
        # 'System' rather than a dash: a row with no issuer was written by the
        # detector, not by a person, and a reader should not be left wondering
        # whose name went missing.
        issued_by = v.issued_by.full_name if v.issued_by else 'System'
        rows.append([
            i,
            tz.localtime(v.issued_at).strftime('%b %d, %Y %I:%M %p'),
            plate, owner,
            type_labels.get(v.violation_type, v.violation_type),
            f"{v.fine_amount:.2f}",
            status_labels.get(v.status, v.status),
            issued_by,
        ])
    return rows


# The one line under the report title that says what is in it. 'All records'
# rather than an empty string when nothing was filtered: the subtitle should
# still assert something, and the count is what a reader checks the table
# against.
#
# Noted, with no code changed: `request` is never read and the `tz` import is
# never used. Both are left over from when this line carried "Generated <when>
# by <who>" itself; branded_pdf_response takes generated_by as its own argument
# and prints it, and the Excel builder composes its own. Harmless, but the
# signature promises a dependency this function does not have.
def _violation_report_subtitle(request, desc, count):
    from django.utils import timezone as tz
    body = ('; '.join(desc) if desc else 'All records') + f" · {count} entries"
    return body


class ViolationReportExcelView(APIView):
    """Download the (filtered) violations as a branded Excel report — admin only."""
    permission_classes = [IsCDSOOrAdmin]

    def get(self, request):
        from django.utils import timezone as tz
        from report_utils import branded_excel_response, report_filename
        qs, desc = _filter_violations_report(request)
        # Capped at 5,000, and the slice is applied to the QUERYSET so it
        # reaches the database as a LIMIT - 100,000 rows are never fetched and
        # thrown away. len(rows) therefore counts what is actually in the file,
        # which is what the subtitle goes on to state.
        rows = _violation_report_rows(qs[:5000])
        # The Excel subtitle carries who generated it and when; the PDF below
        # does not, because branded_pdf_response takes generated_by as its own
        # argument and prints it into the letterhead itself.
        subtitle = (f"Generated {tz.localtime().strftime('%B %d, %Y %I:%M %p')} "
                    f"by {getattr(request.user, 'full_name', '')} · "
                    + _violation_report_subtitle(request, desc, len(rows)))
        return branded_excel_response(
            filename=report_filename('Violations Report', 'xlsx'),
            sheet_title='Violations',
            report_title='Violations Report',
            subtitle=subtitle,
            headers=VIOLATION_REPORT_HEADERS,
            rows=rows,
            col_widths=[5, 21, 16, 26, 22, 12, 14, 22],
        )


class ViolationReportPdfView(APIView):
    """Download the (filtered) violations as a branded PDF report — admin only."""
    permission_classes = [IsCDSOOrAdmin]

    def get(self, request):
        from django.utils import timezone as tz
        from report_utils import branded_pdf_response, report_filename
        qs, desc = _filter_violations_report(request)
        rows = _violation_report_rows(qs[:5000])
        return branded_pdf_response(
            filename=report_filename('Violations Report', 'pdf'),
            report_title='Violations Report',
            subtitle=_violation_report_subtitle(request, desc, len(rows)),
            generated_by=getattr(request.user, 'full_name', ''),
            generated_by_role=getattr(request.user, 'get_role_display', lambda: '')(),   # the preparer's position on the signature block
            headers=VIOLATION_REPORT_HEADERS,
            rows=rows,
            # Owner names ran past their column while Issued By sat mostly
            # empty (27pt used of 146pt). 6mm moves across; total unchanged.
            col_widths_mm=[10, 34, 26, 56, 40, 22, 30, 49],
        )


# ── Confiscated accounts ──────────────────────────────────────────────────────
# Guards need this at the gate and in the parking view: an account serving a
# violation penalty may not enter and may not park, and a car turning up during
# the penalty is a fresh offence. The list is read-only for guards and
# actionable for the CDSO.

# One shape for a confiscated account, used by all three endpoints below so
# the list and the two actions cannot describe the same account differently.
def _confiscation_payload(user):
    # hasattr rather than a bare attribute read: this is also called with the
    # User returned by an action, and a related manager is not guaranteed on
    # every object that reaches here. An account with no vehicles is a normal
    # state, not an error.
    plates = list(
        user.vehicles.values_list('plate_number', flat=True)
    ) if hasattr(user, 'vehicles') else []
    return {
        'id':                  user.id,
        'user_code':           user.user_code,
        'full_name':           user.full_name,
        'email':               user.email,
        'owner_type':          user.owner_type,
        # Empty strings dropped: an e-bike or a brand-new car can be on file
        # with no plate, and a blank chip in the guard's list reads as a
        # rendering fault rather than as "this vehicle has no plate".
        'plates':              [p for p in plates if p],
        'confiscation_level':  user.confiscation_level,
        'confiscated_at':      user.confiscated_at,
        'confiscated_until':   user.confiscated_until,
        'days_left':           user.confiscation_days_left,
        'reason':              user.confiscation_reason,
        'registration_banned': user.registration_banned,
        # Sent as its own flag rather than left for the client to infer from a
        # null date. "No end date" and "the date failed to load" look identical
        # on the wire, and the two mean opposite things to a guard deciding
        # whether to let a car through.
        'is_indefinite':       user.confiscated_until is None,
    }


class ConfiscatedAccountsView(APIView):
    """Every account currently serving a violation penalty.

    Readable by any signed-in role — a guard cannot act on it if they cannot
    see it — while the actions below stay admin-only.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from .penalty import confiscated_owners
        owners = confiscated_owners().prefetch_related('vehicles')
        return Response([_confiscation_payload(u) for u in owners])


class LiftConfiscationView(APIView):
    """CDSO lifts a confiscation early.

    Leaves the violations standing: forgiving the penalty is not the same as
    saying the offences never happened, and wiping the ladder here would let
    the next offence start again at strike one. Use the lift action on the
    violation itself for a genuine false alarm.
    """
    permission_classes = [IsCDSOOrAdmin]

    def post(self, request, pk):
        from accounts.models import User
        user = get_object_or_404(User, pk=pk)
        if not user.confiscation_level:
            return Response({'detail': 'This account is not confiscated.'},
                            status=http_status.HTTP_400_BAD_REQUEST)
        # Read before clearing: the audit line states which rung was lifted,
        # and clear_confiscation is what erases that number.
        was = user.confiscation_level
        user.clear_confiscation()
        audit(request, AuditLog.Action.RECORD_UPDATED,
              f"Confiscation lifted | {user.full_name} ({user.user_code}) | "
              f"Was offence {was} of 3 | By: {request.user.full_name}")
        return Response(_confiscation_payload(user))


class RegistrationPermissionView(APIView):
    """Let a 3rd-offence owner register again, or withdraw that permission.

    The ladder blocks re-registration on the 3rd strike, but the rule is
    explicitly at the CDSO's discretion, so this is a deliberate, audited human
    decision rather than something the system reverses on its own.
    """
    permission_classes = [IsCDSOOrAdmin]

    def post(self, request, pk):
        from accounts.models import User
        user = get_object_or_404(User, pk=pk)
        # Noted, with no code changed: the default is True, so a POST with no
        # body GRANTS permission rather than withdrawing it. That is the
        # permissive direction for a flag the 3rd strike sets - the screen
        # always sends the value explicitly, so it has never mattered, but the
        # safer default for a block being lifted would be the block staying on.
        allow = bool(request.data.get('allow', True))
        user.registration_banned = not allow
        user.save(update_fields=['registration_banned'])
        audit(request, AuditLog.Action.RECORD_UPDATED,
              f"Re-registration {'allowed' if allow else 'blocked'} | "
              f"{user.full_name} ({user.user_code}) | By: {request.user.full_name}")
        return Response(_confiscation_payload(user))

# =============================================================================
# WHAT THIS FILE IS FOR
#
# One table, Violation, plus the rules of the penalty system around it.
#
# A violation is a disciplinary record: a vehicle did something it should not
# have (entered without authorisation, parked across two bays, overstayed), and
# the campus responds by withdrawing that account's access for a period that
# grows with each offence. There is no fine any more; the older fine fields are
# kept only so historical rows still display.
#
# The two ideas to hold on to:
#
#   * The ladder counts per ACCOUNT, not per vehicle and not per offence type.
#     Three different kinds of offence still reach a third strike, and swapping
#     cars does not reset anything.
#
#   * Identity is SNAPSHOTTED onto the row when the violation is issued: plate,
#     owner name and email are copied here. That is what keeps a violation
#     readable after the vehicle or the account is gone, which matters because
#     the record is meant to outlive both.
# =============================================================================

from decimal import Decimal                  # money: exact amounts (only the legacy fine path uses it now)
from django.db import models
from vehicles.models import Vehicle          # a violation is issued against a vehicle

FINE_STANDARD    = Decimal('20.00')   # legacy
FINE_REPEAT      = Decimal('10.00')   # legacy
REPEAT_THRESHOLD = 3                   # legacy
# (Those three are still read by compute_fine() at the bottom, which remains for
# the old fine-based violation types. Nothing in the ladder below uses them.)

# ── The penalty ladder ────────────────────────────────────────────────────────
# Offences no longer carry a fine. Each one costs the owner their campus access
# for a period that grows with the count:
#
#   1st offence — account confiscated for 1 week
#   2nd offence — account confiscated for 2 weeks
#   3rd offence — confiscated for the rest of the registration period, and the
#                 person may not register again unless the CDSO allows it
#
# The count is per ACCOUNT across every tracked type, not per type: three
# different kinds of offence still add up to a third strike. Cleared and lifted
# violations drop out of the count.
CONFISCATION_DAYS = {1: 7, 2: 14}     # 3 runs to the end of the registration period

# Types that count toward the ladder.
NEW_STYLE_TYPES = {
    'unauthorized_entry', 'double_parking', 'time_exceed', 'confiscated_activity',
}


# One recorded offence.
class Violation(models.Model):
    # What was done. The first four (plus the legacy ones) are what a guard or
    # a camera can report; see NEW_STYLE_TYPES for which ones count.
    class Type(models.TextChoices):
        UNAUTHORIZED_ENTRY   = 'unauthorized_entry',   'Unauthorized Entry'
        DOUBLE_PARKING       = 'double_parking',       'Double Parking'
        TIME_EXCEED          = 'time_exceed',           'Time Exceed'      # stayed longer than the rule allows
        NO_STICKER           = 'no_sticker',           'No Sticker'
        EXPIRED_REGISTRATION = 'expired_registration', 'Expired Registration'
        # Logged when a confiscated account is caught entering or parking. The
        # penalty is meant to keep them off campus, so being detected during it
        # is itself an offence and moves them up the ladder.
        CONFISCATED_ACTIVITY = 'confiscated_activity', 'Activity While Confiscated'
        UNAUTHORIZED         = 'unauthorized',          'Unauthorized (Legacy)'
        OTHER                = 'other',                'Other'

    # Where the case has got to.
    class Status(models.TextChoices):
        WARNING     = 'warning',     'Warning'          # issued and still counting
        # Kept so historical rows issued under the old fine system still render.
        # Nothing sets it any more — offences cost access, not money.
        FEE_IMPOSED = 'fee_imposed', 'Fee Imposed (Legacy)'
        CLEARED     = 'cleared',     'Cleared'
        # Voided as a false alarm. Distinct from CLEARED: cleared means the
        # offence happened and was settled (OR presented); lifted means it
        # should never have been issued, so it stops counting toward the
        # offence ladder and the remaining ones renumber beneath it.
        LIFTED      = 'lifted',      'Lifted (False Alarm)'

    id             = models.BigAutoField(primary_key=True, db_column='violation_id')
    # SET_NULL, not CASCADE. A violation is a disciplinary and financial record;
    # it must outlive the vehicle row it was issued against, exactly as AuditLog
    # outlives the account it describes. Under CASCADE, deleting an owner took
    # every violation with it — which also erased the 3rd-offense
    # registration_blocked flags, so a deleted-and-re-registered owner came back
    # with a clean record. That is the outcome the offence ladder exists to
    # prevent.
    vehicle        = models.ForeignKey(
        Vehicle, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='violations',
    )

    # Identity snapshotted at issue time. The list screens used to resolve the
    # owner live through vehicle.user, so archiving an account (which clears
    # vehicle.user) blanked the name on violations that were still perfectly
    # valid. Stored values cannot be un-resolved by a later change to something
    # else.
    plate_number      = models.CharField(max_length=20, blank=True, default='', db_index=True)
    conduction_number = models.CharField(max_length=50, blank=True, default='')
    owner_name        = models.CharField(max_length=150, blank=True, default='')
    owner_email       = models.CharField(max_length=254, blank=True, default='')   # also the fallback key in active_for_owner

    # The account the ladder is counted against. The snapshot fields above are
    # for display and survive deletion; this FK is what the offence count and
    # the confiscation are keyed on. SET_NULL for the same reason as `vehicle`
    # — the disciplinary record outlives the account.
    #
    # Resolved from vehicle.user at issue time. Counting through `vehicle`
    # instead would restart someone's ladder the moment they swapped cars.
    owner             = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='violations', db_index=True,
    )

    violation_type = models.CharField(max_length=30, choices=Type.choices)
    notes          = models.TextField(blank=True)      # the guard's description of what happened
    fine_amount    = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('0.00'))   # legacy; 0.00 under the ladder
    is_resolved    = models.BooleanField(default=False)   # the dashboard's "open violations" count reads this
    is_released    = models.BooleanField(default=False)   # legacy release flag from the fine era
    issued_at      = models.DateTimeField(auto_now_add=True)
    issued_by      = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='issued_violations',    # the person who issued it, when a person did
    )
    on_duty_guard  = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='on_duty_violations',
        help_text="Guard clocked in at the gate when this violation was auto-logged.",
    )

    # Offense tracking (new-style violations only — null for legacy)
    offense_number       = models.PositiveSmallIntegerField(null=True, blank=True)   # 1, 2 or 3: which rung of the ladder
    status               = models.CharField(
        max_length=20, choices=Status.choices, default=Status.WARNING,
    )
    registration_blocked = models.BooleanField(default=False)   # set on a 3rd strike: bars registering again
    cdso_report_issued   = models.BooleanField(default=False)   # a formal report was handed to the CDSO
    official_receipt     = models.CharField(max_length=100, blank=True)   # OR number presented when clearing

    # Lift trail — who voided this as a false alarm, when and why. Kept rather
    # than deleting the row so the decision itself stays auditable.
    lifted_reason        = models.TextField(blank=True)
    lifted_at            = models.DateTimeField(null=True, blank=True)
    lifted_by            = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='lifted_violations',
    )

    class Meta:
        db_table = 'tbl_violation'
        indexes = [
            # Scan hot path: the once-per-day dedup check before auto-logging.
            models.Index(fields=['vehicle', 'violation_type', '-issued_at'],
                         name='violation_vehicle_type_time'),
            # List screens and the date-range report filters.
            models.Index(fields=['-issued_at'], name='violation_issued_at'),
            models.Index(fields=['status', '-issued_at'], name='violation_status_time'),
            models.Index(fields=['issued_by', '-issued_at'], name='violation_issued_by_time'),
            # Dashboard: open (unresolved) violation count.
            models.Index(fields=['is_resolved'], name='violation_is_resolved'),
        ]

    # Statuses that stop a violation counting toward the offence ladder.
    INACTIVE_STATUSES = ('cleared', 'lifted')

    # How this violation names its vehicle on screen, without depending on the
    # vehicle row still existing.
    @property
    def identifier(self) -> str:
        """Plate if there is one, otherwise the conduction number — the same
        rule Vehicle.identifier uses, but read from the snapshot so it survives
        the vehicle being deleted."""
        return (self.plate_number or self.conduction_number
                or (self.vehicle.identifier if self.vehicle_id else ''))   # last resort: ask the vehicle, if it is still there

    # Copies the vehicle's and owner's details onto this row, so the record can
    # stand on its own later.
    def _snapshot_identity(self) -> None:
        """Copy the vehicle's and owner's identity onto this row."""
        vehicle = self.vehicle
        if vehicle is None:
            return                                   # nothing to copy from
        self.plate_number      = vehicle.plate_number or ''
        self.conduction_number = vehicle.conduction_number or ''
        owner = vehicle.user                         # may be None for an unregistered vehicle
        if owner is not None:
            self.owner_name  = owner.full_name or ''
            self.owner_email = owner.email or ''
            if self.owner_id is None:
                self.owner = owner                   # only if the caller did not already name an account
            return

        # No account behind the plate — a visitor on a gate-issued pass, or a
        # vehicle a guard recorded by hand. There IS a name for that person;
        # it just does not live on a User row, so every list screen showed a
        # dash where the offender's name belongs. Read it from whichever record
        # the gate wrote when they came in.
        self.owner_name = self._gate_recorded_name() or ''

    # The name the gate took down for someone with no account, newest first.
    def _gate_recorded_name(self) -> str:
        """Visitor or driver name for an unregistered vehicle, or '' if none.

        Imported inside the method: scanning imports violations on the hot scan
        path, so naming it at module scope here would close the circle.
        """
        from scanning.models import AccessLog, VisitorPass

        plate = self.plate_number or ''
        pass_ = (VisitorPass.objects
                 .filter(vehicle=self.vehicle)
                 .exclude(visitor_name='')
                 .order_by('-entered_at')
                 .values_list('visitor_name', flat=True)
                 .first())
        if pass_:
            return pass_

        # A plateless or unreadable vehicle the guard wrote up by hand. Matched
        # on the plate rather than the vehicle FK because those rows are
        # recorded against the plate text the guard typed.
        if plate:
            driver = (AccessLog.objects
                      .filter(plate_number=plate, is_unrecognized=True)
                      .exclude(driver_name='')
                      .order_by('-scanned_at')
                      .values_list('driver_name', flat=True)
                      .first())
            if driver:
                return driver
        return ''


    # Takes that snapshot exactly once, when the row is first created.
    def save(self, *args, **kwargs):
        """Take the identity snapshot once, when the violation is first issued.

        Done here rather than at each call site because violations are created
        from a dozen places — the gate scanner, the parking camera, the CDSO
        screen, the tests — and a snapshot that depends on every caller
        remembering to fill it is one that will be missing on the row that
        matters. Only on insert: re-saving must never re-resolve identity from a
        vehicle whose owner has since changed.
        """
        if self._state.adding and self.vehicle_id and not self.plate_number:   # new row, has a vehicle, not already filled
            self._snapshot_identity()
        super().save(*args, **kwargs)

    # Every violation that still counts against this account.
    @classmethod
    def active_for_owner(cls, owner):
        """Every violation still counting toward this account's ladder.

        Cleared ones are settled and lifted ones never happened, so neither
        counts. Falls back to the email snapshot so offences issued before the
        owner FK existed — or after the account row was replaced — still count
        against the same person.
        """
        if owner is None:
            return cls.objects.none()                # an empty result, not an error
        from django.db.models import Q
        q = Q(owner=owner)                           # rows linked to this account
        if owner.email:
            q |= Q(owner__isnull=True, owner_email__iexact=owner.email)   # plus unlinked rows carrying their email
        return (cls.objects.filter(q, offense_number__isnull=False)       # ladder types only; legacy rows have no number
                           .exclude(status__in=cls.INACTIVE_STATUSES))    # drop cleared and lifted

    # What strike the next offence would be.
    @classmethod
    def compute_offense_number(cls, owner) -> int:
        """The strike number the next offence for this account will carry.

        Counted per ACCOUNT across every tracked type — three different kinds
        of offence still reach a third strike. Capped at 3: the ladder has no
        rung above "confiscated for the rest of the period".
        """
        return min(cls.active_for_owner(owner).count() + 1, 3)   # count what stands, add this one, cap at 3

    # Renumbers the strikes after one is lifted, so the ladder stays truthful.
    @classmethod
    def resequence_offenses(cls, owner) -> int:
        """Renumber an account's active violations, oldest first.

        `offense_number` is stamped at creation and never revisited, so lifting
        the 1st of two warnings used to leave the survivor still reading
        "offense 2" — the count the owner sees, and the ladder that decides the
        penalty, would both stay wrong. This walks what remains and rewrites the
        strike numbers to match.

        Only the 3rd strike holds registration, so dropping below it releases
        that hold as well.

        Returns the number of rows changed.
        """
        active = list(cls.active_for_owner(owner).order_by('issued_at', 'id'))   # oldest first; id breaks ties

        changed = 0
        for idx, v in enumerate(active, start=1):    # idx is the position in the list: 1, 2, 3...
            n = min(idx, 3)                          # anything beyond the third still reads as a third strike
            blocks = (n == 3)                        # only a third strike holds registration

            fields = []                              # collect just the columns that actually change
            if v.offense_number != n:
                v.offense_number = n; fields.append('offense_number')
            if v.registration_blocked != blocks:
                v.registration_blocked = blocks; fields.append('registration_blocked')

            if fields:
                v.save(update_fields=fields)         # write only those columns
                changed += 1
        return changed                               # so the caller can report how many were renumbered

    # The old money-based rule, still used by the legacy violation types.
    @classmethod
    def compute_fine(cls, vehicle) -> Decimal:
        """Legacy fine logic for old-style violation types."""
        existing = cls.objects.filter(vehicle=vehicle).count()   # every violation on this vehicle, whatever its status
        return FINE_REPEAT if existing >= REPEAT_THRESHOLD else FINE_STANDARD

    # Does this plate carry a hold that should stop a new registration?
    @classmethod
    def registration_block_for_plate(cls, plate_number: str):
        """
        Return the QuerySet of violations that flag this plate for additional
        review at vehicle registration (a 3rd-offense fee sets registration_blocked
        and it stays set even after the fee is cleared). Empty if the plate is clear.
        """
        plate = (plate_number or '').strip().upper()   # compare in the stored form
        if not plate:
            return cls.objects.none()
        # Matches the snapshot, not the FK. Deleting the owner used to delete
        # the vehicle and cascade the violations away, so this query returned
        # nothing and the registration hold silently lifted itself.
        return cls.objects.filter(
            plate_number=plate,
            registration_blocked=True,
        ).order_by('-issued_at')                      # newest first, so the reviewer sees the latest hold

    def __str__(self):
        return f"{self.identifier} — {self.violation_type}"

from decimal import Decimal
from django.db import models
from vehicles.models import Vehicle

FINE_STANDARD    = Decimal('20.00')   # legacy
FINE_REPEAT      = Decimal('10.00')   # legacy
REPEAT_THRESHOLD = 3                   # legacy

FEE_THIRD_OFFENSE = Decimal('150.00')

# Types that escalate to ₱150 fee on 3rd offense and block entry
FEE_ESCALATING_TYPES = {'unauthorized_entry', 'time_exceed'}

# Types that use the new 3-offense tracking system
NEW_STYLE_TYPES = {'unauthorized_entry', 'double_parking', 'time_exceed'}


class Violation(models.Model):
    class Type(models.TextChoices):
        UNAUTHORIZED_ENTRY   = 'unauthorized_entry',   'Unauthorized Entry'
        DOUBLE_PARKING       = 'double_parking',       'Double Parking'
        TIME_EXCEED          = 'time_exceed',           'Time Exceed'
        NO_STICKER           = 'no_sticker',           'No Sticker'
        EXPIRED_REGISTRATION = 'expired_registration', 'Expired Registration'
        UNAUTHORIZED         = 'unauthorized',          'Unauthorized (Legacy)'
        OTHER                = 'other',                'Other'

    class Status(models.TextChoices):
        WARNING     = 'warning',     'Warning'
        FEE_IMPOSED = 'fee_imposed', 'Fee Imposed'
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
    owner_email       = models.CharField(max_length=254, blank=True, default='')

    violation_type = models.CharField(max_length=30, choices=Type.choices)
    notes          = models.TextField(blank=True)
    fine_amount    = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('0.00'))
    is_resolved    = models.BooleanField(default=False)
    is_released    = models.BooleanField(default=False)
    issued_at      = models.DateTimeField(auto_now_add=True)
    issued_by      = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='issued_violations',
    )
    on_duty_guard  = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='on_duty_violations',
        help_text="Guard clocked in at the gate when this violation was auto-logged.",
    )
    evidence       = models.ImageField(upload_to='violations/evidence/', blank=True, null=True)

    # Offense tracking (new-style violations only — null for legacy)
    offense_number       = models.PositiveSmallIntegerField(null=True, blank=True)
    status               = models.CharField(
        max_length=20, choices=Status.choices, default=Status.WARNING,
    )
    registration_blocked = models.BooleanField(default=False)
    cdso_report_issued   = models.BooleanField(default=False)
    official_receipt     = models.CharField(max_length=100, blank=True)

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

    @property
    def identifier(self) -> str:
        """Plate if there is one, otherwise the conduction number — the same
        rule Vehicle.identifier uses, but read from the snapshot so it survives
        the vehicle being deleted."""
        return (self.plate_number or self.conduction_number
                or (self.vehicle.identifier if self.vehicle_id else ''))

    def _snapshot_identity(self) -> None:
        """Copy the vehicle's and owner's identity onto this row."""
        vehicle = self.vehicle
        if vehicle is None:
            return
        self.plate_number      = vehicle.plate_number or ''
        self.conduction_number = vehicle.conduction_number or ''
        owner = vehicle.user
        if owner is not None:
            self.owner_name  = owner.full_name or ''
            self.owner_email = owner.email or ''

    def save(self, *args, **kwargs):
        """Take the identity snapshot once, when the violation is first issued.

        Done here rather than at each call site because violations are created
        from a dozen places — the gate scanner, the parking camera, the CDSO
        screen, the tests — and a snapshot that depends on every caller
        remembering to fill it is one that will be missing on the row that
        matters. Only on insert: re-saving must never re-resolve identity from a
        vehicle whose owner has since changed.
        """
        if self._state.adding and self.vehicle_id and not self.plate_number:
            self._snapshot_identity()
        super().save(*args, **kwargs)

    @classmethod
    def compute_offense_number(cls, vehicle, violation_type) -> int:
        """Count active new-style violations of this type to get the next offense number.

        Cleared ones are settled and lifted ones never happened, so neither
        counts.
        """
        count = cls.objects.filter(
            vehicle=vehicle,
            violation_type=violation_type,
            offense_number__isnull=False,
        ).exclude(status__in=cls.INACTIVE_STATUSES).count()
        return min(count + 1, 3)

    @classmethod
    def resequence_offenses(cls, vehicle, violation_type) -> int:
        """Renumber a vehicle's active violations of one type, oldest first.

        `offense_number` is stamped at creation and never revisited, so lifting
        the 1st of two warnings used to leave the survivor still reading
        "offense 2" — the count the owner sees, and the ladder that decides the
        3rd-offense fee, would both stay wrong. This walks what remains and
        rewrites the number, the fee, and the status to match.

        A violation that was already settled with an Official Receipt is left
        alone: money changed hands, and renumbering it would either invent a
        refund or silently re-impose a fee.

        Returns the number of rows changed.
        """
        from decimal import Decimal

        active = list(
            cls.objects.filter(
                vehicle=vehicle,
                violation_type=violation_type,
                offense_number__isnull=False,
            ).exclude(status__in=cls.INACTIVE_STATUSES).order_by('issued_at', 'id')
        )

        changed = 0
        for idx, v in enumerate(active, start=1):
            n = min(idx, 3)
            is_fee_event = (n == 3 and violation_type in FEE_ESCALATING_TYPES)
            new_status = cls.Status.FEE_IMPOSED if is_fee_event else cls.Status.WARNING
            new_fine   = FEE_THIRD_OFFENSE if is_fee_event else Decimal('0.00')

            fields = []
            if v.offense_number != n:
                v.offense_number = n; fields.append('offense_number')
            if v.status != new_status:
                v.status = new_status; fields.append('status')
            if v.fine_amount != new_fine:
                v.fine_amount = new_fine; fields.append('fine_amount')
            # Dropping below the 3rd offence releases the registration hold that
            # only the fee event should ever have set.
            if v.registration_blocked != is_fee_event:
                v.registration_blocked = is_fee_event; fields.append('registration_blocked')

            if fields:
                v.save(update_fields=fields)
                changed += 1
        return changed

    @classmethod
    def compute_fine(cls, vehicle) -> Decimal:
        """Legacy fine logic for old-style violation types."""
        existing = cls.objects.filter(vehicle=vehicle).count()
        return FINE_REPEAT if existing >= REPEAT_THRESHOLD else FINE_STANDARD

    @classmethod
    def registration_block_for_plate(cls, plate_number: str):
        """
        Return the QuerySet of violations that flag this plate for additional
        review at vehicle registration (a 3rd-offense fee sets registration_blocked
        and it stays set even after the fee is cleared). Empty if the plate is clear.
        """
        plate = (plate_number or '').strip().upper()
        if not plate:
            return cls.objects.none()
        # Matches the snapshot, not the FK. Deleting the owner used to delete
        # the vehicle and cascade the violations away, so this query returned
        # nothing and the registration hold silently lifted itself.
        return cls.objects.filter(
            plate_number=plate,
            registration_blocked=True,
        ).order_by('-issued_at')

    def __str__(self):
        return f"{self.identifier} — {self.violation_type}"

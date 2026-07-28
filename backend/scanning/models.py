from django.db import models
from django.utils import timezone
from vehicles.models import Vehicle

# Days each schedule covers
SCHEDULE_DAYS = {
    'MWF':  [0, 2, 4],   # Mon=0, Wed=2, Fri=4
    'TTHS': [1, 3, 5],   # Tue=1, Thu=3, Sat=5
    'ANY':  [0, 1, 2, 3, 4, 5, 6],
}

class Gate(models.Model):
    """A campus entry gate. Seeded with gate1/gate4; admins can add more from
    System Settings as the school expands. gate_id is the stable slug stored
    on shifts, access logs and camera assignments (e.g. 'gate2')."""
    id         = models.BigAutoField(primary_key=True, db_column='gate_pk')
    gate_id    = models.SlugField(max_length=20, unique=True)
    label      = models.CharField(max_length=100)
    is_active  = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tbl_gate'
        ordering = ['gate_id']

    def __str__(self):
        return f"{self.label} ({self.gate_id})"

    @classmethod
    def active_ids(cls):
        """Slugs of active gates; falls back to the two founding gates so login
        never locks out if the table is empty."""
        ids = list(cls.objects.filter(is_active=True).values_list('gate_id', flat=True))
        return ids or ['gate1', 'gate4']


class Office(models.Model):
    id      = models.BigAutoField(primary_key=True, db_column='office_id')
    name    = models.CharField(max_length=100)
    contact = models.CharField(max_length=50, blank=True)
    email   = models.EmailField(blank=True)

    def __str__(self):
        return self.name

    class Meta:
        db_table = 'tbl_office'


class VisitorPass(models.Model):
    class Status(models.TextChoices):
        ACTIVE  = 'active',  'Active'   # pass issued, visitor is inside
        EXITED  = 'exited',  'Exited'   # guard scanned exit, visitor has left
        EXPIRED = 'expired', 'Expired'  # valid_date passed without exit scan

    id         = models.BigAutoField(primary_key=True, db_column='visitor_pass_id')
    vehicle    = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name='visitor_passes')
    plate_number = models.CharField(max_length=20, blank=True)      # denormalised for quick display
    office     = models.ForeignKey(
        Office, on_delete=models.SET_NULL, null=True, blank=True,   # office being visited (optional)
    )
    purpose    = models.TextField(blank=True)
    status     = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    issued_by  = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='issued_passes',
    )
    allowed_duration = models.PositiveIntegerField(default=60, help_text="Allowed time inside in minutes")
    valid_date = models.DateField(default=timezone.now)
    entered_at = models.DateTimeField(auto_now_add=True)
    # Set when the guard confirms the thermal slip was printed — the visitor's
    # entry is only logged in the AccessLog at that moment.
    printed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    exited_at  = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'tbl_visitor_pass'
        indexes = [
            # Dashboard: passes still active for today.
            models.Index(fields=['status', 'valid_date'],
                         name='visitorpass_status_date'),
        ]

    @property
    def qr_payload(self):
        """Encoded in the QR printed on the slip; scanned at the gate to record exit."""
        return f'SLC-VISITOR:{self.pk}'

    def __str__(self):
        office_name = self.office.name if self.office else 'No office'
        return f"{self.plate_number} → {office_name} ({self.status})"


class AccessLog(models.Model):
    class Status(models.TextChoices):
        AUTHORIZED   = 'authorized',    'Authorized'
        DENIED       = 'denied',        'Denied'
        WRONG_DAY    = 'wrong_day',     'Wrong Day'
        UNKNOWN      = 'unknown',       'Unknown Plate'
        UNREADABLE   = 'unreadable',    'Unreadable'
        EXITED       = 'exited',        'Exited'

    id             = models.BigAutoField(primary_key=True, db_column='access_log_id')
    vehicle        = models.ForeignKey(Vehicle, on_delete=models.SET_NULL, null=True, blank=True)
    plate_number   = models.CharField(max_length=20, blank=True)
    vehicle_type   = models.CharField(max_length=20, blank=True)
    digital_id_used = models.CharField(max_length=50, blank=True)
    status         = models.CharField(max_length=20, choices=Status.choices)
    gate_id         = models.CharField(max_length=50, default='main')
    denied_reason   = models.CharField(max_length=255, blank=True)
    is_override     = models.BooleanField(default=False)
    override_reason = models.CharField(max_length=255, blank=True)
    paired_entry    = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='exit_log',
        help_text="For exit logs: points to the matching entry log.",
    )
    snapshot       = models.ImageField(upload_to='snapshots/', blank=True)
    scanned_at     = models.DateTimeField(auto_now_add=True)
    scanned_by     = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='scans',
    )
    on_duty_guard  = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='on_duty_scans',
        help_text="Guard clocked in at this gate when the scan happened.",
    )

    class Meta:
        db_table = 'tbl_access_log'
        ordering = ['-scanned_at']
        indexes = [
            # The scan hot path: _inside_state / _already_inside / _pair_entry_exit
            # and the three cooldown checks all ask
            # "(plate, status) within a time window, newest first".
            models.Index(fields=['plate_number', 'status', '-scanned_at'],
                         name='accesslog_plate_status_time'),
            # Meta.ordering — every unfiltered list page sorts by this.
            models.Index(fields=['-scanned_at'], name='accesslog_scanned_at'),
            # Dashboard: per-status counts over today / the past week.
            models.Index(fields=['status', '-scanned_at'],
                         name='accesslog_status_time'),
            # Security dashboard + per-guard stats ("my scans today").
            models.Index(fields=['scanned_by', '-scanned_at'],
                         name='accesslog_scanned_by_time'),
            # Per-vehicle entry history.
            models.Index(fields=['vehicle', '-scanned_at'],
                         name='accesslog_vehicle_time'),
            # Gate filtering on the entries/operations screens.
            models.Index(fields=['gate_id', '-scanned_at'],
                         name='accesslog_gate_time'),
        ]

    def save(self, *args, **kwargs):
        # scanned_by records whose session triggered the scan (may be an admin
        # watching a camera feed); on_duty_guard records who was clocked in at
        # the gate at that moment.
        if self._state.adding and self.on_duty_guard_id is None and self.gate_id:
            self.on_duty_guard = active_guard_for_gate(self.gate_id)
        super().save(*args, **kwargs)


class GuardShift(models.Model):
    id = models.BigAutoField(primary_key=True, db_column='guard_shift_id')
    guard = models.ForeignKey(
        'accounts.User', on_delete=models.CASCADE, related_name='shifts',
    )
    gate = models.CharField(max_length=10)  # 'gate1' or 'gate4'
    clocked_in_at = models.DateTimeField(auto_now_add=True)
    clocked_out_at = models.DateTimeField(null=True, blank=True)
    clocked_out_by = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='clocked_out_shifts',
    )

    class Meta:
        db_table = 'tbl_guard_shift'
        ordering = ['-clocked_in_at']
        indexes = [
            # "shifts for this guard today" and the open-shift lookup.
            models.Index(fields=['guard', '-clocked_in_at'],
                         name='guardshift_guard_time'),
            models.Index(fields=['gate', '-clocked_in_at'],
                         name='guardshift_gate_time'),
            # Finding the currently-active shift (clocked_out_at IS NULL).
            models.Index(fields=['clocked_out_at'], name='guardshift_clocked_out'),
        ]

    def __str__(self):
        return f"{self.guard.full_name} @ {self.gate} — {self.clocked_in_at.strftime('%Y-%m-%d %H:%M')}"


def active_guard_for_gate(gate: str):
    """The guard currently clocked in at this gate, or None."""
    if not gate:
        return None
    shift = (
        GuardShift.objects
        .filter(gate=gate, clocked_out_at__isnull=True)
        .select_related('guard')
        .order_by('-clocked_in_at')
        .first()
    )
    return shift.guard if shift else None


class MLTrainingSample(models.Model):
    SOURCE_CHOICES = [
        ('scan',         'Live Scan'),
        ('manual',       'Manual Label'),
        ('imported',     'Dataset Import'),
    ]

    STATUS_CHOICES = [
        ('unlabeled',    'Unlabeled'),
        ('auto_labeled', 'Auto-Labeled'),
        ('verified',     'Verified'),
        ('rejected',     'Rejected'),
    ]

    id = models.BigAutoField(primary_key=True, db_column='ml_training_sample_id')
    image = models.ImageField(upload_to='ml_samples/')
    plate_number = models.CharField(max_length=20, blank=True)
    bbox = models.JSONField(default=dict, blank=True)
    confidence = models.FloatField(null=True, blank=True)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='scan')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='unlabeled')
    used_in_training = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tbl_ml_training_sample'
        ordering = ['-created_at']
        # 10k+ rows and growing with every scan — the review queue filters by
        # status and always sorts newest-first.
        indexes = [
            models.Index(fields=['-created_at'], name='mlsample_created_at'),
            models.Index(fields=['status', '-created_at'], name='mlsample_status_time'),
        ]

    def __str__(self):
        return f"[{self.status}] {self.plate_number or '?'} ({self.source})"


class PlateRecognitionRecord(models.Model):
    id = models.BigAutoField(primary_key=True, db_column='plate_recognition_record_id')
    track_id = models.IntegerField(db_index=True)
    plate_text = models.CharField(max_length=20, db_index=True)
    detection_confidence = models.FloatField()
    ocr_confidence = models.FloatField()
    timestamp = models.DateTimeField(db_index=True)
    snapshot_path = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = 'tbl_plate_recognition_record'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['plate_text', '-timestamp'], name='plate_text_timestamp_idx'),
            models.Index(fields=['track_id'], name='track_id_idx'),
        ]

    def __str__(self):
        return f"Track {self.track_id}: {self.plate_text} ({self.timestamp})"
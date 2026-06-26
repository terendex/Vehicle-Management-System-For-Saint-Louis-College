from django.db import models
from django.utils import timezone
from vehicles.models import Vehicle

# Days each schedule covers
SCHEDULE_DAYS = {
    'MWF':  [0, 2, 4],   # Mon=0, Wed=2, Fri=4
    'TTHS': [1, 3, 5],   # Tue=1, Thu=3, Sat=5
    'ANY':  [0, 1, 2, 3, 4, 5, 6],
}

class Office(models.Model):
    name    = models.CharField(max_length=100)
    contact = models.CharField(max_length=50, blank=True)
    email   = models.EmailField(blank=True)

    def __str__(self):
        return self.name


class VisitorPass(models.Model):
    class Status(models.TextChoices):
        ACTIVE  = 'active',  'Active'   # pass issued, visitor is inside
        EXITED  = 'exited',  'Exited'   # guard scanned exit, visitor has left
        EXPIRED = 'expired', 'Expired'  # valid_date passed without exit scan

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
    valid_date = models.DateField(default=timezone.now)
    entered_at = models.DateTimeField(auto_now_add=True)
    exited_at  = models.DateTimeField(null=True, blank=True)

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

    vehicle        = models.ForeignKey(Vehicle, on_delete=models.SET_NULL, null=True, blank=True)
    plate_number   = models.CharField(max_length=20, blank=True)
    vehicle_type   = models.CharField(max_length=20, blank=True)
    digital_id_used = models.CharField(max_length=50, blank=True)
    status         = models.CharField(max_length=20, choices=Status.choices)
    gate_id        = models.CharField(max_length=50, default='main')
    denied_reason  = models.CharField(max_length=255, blank=True)
    is_override    = models.BooleanField(default=False)
    override_reason = models.CharField(max_length=255, blank=True)
    snapshot       = models.ImageField(upload_to='snapshots/', blank=True)
    scanned_at     = models.DateTimeField(auto_now_add=True)
    scanned_by     = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='scans',
    )

    class Meta:
        ordering = ['-scanned_at']


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

    image = models.ImageField(upload_to='ml_samples/')
    plate_number = models.CharField(max_length=20, blank=True)
    bbox = models.JSONField(default=dict, blank=True)
    confidence = models.FloatField(null=True, blank=True)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='scan')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='unlabeled')
    used_in_training = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.status}] {self.plate_number or '?'} ({self.source})"


class PlateRecognitionRecord(models.Model):
    track_id = models.IntegerField(db_index=True)
    plate_text = models.CharField(max_length=20, db_index=True)
    detection_confidence = models.FloatField()
    ocr_confidence = models.FloatField()
    timestamp = models.DateTimeField(db_index=True)
    snapshot_path = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['plate_text', '-timestamp'], name='plate_text_timestamp_idx'),
            models.Index(fields=['track_id'], name='track_id_idx'),
        ]

    def __str__(self):
        return f"Track {self.track_id}: {self.plate_text} ({self.timestamp})"
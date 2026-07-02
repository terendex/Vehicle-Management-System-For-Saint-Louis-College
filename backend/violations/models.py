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

    vehicle        = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name='violations')
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

    @classmethod
    def compute_offense_number(cls, vehicle, violation_type) -> int:
        """Count active (uncleared) new-style violations of this type to get the next offense number."""
        count = cls.objects.filter(
            vehicle=vehicle,
            violation_type=violation_type,
            offense_number__isnull=False,
        ).exclude(status=cls.Status.CLEARED).count()
        return min(count + 1, 3)

    @classmethod
    def compute_fine(cls, vehicle) -> Decimal:
        """Legacy fine logic for old-style violation types."""
        existing = cls.objects.filter(vehicle=vehicle).count()
        return FINE_REPEAT if existing >= REPEAT_THRESHOLD else FINE_STANDARD

    def __str__(self):
        return f"{self.vehicle.plate_number} — {self.violation_type}"

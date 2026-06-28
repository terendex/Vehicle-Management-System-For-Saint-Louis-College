from decimal import Decimal
from django.db import models
from vehicles.models import Vehicle

FINE_STANDARD   = Decimal('20.00')
FINE_REPEAT     = Decimal('10.00')
REPEAT_THRESHOLD = 3   # violations already on record before this one triggers reduced fine


class Violation(models.Model):
    class Type(models.TextChoices):
        NO_STICKER           = 'no_sticker',           'No Sticker'
        EXPIRED_REGISTRATION = 'expired_registration', 'Expired Registration'
        UNAUTHORIZED         = 'unauthorized',          'Unauthorized'
        OTHER                = 'other',                 'Other'

    vehicle        = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name='violations')
    violation_type = models.CharField(max_length=30, choices=Type.choices)
    notes          = models.TextField(blank=True)
    fine_amount    = models.DecimalField(max_digits=6, decimal_places=2, default=FINE_STANDARD)
    is_resolved    = models.BooleanField(default=False)
    is_released    = models.BooleanField(default=False)
    issued_at      = models.DateTimeField(auto_now_add=True)
    evidence       = models.ImageField(upload_to='violations/evidence/', blank=True, null=True)

    @classmethod
    def compute_fine(cls, vehicle) -> Decimal:
        """₱20 per violation; ₱10 once the vehicle already has ≥ REPEAT_THRESHOLD on record."""
        existing = cls.objects.filter(vehicle=vehicle).count()
        return FINE_REPEAT if existing >= REPEAT_THRESHOLD else FINE_STANDARD

    def __str__(self):
        return f"{self.vehicle.plate_number} — {self.violation_type}"
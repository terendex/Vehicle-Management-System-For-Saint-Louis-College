from django.db import models
from vehicles.models import Vehicle

class Violation(models.Model):
    class Type(models.TextChoices):
        NO_STICKER           = 'no_sticker',           'No Sticker'
        EXPIRED_REGISTRATION = 'expired_registration', 'Expired Registration'
        UNAUTHORIZED         = 'unauthorized',          'Unauthorized'
        OTHER                = 'other',                 'Other'

    vehicle        = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name='violations')
    violation_type = models.CharField(max_length=30, choices=Type.choices)
    notes          = models.TextField(blank=True)
    is_resolved    = models.BooleanField(default=False)
    issued_at      = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.vehicle.plate_number} — {self.violation_type}"
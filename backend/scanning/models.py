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
        PENDING   = 'pending',   'Pending'
        CONFIRMED = 'confirmed', 'Confirmed'
        REJECTED  = 'rejected',  'Rejected'
        EXPIRED   = 'expired',   'Expired'

    vehicle      = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name='visitor_passes')
    office       = models.ForeignKey(Office, on_delete=models.CASCADE)
    purpose      = models.TextField()
    status       = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    confirmed_by = models.CharField(max_length=100, blank=True)   # office staff name
    valid_date   = models.DateField(default=timezone.now)          # pass is only for this day
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.vehicle.plate_number} → {self.office.name} ({self.status})"


class AccessLog(models.Model):
    class Status(models.TextChoices):
        AUTHORIZED   = 'authorized',    'Authorized'
        DENIED       = 'denied',        'Denied'
        WRONG_DAY    = 'wrong_day',     'Wrong Day'
        PENDING      = 'pending',       'Visitor Pending'
        UNKNOWN      = 'unknown',       'Unknown Plate'
        UNREADABLE   = 'unreadable',    'Unreadable'

    vehicle      = models.ForeignKey(Vehicle, on_delete=models.SET_NULL, null=True, blank=True)
    plate_number = models.CharField(max_length=20)
    status       = models.CharField(max_length=20, choices=Status.choices)
    gate_id      = models.CharField(max_length=50, default='main')
    denied_reason= models.CharField(max_length=255, blank=True)
    snapshot     = models.ImageField(upload_to='snapshots/', blank=True)
    scanned_at   = models.DateTimeField(auto_now_add=True)
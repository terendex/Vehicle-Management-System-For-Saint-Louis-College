from django.db import models

class Owner(models.Model):
    class OwnerType(models.TextChoices):
        STUDENT  = 'student',  'Student'
        FETCHER  = 'fetcher',  'Fetcher/Dropper'
        EMPLOYEE = 'employee', 'Employee'
        VISITOR  = 'visitor',  'Visitor'

    class Schedule(models.TextChoices):
        MWF  = 'MWF',  'Monday-Wednesday-Friday'
        TTHS = 'TTHS', 'Tuesday-Thursday-Saturday'
        ANY  = 'ANY',  'Any Day'               # for employees

    full_name   = models.CharField(max_length=255)
    contact     = models.CharField(max_length=50, blank=True)
    address     = models.TextField(blank=True)
    photo       = models.ImageField(upload_to='owners/', blank=True)
    owner_type  = models.CharField(max_length=20, choices=OwnerType.choices, default=OwnerType.STUDENT)
    schedule    = models.CharField(max_length=10, choices=Schedule.choices, default=Schedule.MWF)
    created_at  = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.full_name} ({self.owner_type})"


class Vehicle(models.Model):
    class Type(models.TextChoices):
        CAR        = 'car',        'Car'
        MOTORCYCLE = 'motorcycle', 'Motorcycle'
        TRUCK      = 'truck',      'Truck'
        VAN        = 'van',        'Van'
        BUS        = 'bus',        'Bus'

    plate_number  = models.CharField(max_length=20, unique=True, db_index=True)
    vehicle_type  = models.CharField(max_length=20, choices=Type.choices, default=Type.CAR)
    model         = models.CharField(max_length=100, blank=True)
    color         = models.CharField(max_length=50, blank=True)
    is_authorized = models.BooleanField(default=False)
    owner         = models.ForeignKey(Owner, on_delete=models.SET_NULL, null=True, related_name='vehicles')
    created_at    = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.plate_number


import uuid
from django.utils import timezone

class RegistrationToken(models.Model):
    token           = models.UUIDField(default=uuid.uuid4, unique=True)
    registrant_type = models.CharField(max_length=20, choices=[('student','Student'),('employee','Employee')])
    is_used         = models.BooleanField(default=False)
    is_active       = models.BooleanField(default=True)   # admin can disable
    expires_at      = models.DateTimeField()  # required expiration
    created_at      = models.DateTimeField(auto_now_add=True)

    @property
    def is_valid(self):
        if not self.is_active or self.is_used:
            return False
        if self.expires_at and timezone.now() > self.expires_at:
            return False
        return True

    def __str__(self):
        return f"{self.registrant_type} Token ({self.token}) - Valid: {self.is_valid}"


class VehicleRegistration(models.Model):
    class Status(models.TextChoices):
        PENDING  = 'pending',  'Pending'
        ACCEPTED = 'accepted', 'Accepted'
        REJECTED = 'rejected', 'Rejected'

    class RegistrantType(models.TextChoices):
        STUDENT  = 'student',  'Student'
        EMPLOYEE = 'employee', 'Employee'

    # Common fields
    registrant_type = models.CharField(max_length=20, choices=RegistrantType.choices)
    full_name       = models.CharField(max_length=255)
    email           = models.EmailField()
    address         = models.TextField(blank=True)
    contact_number  = models.CharField(max_length=100, blank=True)
    age             = models.PositiveIntegerField(null=True, blank=True)
    drivers_license = models.CharField(max_length=100, blank=True)
    campus_days     = models.JSONField(default=list)  # e.g. ["Monday","Wednesday","Friday"]

    # Student-specific
    student_id      = models.CharField(max_length=50, blank=True)
    program_year    = models.CharField(max_length=100, blank=True)

    # Employee-specific
    employee_id     = models.CharField(max_length=50, blank=True)
    department      = models.CharField(max_length=100, blank=True)

    # Vehicle fields
    plate_number      = models.CharField(max_length=20)
    conduction_number = models.CharField(max_length=50, blank=True)
    vehicle_type      = models.CharField(max_length=50)
    vehicle_color     = models.CharField(max_length=50, blank=True)
    body_number       = models.CharField(max_length=50, blank=True)

    # Status
    status          = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    rejection_reason = models.TextField(blank=True)
    token           = models.UUIDField(unique=True)  # the registration invite token
    
    created_at      = models.DateTimeField(auto_now_add=True)
    reviewed_at     = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.full_name} - {self.plate_number} ({self.status})"
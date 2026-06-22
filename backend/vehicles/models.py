from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator

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
        ALL  = 'ALL',  'All Days'              # for visitors

    full_name   = models.CharField(max_length=255)
    contact     = models.CharField(max_length=50, blank=True)
    address     = models.TextField(blank=True)
    photo       = models.ImageField(upload_to='owners/', blank=True)
    owner_type  = models.CharField(max_length=20, choices=OwnerType.choices, default=OwnerType.STUDENT)
    schedule    = models.CharField(max_length=10, choices=Schedule.choices, default=Schedule.MWF)
    user_code   = models.CharField(max_length=20, blank=True, db_index=True,
                                    help_text="SLC user code (e.g., SLC-OWN-000001) linking to accounts.User")
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
    registrant_type = models.CharField(max_length=20, choices=[('student','Student'),('employee','Employee'),('fetcher','Fetcher/Drop&Go')])
    is_used         = models.BooleanField(default=False)
    is_active       = models.BooleanField(default=True)   # admin can disable
    expires_at      = models.DateTimeField()  # required expiration
    created_at      = models.DateTimeField(auto_now_add=True)

    @property
    def is_valid(self):
        from django.utils import timezone as tz
        if not self.is_active or self.is_used:
            return False
        if self.expires_at:
            expires = self.expires_at
            if tz.is_naive(expires):
                expires = tz.make_aware(expires)
            if tz.now() > expires:
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
        FETCHER  = 'fetcher',  'Fetcher/Drop&Go'

    class Schedule(models.TextChoices):
        MWF  = 'MWF',  'Monday-Wednesday-Friday'
        TTHS = 'TTHS', 'Tuesday-Thursday-Saturday'
        ANY  = 'ANY',  'Any Day'

    # Link to the created User account (populated on acceptance)
    user = models.ForeignKey(
        'accounts.User',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='registrations',
    )

    # Common fields
    registrant_type = models.CharField(max_length=20, choices=RegistrantType.choices)
    full_name       = models.CharField(max_length=255)
    email           = models.EmailField(db_index=True)
    address         = models.TextField(blank=True)
    contact_number  = models.CharField(max_length=100, blank=True)
    age             = models.PositiveIntegerField(null=True, blank=True)
    drivers_license = models.CharField(max_length=100, blank=True)
    campus_days     = models.JSONField(default=list)  # e.g. ["Monday","Wednesday","Friday"]
    schedule        = models.CharField(max_length=10, choices=Schedule.choices, blank=True)

    # Student-specific
    student_id      = models.CharField(max_length=50, blank=True)
    program_year    = models.CharField(max_length=100, blank=True)

    # Employee-specific
    employee_id     = models.CharField(max_length=50, blank=True)
    department      = models.CharField(max_length=100, blank=True)

    # Vehicle fields
    plate_number      = models.CharField(max_length=20, db_index=True)
    conduction_number = models.CharField(max_length=50, blank=True)
    vehicle_type      = models.CharField(max_length=50)
    vehicle_color     = models.CharField(max_length=50, blank=True)
    body_number       = models.CharField(max_length=50, blank=True)

    # Status & admin review
    status           = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    rejection_reason = models.TextField(blank=True)
    or_number        = models.CharField(max_length=100, blank=True, help_text="Official Receipt number from Accounting Office")
    token            = models.UUIDField(unique=True, null=True, blank=True)  # null for direct (non-token) registrations

    # Auto-assigned unique system IDs (populated on acceptance)
    system_student_id  = models.CharField(max_length=30, blank=True, unique=True, null=True)
    system_employee_id = models.CharField(max_length=30, blank=True, unique=True, null=True)

    created_at      = models.DateTimeField(auto_now_add=True)
    reviewed_at     = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.full_name} - {self.plate_number} ({self.status})"


class RuleConstraint(models.Model):
    class ConstraintType(models.TextChoices):
        STUDENT_VEHICLE = 'student_vehicle', 'Student — Vehicle'
        STUDENT_EBIKE   = 'student_ebike',   'Student — E-Bike'
        EMPLOYEE        = 'employee',         'Employee'
        FETCHER         = 'fetcher',          'Fetcher / Drop & Go'

    name        = models.CharField(max_length=120)
    constraint_type = models.CharField(max_length=20, choices=ConstraintType.choices)
    days        = models.JSONField(default=list)   # e.g. ["mon","tue","wed","thu","fri","sat"]
    start_time  = models.CharField(max_length=5, default='06:00')   # HH:MM
    end_time    = models.CharField(max_length=5, default='20:00')   # HH:MM
    enabled     = models.BooleanField(default=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['constraint_type', 'name']

    def __str__(self):
        return f"{self.name} ({self.constraint_type})"


class VehicleTypeAccess(models.Model):
    class Status(models.TextChoices):
        ALLOWED = 'allowed', 'Allowed'
        RESTRICTED = 'restricted', 'Restricted Hours'

    VEHICLE_CATEGORIES = [
        ('four-wheel', '4-Wheel Vehicles'),
        ('three-wheel', '3-Wheel Vehicles'),
        ('two-wheel', '2-Wheel Vehicles'),
        ('ebike', 'E-Bike'),
        ('escooter', 'E-Scooter'),
        ('heavy', 'Heavy Vehicles'),
    ]

    ICON_CHOICES = [
        ('Car', 'Car'),
        ('Bike', 'Bike'),
        ('Truck', 'Truck'),
        ('Zap', 'Zap'),
    ]

    GATE_CHOICES = [
        ('Main Gate 1', 'Main Gate 1'),
        ('Main Gate 2', 'Main Gate 2'),
    ]

    category_key = models.CharField(max_length=30, choices=VEHICLE_CATEGORIES, unique=True)
    label = models.CharField(max_length=100)
    sub = models.CharField(max_length=200, blank=True)
    icon = models.CharField(max_length=20, choices=ICON_CHOICES, default='Car')
    gate = models.CharField(max_length=30, choices=GATE_CHOICES, default='Main Gate 1')
    is_all_hours = models.BooleanField(default=True)
    hours_start = models.CharField(max_length=5, default='06:00')   # HH:MM
    hours_end = models.CharField(max_length=5, default='20:00')     # HH:MM
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ALLOWED)
    enabled = models.BooleanField(default=True)
    ordering = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['ordering', 'label']

    def __str__(self):
        return f"{self.label} ({self.get_status_display()})"


class ParkingZone(models.Model):
    """A named camera view / parking area. Spaces are drawn on top of its reference image."""
    class VehicleCategory(models.TextChoices):
        MOTORCYCLE = 'motorcycle', 'Motorcycle'
        CAR        = 'car',        'Car'

    name             = models.CharField(max_length=100)
    vehicle_category = models.CharField(max_length=20, choices=VehicleCategory.choices)
    reference_image  = models.ImageField(upload_to='parking_zones/', blank=True, null=True)
    created_at       = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['vehicle_category', 'name']

    def __str__(self):
        return f"{self.name} ({self.get_vehicle_category_display()})"


class ParkingSpace(models.Model):
    class VehicleCategory(models.TextChoices):
        MOTORCYCLE = 'motorcycle', 'Motorcycle'
        CAR        = 'car',        'Car'

    zone             = models.ForeignKey(
        ParkingZone, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='spaces',
    )
    space_number     = models.CharField(max_length=20)
    vehicle_category = models.CharField(max_length=20, choices=VehicleCategory.choices)
    # Bounding box in 0.0–1.0 normalised coords of the zone reference image
    x1               = models.FloatField(null=True, blank=True)
    y1               = models.FloatField(null=True, blank=True)
    x2               = models.FloatField(null=True, blank=True)
    y2               = models.FloatField(null=True, blank=True)
    is_occupied      = models.BooleanField(default=False)
    occupied_by      = models.CharField(max_length=20, blank=True, help_text="Plate number of occupying vehicle")
    updated_at       = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['vehicle_category', 'space_number']

    def __str__(self):
        status = f"({self.occupied_by})" if self.is_occupied else "(free)"
        return f"{self.get_vehicle_category_display()} Space {self.space_number} {status}"


class Department(models.Model):
    name      = models.CharField(max_length=200, unique=True)
    is_active = models.BooleanField(default=True)
    order     = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order', 'name']

    def __str__(self):
        return self.name


class Program(models.Model):
    name      = models.CharField(max_length=200, unique=True)
    is_active = models.BooleanField(default=True)
    order     = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order', 'name']

    def __str__(self):
        return self.name
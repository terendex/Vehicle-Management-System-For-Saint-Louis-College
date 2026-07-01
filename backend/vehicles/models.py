from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator


class ReferenceItem(models.Model):
    class Category(models.TextChoices):
        DEPARTMENT = 'department', 'Department'
        PROGRAM    = 'program',    'Program'

    category  = models.CharField(max_length=20, choices=Category.choices)
    name      = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)
    order     = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = [('category', 'name')]
        ordering = ['category', 'order', 'name']

    def __str__(self):
        return f"{self.name} ({self.category})"


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
    user          = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='vehicles',
    )
    created_at    = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.plate_number


import uuid
from django.utils import timezone

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
        MWF   = 'MWF',   'Monday-Wednesday-Friday'
        TTHS  = 'TTHS',  'Tuesday-Thursday-Saturday'
        MIXED = 'MIXED', 'Mixed / Custom Days'
        ANY   = 'ANY',   'Any Day'

    class Source(models.TextChoices):
        PUBLIC = 'public', 'Online/Public Form'
        DIRECT = 'direct', 'CDSO Walk-in'

    class DepartmentType(models.TextChoices):
        TEACHING     = 'teaching',     'Teaching'
        NON_TEACHING = 'non_teaching', 'Non-Teaching'

    user = models.ForeignKey(
        'accounts.User',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='registrations',
    )
    vehicle = models.ForeignKey(
        'Vehicle',
        null=True, blank=True,
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
    campus_days     = models.JSONField(default=list)
    schedule        = models.CharField(max_length=10, choices=Schedule.choices, blank=True)

    # Student-specific
    student_id   = models.CharField(max_length=50, blank=True)
    program_year = models.CharField(max_length=100, blank=True)
    program      = models.ForeignKey(
        ReferenceItem, null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='registrations',
        limit_choices_to={'category': 'program'},
    )

    # Employee-specific
    employee_id     = models.CharField(max_length=50, blank=True)
    department      = models.ForeignKey(
        ReferenceItem, null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='employee_registrations',
        limit_choices_to={'category': 'department'},
    )
    department_type = models.CharField(
        max_length=20, choices=DepartmentType.choices, null=True, blank=True,
    )

    # Vehicle fields
    plate_number      = models.CharField(max_length=20, db_index=True)
    conduction_number = models.CharField(max_length=50, blank=True)
    vehicle_type      = models.CharField(max_length=50)
    vehicle_color     = models.CharField(max_length=50, blank=True)
    body_number       = models.CharField(max_length=50, blank=True)

    # Status & admin review
    status           = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    rejection_reason = models.TextField(blank=True)
    or_number        = models.CharField(max_length=100, blank=True)
    source           = models.CharField(max_length=20, choices=Source.choices, default=Source.PUBLIC)

    # Special case — set when admin grants days beyond the original request
    is_special_case      = models.BooleanField(default=False)
    special_case_reason  = models.TextField(blank=True)

    # Auto-assigned unique system IDs (populated on acceptance)
    system_student_id  = models.CharField(max_length=30, blank=True, unique=True, null=True)
    system_employee_id = models.CharField(max_length=30, blank=True, unique=True, null=True)

    created_at  = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.full_name} - {self.plate_number} ({self.status})"


class RuleConstraint(models.Model):
    class ConstraintType(models.TextChoices):
        STUDENT_VEHICLE = 'student_vehicle', 'Student — Vehicle'
        EMPLOYEE        = 'employee',         'Employee'
        FETCHER         = 'fetcher',          'Fetcher / Drop & Go'

    name            = models.CharField(max_length=120)
    constraint_type = models.CharField(max_length=20, choices=ConstraintType.choices)
    days            = models.JSONField(default=list)
    start_time      = models.CharField(max_length=5, default='06:00')
    end_time        = models.CharField(max_length=5, default='20:00')
    enabled         = models.BooleanField(default=True)
    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['constraint_type', 'name']

    def __str__(self):
        return f"{self.name} ({self.constraint_type})"


class ParkingZone(models.Model):
    class VehicleCategory(models.TextChoices):
        MOTORCYCLE = 'motorcycle', 'Motorcycle'
        CAR        = 'car',        'Car'

    name              = models.CharField(max_length=100)
    vehicle_category  = models.CharField(max_length=20, choices=VehicleCategory.choices)
    reference_image   = models.ImageField(upload_to='parking_zones/', blank=True, null=True)
    rtsp_url          = models.CharField(max_length=500, blank=True)
    capacity_override = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Event-mode capacity override. If set, overrides the mapped space count as the effective capacity.",
    )
    created_at        = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['vehicle_category', 'name']

    def __str__(self):
        return f"{self.name} ({self.get_vehicle_category_display()})"


class SystemSettings(models.Model):
    """Singleton row (pk=1) for CDSO/admin-configurable system-wide parameters."""
    retention_years      = models.IntegerField(
        default=5,
        validators=[MinValueValidator(1), MaxValueValidator(10)],
    )
    scan_dedup_seconds   = models.IntegerField(
        default=30,
        validators=[MinValueValidator(5), MaxValueValidator(300)],
    )
    event_mode_parking   = models.BooleanField(
        default=False,
        help_text="When enabled, guards can override full-parking restrictions.",
    )
    event_mode_entry     = models.BooleanField(
        default=False,
        help_text="When enabled, guards can override denied entry scans at the gate.",
    )
    registration_start   = models.DateField(
        null=True, blank=True,
        help_text="First day vehicle registrations are accepted.",
    )
    registration_end     = models.DateField(
        null=True, blank=True,
        help_text="Last day vehicle registrations are accepted.",
    )
    open_campus_mode     = models.BooleanField(
        default=False,
        help_text="When enabled, all vehicles are allowed entry regardless of registration or schedule rules.",
    )

    class Meta:
        verbose_name        = "System Settings"
        verbose_name_plural = "System Settings"

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return "System Settings"


class RegistrationPeriod(models.Model):
    """One row per registration window. Only one row may be active at a time."""
    label      = models.CharField(max_length=150)
    start_date = models.DateField()
    end_date   = models.DateField()
    is_active  = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    @classmethod
    def get_active(cls):
        return cls.objects.filter(is_active=True).first()

    def __str__(self):
        status = 'Active' if self.is_active else 'Archived'
        return f"{self.label} ({status})"


class Event(models.Model):
    """A campus event. Organizer plates are noted temporarily; activating closes parts of parking."""
    name             = models.CharField(max_length=200)
    date             = models.DateField()
    is_active        = models.BooleanField(default=False)
    organizer_plates = models.JSONField(default=list, blank=True)
    created_by       = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='events_created',
    )
    created_at       = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-created_at']

    def __str__(self):
        return self.name


class ParkingNotice(models.Model):
    """Admin/CDSO-authored broadcast message sent to all vehicle owners by email and shown in their portal."""
    title      = models.CharField(max_length=200)
    body       = models.TextField()
    is_active  = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='parking_notices',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title


class ParkingSpace(models.Model):
    zone         = models.ForeignKey(
        ParkingZone, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='spaces',
    )
    space_number = models.CharField(max_length=20)
    x1           = models.FloatField(null=True, blank=True)
    y1           = models.FloatField(null=True, blank=True)
    x2           = models.FloatField(null=True, blank=True)
    y2           = models.FloatField(null=True, blank=True)
    is_occupied  = models.BooleanField(default=False)
    occupied_by  = models.CharField(max_length=20, blank=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['zone__vehicle_category', 'space_number']

    def __str__(self):
        status = f"({self.occupied_by})" if self.is_occupied else "(free)"
        cat = self.zone.get_vehicle_category_display() if self.zone else '?'
        return f"{cat} Space {self.space_number} {status}"


class Camera(models.Model):
    class Assignment(models.TextChoices):
        ENTRY   = 'entry',   'Entry'
        PARKING = 'parking', 'Parking'

    class GateId(models.TextChoices):
        GATE1 = 'gate1', 'Gate 1'
        GATE4 = 'gate4', 'Gate 4'

    cam_number = models.PositiveIntegerField(unique=True)
    name       = models.CharField(max_length=50)
    ip         = models.CharField(max_length=100)
    device_id  = models.CharField(max_length=100)
    password   = models.CharField(max_length=100)
    rtsp_url   = models.CharField(max_length=500)
    assignment = models.CharField(max_length=20, choices=Assignment.choices)
    gate_id    = models.CharField(max_length=10, choices=GateId.choices, null=True, blank=True,
                                  help_text='Required when assignment is Entry. Identifies which gate this camera covers.')
    is_active  = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['cam_number']

    def __str__(self):
        gate = f' — {self.get_gate_id_display()}' if self.gate_id else ''
        return f"{self.name} ({self.get_assignment_display()}{gate})"

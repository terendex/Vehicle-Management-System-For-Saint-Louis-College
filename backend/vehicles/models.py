from decimal import Decimal

from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.db.models import Value
from django.db.models.functions import Lower, Replace, Upper
from django.core.validators import MinValueValidator, MaxValueValidator


class ReferenceItem(models.Model):
    class Category(models.TextChoices):
        DEPARTMENT = 'department', 'Department'
        PROGRAM    = 'program',    'Program'

    id        = models.BigAutoField(primary_key=True, db_column='reference_item_id')
    category  = models.CharField(max_length=20, choices=Category.choices)
    name      = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)
    order     = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'tbl_reference_item'
        unique_together = [('category', 'name')]
        ordering = ['category', 'order', 'name']

    def __str__(self):
        return f"{self.name} ({self.category})"


class Vehicle(models.Model):
    class Type(models.TextChoices):
        CAR        = 'car',        'Car'
        MOTORCYCLE = 'motorcycle', 'Motorcycle'
        EBIKE      = 'ebike',      'E-Bike'
        TRUCK      = 'truck',      'Truck'
        VAN        = 'van',        'Van'
        BUS        = 'bus',        'Bus'

    id            = models.BigAutoField(primary_key=True, db_column='vehicle_id')
    # A vehicle is identified by EITHER a real plate OR a conduction sticker
    # (brand-new car with no plate yet), never both. Both are blank-not-null so a
    # conduction-only car can exist; uniqueness among non-blank values is enforced
    # by the partial constraints below (blank '' is exempt, like plate_number was).
    plate_number      = models.CharField(max_length=20, blank=True, default='', db_index=True)
    conduction_number = models.CharField(max_length=50, blank=True, default='', db_index=True)
    vehicle_type  = models.CharField(max_length=20, choices=Type.choices, default=Type.CAR)
    model         = models.CharField(max_length=100, blank=True)
    color         = models.CharField(max_length=50, blank=True)
    is_authorized = models.BooleanField(default=False)
    user          = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='vehicles',
    )
    created_at    = models.DateTimeField(auto_now_add=True)

    @property
    def identifier(self) -> str:
        """The vehicle's active identifier for display — plate if it has one,
        otherwise the conduction number."""
        return self.plate_number or self.conduction_number

    @classmethod
    def resolve(cls, identifier: str):
        """Find a vehicle by a scanned/typed identifier — plate first, then
        conduction number. Both are normalized (upper, no spaces) the same way.
        Returns the Vehicle or None."""
        norm = _normalize_plate(identifier)
        if not norm:
            return None
        return (cls.objects.select_related('user').filter(plate_number=norm).first()
                or cls.objects.select_related('user').filter(conduction_number=norm).first())

    def __str__(self):
        return self.identifier or f"Vehicle {self.pk}"

    class Meta:
        db_table = 'tbl_vehicle'
        constraints = [
            # Uniqueness among real values only; multiple blanks are allowed so a
            # conduction-only car (blank plate) and vice-versa don't collide.
            models.UniqueConstraint(
                fields=['plate_number'], condition=~models.Q(plate_number=''),
                name='uniq_vehicle_plate_number',
            ),
            models.UniqueConstraint(
                fields=['conduction_number'], condition=~models.Q(conduction_number=''),
                name='uniq_vehicle_conduction_number',
            ),
        ]


import uuid
from django.utils import timezone

def _normalize_plate(value):
    """Canonical plate form used for uniqueness: upper-cased, no spaces."""
    return (value or '').strip().upper().replace(' ', '')


def _normalize_email(value):
    return (value or '').strip().lower()


class VehicleRegistration(models.Model):
    class Status(models.TextChoices):
        PENDING  = 'pending',  'Pending'
        ACCEPTED = 'accepted', 'Accepted'
        REJECTED = 'rejected', 'Rejected'
        # Set when the owning account auto-archives on expiry. Excluded from the
        # active (pending/accepted) uniqueness constraints below, so it releases
        # the plate/email/ID/license for the person to register again.
        EXPIRED  = 'expired',  'Expired'

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
        TEACHING          = 'teaching',          'Teaching'
        NON_TEACHING      = 'non_teaching',      'Non-Teaching'
        # One department, not two — Cleaning and Services is a single unit.
        CLEANING_SERVICES = 'cleaning_services', 'Cleaning and Services'

    # Departments whose staff pay nothing for a vehicle pass. Kept next to the
    # choices so adding a department forces a decision about its fee rather
    # than silently inheriting the employee rate.
    #
    # The exemption is deliberately NOT advertised in the registration form's
    # department picker: seeing "free" next to an option invites people who are
    # not in that department to select it, which is a false registration the
    # CDSO then has to unpick. Applicants are told after submitting.
    FEE_EXEMPT_DEPARTMENTS = frozenset({'cleaning_services'})

    class StudentLevel(models.TextChoices):
        COLLEGE    = 'college',    'College'
        SHS        = 'shs',        'Senior High School'
        JHS        = 'jhs',        'Junior High School'
        ELEMENTARY = 'elementary', 'Elementary'
        SPED       = 'sped',       'Special Education'

    class DriverRelationship(models.TextChoices):
        PARENT            = 'parent',            'Parent'
        GUARDIAN          = 'guardian',          'Guardian'
        AUTHORIZED_DRIVER = 'authorized_driver', 'Authorized Driver'

    class FetcherType(models.TextChoices):
        DROP_AND_GO = 'drop_and_go', 'Fetcher / Drop & Go'
        STANDBY     = 'standby',     'Standby'

    id = models.BigAutoField(primary_key=True, db_column='vehicle_registration_id')
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
    drivers_license_image = models.ImageField(upload_to='licenses/', null=True, blank=True)
    campus_days     = models.JSONField(default=list)
    schedule        = models.CharField(max_length=10, choices=Schedule.choices, blank=True)

    # Student-specific
    student_id    = models.CharField(max_length=50, blank=True)
    student_level = models.CharField(max_length=20, choices=StudentLevel.choices, blank=True)
    program_year  = models.CharField(max_length=100, blank=True)

    # Authorized driver — filled when the registrant is not the one driving
    # (JHS/Elementary are always minors; some SpEd students cannot drive).
    # When set, drivers_license holds THIS person's license, not the student's.
    driver_name         = models.CharField(max_length=255, blank=True)
    driver_relationship = models.CharField(
        max_length=30, choices=DriverRelationship.choices, blank=True,
    )
    driver_contact      = models.CharField(max_length=100, blank=True)
    program      = models.ForeignKey(
        ReferenceItem, null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='registrations',
        limit_choices_to={'category': 'program'},
    )

    # Fetcher-specific — classification plus the students being fetched.
    # drop_and_go: entry only during the allotted drop-off/pick-up windows.
    # standby:     allowed to park inside campus while waiting.
    fetcher_type     = models.CharField(max_length=20, choices=FetcherType.choices, blank=True)
    # [{full_name, student_id, student_level, program_year}, ...] — at least one
    # entry is required for fetcher registrations (validated in the views).
    fetcher_students = models.JSONField(default=list, blank=True)

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

    # Vehicle fields — a registration carries EITHER a plate OR a conduction
    # number (brand-new car), never both; enforced in the views. plate_number is
    # blank-able so conduction-only registrations are valid.
    plate_number      = models.CharField(max_length=20, blank=True, default='', db_index=True)
    conduction_number = models.CharField(max_length=50, blank=True, default='', db_index=True)
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

    def save(self, *args, **kwargs):
        # Canonicalize so both the application-layer conflict checks and the
        # DB unique constraints below compare like-for-like values.
        self.plate_number = _normalize_plate(self.plate_number)
        self.conduction_number = _normalize_plate(self.conduction_number)
        self.email = _normalize_email(self.email)
        self.student_id = (self.student_id or '').strip()
        self.employee_id = (self.employee_id or '').strip()
        self.drivers_license = (self.drivers_license or '').strip().upper()
        super().save(*args, **kwargs)

    def pass_fee(self, settings_obj=None) -> Decimal:
        """What this applicant owes for their vehicle pass.

        Single source of truth for the amount. The figure used to be worked out
        in the React form alone, which meant the price a person was told and the
        price the system believed were two separate implementations that could
        drift apart.

        Services and Cleaning staff pay nothing — they are exempt outright, not
        discounted, so this returns 0 regardless of the configured employee rate.
        """
        if settings_obj is None:
            settings_obj = SystemSettings.get()

        if self.registrant_type == 'employee':
            if (self.department_type or '') in self.FEE_EXEMPT_DEPARTMENTS:
                return Decimal('0.00')
            return settings_obj.vehicle_pass_fee_employee
        return settings_obj.vehicle_pass_fee

    def __str__(self):
        return f"{self.full_name} - {self.plate_number} ({self.status})"

    class Meta:
        db_table = 'tbl_vehicle_registration'
        indexes = [
            models.Index(fields=['-created_at'], name='vehreg_created_at'),
            models.Index(fields=['status', '-created_at'], name='vehreg_status_time'),
            models.Index(fields=['registrant_type'], name='vehreg_registrant_type'),
            # campus_days is JSON; the dashboard asks `campus_days__contains=[day]`
            # once per weekday. Only a GIN index can answer containment.
            GinIndex(fields=['campus_days'], name='vehreg_campus_days_gin'),

            # Duplicate checking compares the *normalised* plate/email, because
            # rows predating normalisation may carry stray spacing or case. That
            # comparison used to happen in Python over every active registration
            # — an O(N) fetch on every submission. These expression indexes let
            # Postgres answer the same question with an index lookup, so the
            # check costs the same at 10 rows and 10,000.
            models.Index(
                Upper(Replace('plate_number', Value(' '), Value(''))),
                name='vehreg_plate_norm',
            ),
            models.Index(Lower('email'), name='vehreg_email_norm'),
        ]
        # A plate and an email may each belong to at most ONE active
        # (pending/accepted) registration — enforcing a 1:1 email↔plate pairing
        # at the database level. Rejected registrations are exempt so a
        # previously declined plate/email can be re-submitted.
        constraints = [
            # plate/conduction are mutually exclusive per row and each blank when
            # unused, so blanks are excluded — only a *provided* value must be
            # unique among active registrations.
            models.UniqueConstraint(
                fields=['plate_number'],
                condition=models.Q(status__in=['pending', 'accepted']) & ~models.Q(plate_number=''),
                name='uniq_active_registration_plate',
            ),
            models.UniqueConstraint(
                fields=['conduction_number'],
                condition=models.Q(status__in=['pending', 'accepted']) & ~models.Q(conduction_number=''),
                name='uniq_active_registration_conduction',
            ),
            models.UniqueConstraint(
                fields=['email'],
                condition=models.Q(status__in=['pending', 'accepted']),
                name='uniq_active_registration_email',
            ),
            # student ID / employee ID / driver's license are optional per row
            # (blank for other registrant types), so blanks are excluded — only
            # a *provided* value must be unique among active registrations.
            models.UniqueConstraint(
                fields=['student_id'],
                condition=models.Q(status__in=['pending', 'accepted']) & ~models.Q(student_id=''),
                name='uniq_active_registration_student_id',
            ),
            models.UniqueConstraint(
                fields=['employee_id'],
                condition=models.Q(status__in=['pending', 'accepted']) & ~models.Q(employee_id=''),
                name='uniq_active_registration_employee_id',
            ),
            models.UniqueConstraint(
                fields=['drivers_license'],
                condition=models.Q(status__in=['pending', 'accepted']) & ~models.Q(drivers_license=''),
                name='uniq_active_registration_drivers_license',
            ),
        ]


class RuleConstraint(models.Model):
    class ConstraintType(models.TextChoices):
        STUDENT_VEHICLE = 'student_vehicle', 'Student — Vehicle'
        EMPLOYEE        = 'employee',         'Employee'
        FETCHER         = 'fetcher',          'Fetcher / Drop & Go'
        SUPPLIER        = 'supplier',         'Supplier'

    id              = models.BigAutoField(primary_key=True, db_column='rule_constraint_id')
    name            = models.CharField(max_length=120)
    constraint_type = models.CharField(max_length=20, choices=ConstraintType.choices)
    days            = models.JSONField(default=list)
    start_time      = models.CharField(max_length=5, default='06:00')
    end_time        = models.CharField(max_length=5, default='20:00')
    max_stay_minutes = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Maximum allowed stay in minutes. Exceeding it on exit auto-issues a "
                  "time-exceed violation. Blank = no stay limit.",
    )
    enabled         = models.BooleanField(default=True)
    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'tbl_rule_constraint'
        ordering = ['constraint_type', 'name']

    def __str__(self):
        return f"{self.name} ({self.constraint_type})"


class ParkingZone(models.Model):
    class VehicleCategory(models.TextChoices):
        MOTORCYCLE = 'motorcycle', 'Motorcycle'
        CAR        = 'car',        'Car'

    id                = models.BigAutoField(primary_key=True, db_column='parking_zone_id')
    name              = models.CharField(max_length=100)
    vehicle_category  = models.CharField(max_length=20, choices=VehicleCategory.choices)
    reference_image   = models.ImageField(upload_to='parking_zones/', blank=True, null=True)
    camera            = models.ForeignKey(
        'Camera', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='parking_zones',
        help_text="Physical camera (registered in Device Management) that watches this zone.",
    )
    capacity_override = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Event-mode capacity override. If set, overrides the mapped space count as the effective capacity.",
    )
    created_at        = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tbl_parking_zone'
        ordering = ['vehicle_category', 'name']

    def __str__(self):
        return f"{self.name} ({self.get_vehicle_category_display()})"


class SystemSettings(models.Model):
    """Singleton row (pk=1) for CDSO/admin-configurable system-wide parameters."""
    id                   = models.BigAutoField(primary_key=True, db_column='system_settings_id')
    retention_years      = models.IntegerField(
        default=5,
        validators=[MinValueValidator(1), MaxValueValidator(10)],
    )
    scan_dedup_seconds   = models.IntegerField(
        default=60,
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
    vehicle_pass_fee          = models.DecimalField(
        max_digits=8, decimal_places=2, default=300,
        validators=[MinValueValidator(0)],
        help_text="Vehicle Pass registration fee (₱) for students and fetchers.",
    )
    vehicle_pass_fee_employee = models.DecimalField(
        max_digits=8, decimal_places=2, default=150,
        validators=[MinValueValidator(0)],
        help_text="Vehicle Pass registration fee (₱) for employees.",
    )
    # Vehicle-owner account expiration. When enabled, each owner account gets an
    # expires_at = creation date + (months, days), and the daily maintenance job
    # archives accounts once that date passes. Admin/security accounts never expire.
    account_expiry_enabled = models.BooleanField(
        default=False,
        help_text="When enabled, vehicle-owner accounts auto-archive after the set duration.",
    )
    account_expiry_months  = models.IntegerField(
        default=12,
        validators=[MinValueValidator(0), MaxValueValidator(120)],
        help_text="Months an owner account stays active after creation.",
    )
    account_expiry_days    = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(365)],
        help_text="Extra days (on top of months) before an owner account expires.",
    )

    class Meta:
        db_table = 'tbl_system_settings'
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
    id         = models.BigAutoField(primary_key=True, db_column='registration_period_id')
    label      = models.CharField(max_length=150)
    start_date = models.DateField()
    end_date   = models.DateField()
    is_active  = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tbl_registration_period'
        ordering = ['-created_at']

    @classmethod
    def get_active(cls):
        return cls.objects.filter(is_active=True).first()

    def __str__(self):
        status = 'Active' if self.is_active else 'Archived'
        return f"{self.label} ({status})"


class Event(models.Model):
    """A campus event. Organizer plates are noted temporarily; activating closes parts of parking."""
    id               = models.BigAutoField(primary_key=True, db_column='event_id')
    name             = models.CharField(max_length=200)
    date             = models.DateField()
    is_active        = models.BooleanField(default=False)
    archived         = models.BooleanField(default=False)
    organizer_plates = models.JSONField(default=list, blank=True)
    created_by       = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='events_created',
    )
    created_at       = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tbl_event'
        ordering = ['-date', '-created_at']

    def __str__(self):
        return self.name


class ParkingNotice(models.Model):
    """Admin/CDSO-authored broadcast message sent to all vehicle owners by email and shown in their portal."""
    id         = models.BigAutoField(primary_key=True, db_column='parking_notice_id')
    title      = models.CharField(max_length=200)
    body       = models.TextField()
    is_active  = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='parking_notices',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tbl_parking_notice'
        ordering = ['-created_at']

    def __str__(self):
        return self.title


class ParkingSpace(models.Model):
    id           = models.BigAutoField(primary_key=True, db_column='parking_space_id')
    zone         = models.ForeignKey(
        ParkingZone, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='spaces',
    )
    space_number = models.CharField(max_length=20)
    x1           = models.FloatField(null=True, blank=True)
    y1           = models.FloatField(null=True, blank=True)
    x2           = models.FloatField(null=True, blank=True)
    y2           = models.FloatField(null=True, blank=True)
    points       = models.JSONField(
        null=True, blank=True,
        help_text="Freeform polygon vertices [[x,y], ...] normalized 0-1 (pen tool). "
                   "x1..y2 still holds the bounding box for quick lookups.",
    )
    is_occupied  = models.BooleanField(default=False)
    occupied_by  = models.CharField(max_length=20, blank=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'tbl_parking_space'
        ordering = ['zone__vehicle_category', 'space_number']

    def __str__(self):
        status = f"({self.occupied_by})" if self.is_occupied else "(free)"
        cat = self.zone.get_vehicle_category_display() if self.zone else '?'
        return f"{cat} Space {self.space_number} {status}"


class Supplier(models.Model):
    """A supplier company whose vehicles are automatically permitted entry."""
    class Category(models.TextChoices):
        DELIVERY    = 'delivery',    'Delivery'
        MAINTENANCE = 'maintenance', 'Maintenance'
        VENDOR      = 'vendor',      'Vendor'
        CONTRACTOR  = 'contractor',  'Contractor'
        OTHER       = 'other',       'Other'

    id           = models.BigAutoField(primary_key=True, db_column='supplier_id')
    company_name = models.CharField(max_length=200, unique=True)
    category     = models.CharField(max_length=20, choices=Category.choices, default=Category.OTHER)
    is_active    = models.BooleanField(default=True)
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'tbl_supplier'
        ordering = ['company_name']

    def __str__(self):
        return self.company_name


class ScheduledVisit(models.Model):
    """A visitor or supplier visit coordinated ahead of time, so gate guards
    know who to expect on a given day before they show up."""
    class Category(models.TextChoices):
        DELIVERY    = 'delivery',    'Delivery'
        MAINTENANCE = 'maintenance', 'Maintenance'
        VENDOR      = 'vendor',      'Vendor'
        CONTRACTOR  = 'contractor',  'Contractor'
        GUEST       = 'guest',       'Guest / Visitor'
        OTHER       = 'other',       'Other'

    id            = models.BigAutoField(primary_key=True, db_column='scheduled_visit_id')
    visitor_name  = models.CharField(max_length=200)
    category      = models.CharField(max_length=20, choices=Category.choices, default=Category.OTHER)
    supplier      = models.ForeignKey(
        Supplier, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='scheduled_visits',
    )
    plate_number  = models.CharField(max_length=20, blank=True)
    purpose       = models.CharField(max_length=255, blank=True)
    expected_date = models.DateField()
    notes         = models.TextField(blank=True)
    is_arrived    = models.BooleanField(default=False)
    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['expected_date', 'visitor_name']

    def __str__(self):
        return f"{self.visitor_name} — {self.expected_date}"


class SupplierPlate(models.Model):
    """A license plate registered under a supplier."""
    id           = models.BigAutoField(primary_key=True, db_column='supplier_plate_id')
    supplier     = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name='plates')
    plate_number = models.CharField(max_length=20, unique=True, db_index=True)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tbl_supplier_plate'
        ordering = ['plate_number']

    def __str__(self):
        return f"{self.plate_number} ({self.supplier.company_name})"


class Camera(models.Model):
    class Assignment(models.TextChoices):
        ENTRY   = 'entry',   'Entry'
        PARKING = 'parking', 'Parking'

    class GateId(models.TextChoices):
        GATE1 = 'gate1', 'Gate 1'
        GATE4 = 'gate4', 'Gate 4'

    id         = models.BigAutoField(primary_key=True, db_column='camera_id')
    cam_number = models.PositiveIntegerField(unique=True)
    name       = models.CharField(max_length=50)
    ip         = models.CharField(max_length=100)
    device_id  = models.CharField(max_length=100)
    password   = models.CharField(max_length=100)
    rtsp_url   = models.CharField(max_length=500)
    assignment = models.CharField(max_length=20, choices=Assignment.choices)
    # Gate slug (e.g. 'gate1'). No choices constraint — gates are dynamic rows
    # in scanning.Gate so admins can add new ones from System Settings.
    gate_id    = models.CharField(max_length=10, null=True, blank=True,
                                  help_text='Required when assignment is Entry. Identifies which gate this camera covers.')
    is_active  = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'tbl_camera'
        ordering = ['cam_number']

    def __str__(self):
        gate = f' — {self.get_gate_id_display()}' if self.gate_id else ''
        return f"{self.name} ({self.get_assignment_display()}{gate})"

from decimal import Decimal

from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.db.models import Value
from django.db.models.functions import Lower, Replace, Upper
from django.core.validators import FileExtensionValidator, MinValueValidator, MaxValueValidator


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

    class PaymentStatus(models.TextChoices):
        """Whether the Vehicle Pass fee has been settled.

        Deliberately a second axis rather than more `Status` values. A
        registration can be rejected *after* the applicant already paid (a
        refund case), and a fee-exempt applicant is neither unpaid nor paid —
        neither fact fits in a single enum with pending/accepted, and the
        active-registration uniqueness constraints below key off `status`, so
        widening it would quietly release plates that must stay held.
        """
        UNPAID = 'unpaid', 'Unpaid'
        PAID   = 'paid',   'Paid'
        EXEMPT = 'exempt', 'Exempt'

    class RegistrantType(models.TextChoices):
        STUDENT  = 'student',  'Student'
        EMPLOYEE = 'employee', 'Employee'
        FETCHER  = 'fetcher',  'Fetcher/Drop&Go'

    class Schedule(models.TextChoices):
        MWF   = 'MWF',   'Monday-Wednesday-Friday'
        TTHF  = 'TTHF',  'Tuesday-Thursday-Friday'
        MIXED = 'MIXED', 'Mixed / Custom Days'
        # Spelled out rather than "Any Day": the campus is closed on Sunday, so
        # an unqualified "any day" overstates what the pass actually admits.
        ANY   = 'ANY',   'Any Campus Day (Monday-Saturday)'

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
    # Proof the applicant is genuinely enrolled/employed: the registrar's
    # assessment form. A FileField rather than an ImageField because students
    # usually attach the PDF the portal hands them, and only sometimes a photo
    # of the printed copy — the extension validator is what keeps the field
    # from accepting arbitrary uploads.
    assessment_form = models.FileField(
        upload_to='assessments/', null=True, blank=True,
        validators=[FileExtensionValidator(
            allowed_extensions=['jpg', 'jpeg', 'png', 'webp', 'heic', 'heif', 'pdf'],
        )],
    )
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

    # ── Payment ──
    # The applicant pays at the Accounting Office, then uploads the Official
    # Receipt themselves through the link in their pending email; CDSO verifies
    # the image against or_number at review time rather than re-keying it.
    payment_status   = models.CharField(
        max_length=20, choices=PaymentStatus.choices,
        default=PaymentStatus.UNPAID, db_index=True,
    )
    or_receipt_image = models.FileField(
        upload_to='receipts/', null=True, blank=True,
        validators=[FileExtensionValidator(
            allowed_extensions=['jpg', 'jpeg', 'png', 'webp', 'heic', 'heif', 'pdf'],
        )],
    )
    # Snapshot, not a lookup: vehicle_pass_fee is admin-configurable, so reading
    # the live setting would retroactively rewrite what past applicants paid the
    # moment the fee changes.
    amount_paid      = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    paid_at          = models.DateTimeField(null=True, blank=True)
    # Unguessable handle for the public receipt-upload page. The document upload
    # endpoint keys on (id, email), which stopped being much of a secret once
    # school emails became <8-digit ID>@slc-sflu.edu.ph against sequential ids.
    payment_token    = models.UUIDField(null=True, blank=True, unique=True, editable=False)
    # Set when CDSO approves a registration that is still unpaid. Required in
    # that case, so an issued pass with no receipt on file always says why.
    unpaid_accept_reason = models.TextField(blank=True)

    # Special case — set when admin grants days beyond the original request
    is_special_case      = models.BooleanField(default=False)
    special_case_reason  = models.TextField(blank=True)

    # Auto-assigned unique system IDs (populated on acceptance)
    system_student_id  = models.CharField(max_length=30, blank=True, unique=True, null=True)
    system_employee_id = models.CharField(max_length=30, blank=True, unique=True, null=True)

    created_at  = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    def save(self, *args, **kwargs):
        # Minted on first save and never rotated — the link in the pending email
        # has to keep working for as long as the registration is reviewable.
        if not self.payment_token:
            self.payment_token = uuid.uuid4()
            if kwargs.get('update_fields') is not None:
                kwargs['update_fields'] = list(kwargs['update_fields']) + ['payment_token']
        # Canonicalize so both the application-layer conflict checks and the
        # DB unique constraints below compare like-for-like values.
        self.plate_number = _normalize_plate(self.plate_number)
        self.conduction_number = _normalize_plate(self.conduction_number)
        self.email = _normalize_email(self.email)
        self.student_id = (self.student_id or '').strip()
        self.employee_id = (self.employee_id or '').strip()
        self.drivers_license = (self.drivers_license or '').strip().upper()
        super().save(*args, **kwargs)

    @classmethod
    def is_fee_exempt(cls, registrant_type, department_type='') -> bool:
        """Whether this applicant owes nothing at all for a vehicle pass.

        Answerable without a row and without touching the database, which is
        what the CDSO walk-in path needs: it decides whether to demand an
        Official Receipt number before there is a registration to ask.
        """
        return (registrant_type == 'employee'
                and (department_type or '') in cls.FEE_EXEMPT_DEPARTMENTS)

    @classmethod
    def fee_for(cls, registrant_type, department_type='', settings_obj=None) -> Decimal:
        """What an applicant of this type and department owes.

        Single source of truth for the amount. The figure used to be worked out
        in the React form alone, which meant the price a person was told and the
        price the system believed were two separate implementations that could
        drift apart.

        Services and Cleaning staff pay nothing — they are exempt outright, not
        discounted, so this returns 0 regardless of the configured employee rate.

        Pass `settings_obj` when the caller already holds one: every miss is a
        SystemSettings.get(), which is an uncached get_or_create round trip.
        """
        if cls.is_fee_exempt(registrant_type, department_type):
            return Decimal('0.00')
        if settings_obj is None:
            settings_obj = SystemSettings.get()
        if registrant_type == 'employee':
            return settings_obj.vehicle_pass_fee_employee
        return settings_obj.vehicle_pass_fee

    def pass_fee(self, settings_obj=None) -> Decimal:
        """What this applicant owes — see fee_for, which this delegates to."""
        return self.fee_for(self.registrant_type, self.department_type, settings_obj)

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


class FetcherStudentAssessment(models.Model):
    """The enrolment proof for one student named on a fetcher registration.

    A fetcher is not enrolled themselves, so their own application proves
    nothing about the students they collect — each listed student carries their
    own assessment form, the same document a student applicant attaches.

    Kept in its own table rather than inside VehicleRegistration.fetcher_students
    (a JSONField): a file needs real storage handling — extension validation, a
    signed URL for the reviewer, deletion when the row goes — and a JSON value
    gets none of that. student_index is the position in that list, so the two
    stay paired without the JSON having to hold anything but text.
    """
    id            = models.BigAutoField(primary_key=True, db_column='fetcher_student_assessment_id')
    registration  = models.ForeignKey(
        VehicleRegistration, on_delete=models.CASCADE,
        related_name='fetcher_assessments',
    )
    student_index = models.PositiveIntegerField()
    assessment_form = models.FileField(
        upload_to='assessments/fetcher/',
        validators=[FileExtensionValidator(
            allowed_extensions=['jpg', 'jpeg', 'png', 'webp', 'heic', 'heif', 'pdf'],
        )],
    )
    uploaded_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tbl_fetcher_student_assessment'
        ordering = ['student_index']
        # One document per listed student: a re-upload replaces what is on file
        # rather than leaving the reviewer two copies with no way to tell which
        # one the applicant meant.
        constraints = [
            models.UniqueConstraint(
                fields=['registration', 'student_index'],
                name='uniq_fetcher_assessment_per_student',
            ),
        ]

    def student(self):
        """The fetcher_students entry this document belongs to, or None."""
        students = self.registration.fetcher_students or []
        if 0 <= self.student_index < len(students):
            entry = students[self.student_index]
            return entry if isinstance(entry, dict) else None
        return None

    def student_name(self):
        entry = self.student() or {}
        return entry.get('full_name') or f'Student #{self.student_index + 1}'

    def __str__(self):
        return f"Assessment for {self.student_name()} (registration {self.registration_id})"


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

    class OccupancyMethod(models.TextChoices):
        ML      = 'ml',      'Vehicle detector (YOLO)'
        CLASSIC = 'classic', 'Baseline comparison (no ML)'

    id                = models.BigAutoField(primary_key=True, db_column='parking_zone_id')
    name              = models.CharField(max_length=100)
    vehicle_category  = models.CharField(max_length=20, choices=VehicleCategory.choices)
    # Which view of its camera this zone covers.
    #
    # A dual-lens unit stacks two unrelated scenes into one frame, so one camera
    # watches two places and each wants its own zone. Recording it here rather
    # than asking the editor every session is what makes the choice stick: the
    # bays were drawn against one of those scenes and are meaningless against
    # the other. 0 for every single-lens camera, so existing zones need no
    # backfill. Like ParkingSpace.lens_index this is a tag, not a coordinate
    # space — geometry stays normalised against the whole frame.
    lens_index        = models.PositiveSmallIntegerField(default=0)
    reference_image   = models.ImageField(upload_to='parking_zones/', blank=True, null=True)
    # The empty-lot reference the classic scorer measures against. Separate from
    # reference_image, which is the picture the admin draws bays on and may well
    # have cars in it.
    baseline_image       = models.ImageField(upload_to='parking_baselines/', blank=True, null=True)
    baseline_captured_at = models.DateTimeField(null=True, blank=True)
    occupancy_method     = models.CharField(
        max_length=20, choices=OccupancyMethod.choices, default=OccupancyMethod.ML,
        help_text="How this zone decides a bay is taken. 'classic' compares each bay "
                  "against an empty baseline and needs no detector; it falls back to "
                  "the detector until a baseline is captured.",
    )
    camera            = models.ForeignKey(
        'Camera', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='parking_zones',
        help_text="Physical camera (registered in Device Management) that watches this zone.",
    )
    # Whether this zone's detector should be running.
    #
    # Detection used to exist only as a button someone pressed, and it stayed
    # off until they did — so a zone drawn on a Friday watched nothing all
    # weekend, and every restart quietly switched every zone off again while the
    # screens went on showing bays free. It defaults on, and the supervisor in
    # detection_supervisor.py keeps a worker running for every zone that has it.
    # The Stop Detection button clears it, which is what makes a deliberate
    # pause survive both the supervisor and a restart.
    detection_enabled = models.BooleanField(
        default=True,
        help_text="Run this zone's camera detector automatically. Turn off to pause it.",
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
    # Vehicle-owner account expiration. Every owner account gets an
    # expires_at = creation date + (months, days), and the daily job archives
    # accounts once that date passes. Admin/security accounts never expire.
    #
    # Expiration cannot be switched off: the period may be shortened or extended,
    # but a zero period is rejected by the API. The flag is kept only so a
    # deployment can be frozen from the Django admin in an emergency; nothing in
    # the app can clear it, and the jobs treat False as "do nothing".
    account_expiry_enabled = models.BooleanField(
        default=True,
        help_text="Vehicle-owner accounts auto-archive after the set duration. "
                  "Not clearable from System Settings — the period is the control.",
    )
    account_expiry_months  = models.IntegerField(
        default=12,
        validators=[MinValueValidator(0), MaxValueValidator(120)],
        help_text="Months an owner account stays active after creation.",
    )
    account_expiry_days    = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(365)],
        help_text="Extra days (on top of months) before an owner account expires. "
                  "Months + days must total at least 1.",
    )
    # Parking dwell thresholds. The camera follows each vehicle's box and times
    # how long it has been still; these say how long "still" has to last before
    # the zone commits. They are here rather than hard-coded because the right
    # values depend on the lot — a busy aisle needs longer than a quiet bay.
    parked_after_seconds      = models.IntegerField(
        default=8,
        validators=[MinValueValidator(1), MaxValueValidator(120)],
        help_text="Seconds a vehicle must sit still before the camera counts it "
                  "as parked and claims the bays it covers.",
    )
    double_park_after_seconds = models.IntegerField(
        default=12,
        validators=[MinValueValidator(1), MaxValueValidator(300)],
        help_text="Seconds a vehicle must sit still across two or more bays "
                  "before it is reported as double parking. Cannot be shorter "
                  "than the parked threshold — a car cannot be badly parked "
                  "before it counts as parked at all.",
    )
    # Automatic backups. The frequency doubles as the on/off switch — "off" is a
    # real choice rather than a separate boolean, so there is no way to end up
    # with a schedule that is enabled but has no interval.
    #
    # Files land in BASE_DIR/backups alongside the pre-restore snapshots, and
    # `auto_backup_keep` rotates the automatic ones so a daily schedule cannot
    # fill the disk over a semester.
    auto_backup_frequency = models.CharField(
        max_length=10, default='off',
        choices=[
            ('off',     'Off'),
            ('hourly',  'Hourly'),
            ('daily',   'Daily'),
            ('weekly',  'Weekly'),
            ('monthly', 'Monthly'),
        ],
        help_text="How often the server takes a backup of system data by itself.",
    )
    auto_backup_keep = models.IntegerField(
        default=10,
        validators=[MinValueValidator(1), MaxValueValidator(90)],
        help_text="How many automatic backups to keep before the oldest is deleted. "
                  "Pre-restore snapshots are never rotated away.",
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


class DailyJobRun(models.Model):
    """One row per (job, day) — the ledger the in-process scheduler runs against.

    The unique constraint is the lock: a process claims a day's run by inserting
    the row, and whoever loses the race gets IntegrityError and skips. That makes
    the scheduler safe to start in every server process, and it means a restart
    loop cannot re-run a job that already ran today.

    It doubles as the catch-up record. The scheduler asks "did this run today?",
    not "is it 00:05 now?", so a campus machine that was switched off overnight
    runs the job when it next boots instead of skipping the day.
    """
    id         = models.BigAutoField(primary_key=True, db_column='daily_job_run_id')
    job        = models.CharField(max_length=64)
    run_date   = models.DateField()
    started_at = models.DateTimeField(auto_now_add=True)
    # Null while in flight; a row that stays null is a job that crashed midway.
    finished_at = models.DateTimeField(null=True, blank=True)
    result      = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        db_table = 'tbl_daily_job_run'
        ordering = ['-run_date', 'job']
        constraints = [
            models.UniqueConstraint(fields=['job', 'run_date'], name='uniq_daily_job_per_day'),
        ]

    def __str__(self):
        return f"{self.job} @ {self.run_date}"


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
    # Which view of a multi-lens camera this bay belongs to.
    #
    # A dual-lens unit stacks two unrelated scenes into one frame, so a camera
    # has two independent sets of bays. This tags which set a bay is in; it is
    # NOT a coordinate space. The geometry above stays normalised against the
    # WHOLE frame, because that is what the detector returns and what
    # `bay_occupancy._rect_for` reads — storing lens-local coordinates instead
    # would mean translating in two more places for no gain. 0 for every
    # ordinary single-lens camera, which is why it defaults to 0 and why
    # existing rows need no backfill.
    lens_index   = models.PositiveSmallIntegerField(default=0)
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
    # Optional, but usually needed: IMOU/Dahua units refuse RTSP without it.
    # Blank is allowed so a genuinely open camera can still be added without
    # inventing a credential for it.
    password   = models.CharField(max_length=100, blank=True, default='')
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

    @property
    def gate_label(self) -> str:
        """Human name for the gate, or the raw slug for gates added later.

        NOT get_gate_id_display(): Django only generates that for fields that
        declare `choices`, and gate_id deliberately has none because gates are
        dynamic rows in scanning.Gate. Calling it raised AttributeError for any
        camera with a gate set — which broke __str__, and with it every audited
        write. Deleting such a camera returned a 500 that the UI reported as a
        flat "Failed to remove camera."
        """
        if not self.gate_id:
            return ''
        if self.gate_id in self.GateId.values:
            return self.GateId(self.gate_id).label
        return self.gate_id

    def __str__(self):
        gate = f' — {self.gate_label}' if self.gate_id else ''
        return f"{self.name} ({self.get_assignment_display()}{gate})"

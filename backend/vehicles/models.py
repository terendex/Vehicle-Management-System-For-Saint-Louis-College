# =============================================================================
# WHAT THIS FILE IS FOR
#
# These are the database tables for vehicles, the paperwork behind them, and
# the campus settings that govern both. Roughly in the order they appear:
#
#   ReferenceItem            drop-down lists the CDSO maintains (departments, programs)
#   Vehicle                  one physical vehicle, identified by plate OR conduction number
#   VehicleRegistration      one application for a vehicle pass, and its review
#   FetcherStudentAssessment proof of enrolment for each student a fetcher collects
#   RegistrationChangeRequest an owner's proposed correction, awaiting CDSO
#   RuleConstraint           the days and hours each kind of entrant may enter
#   ParkingZone / ParkingSpace  the lots, and the individual bays drawn on camera views
#   SystemSettings           one row of campus-wide settings (fees, expiry, thresholds)
#   DailyJobRun              the ledger that stops a daily job running twice
#   RegistrationPeriod       the window during which applications are accepted
#   Event                    a campus event that can reserve parking
#   ParkingNotice            a broadcast message to all owners
#   Supplier / SupplierPlate / ScheduledVisit   expected non-owner traffic
#   Camera                   a physical camera and how to reach its video stream
#
# Recurring ideas worth knowing before reading:
#   * A vehicle carries EITHER a plate OR a conduction sticker (a brand-new car
#     with no plate yet), never both. Blank means "not this one".
#   * Identifiers are normalised (upper case, no spaces) so the same car always
#     compares equal however it was typed.
#   * "Partial" unique constraints apply a rule to some rows only — for example,
#     a plate must be unique among ACTIVE registrations, so a rejected one can
#     be applied for again.
# =============================================================================

import re                                     # for the control-number pattern below
from decimal import Decimal                   # money: exact amounts, no floating-point drift

from django.contrib.postgres.indexes import GinIndex   # index type that can search inside JSON
from django.db import models                  # field types, constraints and indexes
from django.db.models import Value            # a literal value used inside an index expression
from django.db.models.functions import Lower, Replace, Upper   # SQL text functions used by the indexes
from django.core.validators import FileExtensionValidator, MinValueValidator, MaxValueValidator


# The editable drop-down lists behind the registration form: the departments and
# programs the CDSO keeps up to date, rather than values hard-coded in the app.
class ReferenceItem(models.Model):
    class Category(models.TextChoices):
        DEPARTMENT = 'department', 'Department'
        PROGRAM    = 'program',    'Program'

    id        = models.BigAutoField(primary_key=True, db_column='reference_item_id')
    category  = models.CharField(max_length=20, choices=Category.choices)   # which list this belongs to
    name      = models.CharField(max_length=200)     # what appears in the drop-down
    is_active = models.BooleanField(default=True)    # retire an item without deleting past references to it
    order     = models.PositiveIntegerField(default=0)   # lets the CDSO put common choices first

    class Meta:
        db_table = 'tbl_reference_item'
        unique_together = [('category', 'name')]     # the same name may appear once per list
        ordering = ['category', 'order', 'name']     # grouped, then by chosen order, then alphabetical

    def __str__(self):
        return f"{self.name} ({self.category})"


# One physical vehicle. This is the row the gate matches a scanned plate
# against, so it stays deliberately small: who owns it, what it looks like, and
# whether it is currently allowed in.
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
    model         = models.CharField(max_length=100, blank=True)   # e.g. "Vios"; helps a guard confirm the car
    color         = models.CharField(max_length=50, blank=True)
    is_authorized = models.BooleanField(default=False)   # the gate's yes/no; set when a registration is accepted
    user          = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL, null=True, blank=True,   # keep the vehicle if the account goes
        related_name='vehicles',
    )
    created_at    = models.DateTimeField(auto_now_add=True)

    # Whichever identifier this vehicle actually has, for showing on screen.
    @property
    def identifier(self) -> str:
        """The vehicle's active identifier for display — plate if it has one,
        otherwise the conduction number."""
        return self.plate_number or self.conduction_number

    # Looks a vehicle up from whatever was scanned or typed at the gate.
    @classmethod
    def resolve(cls, identifier: str):
        """Find a vehicle by a scanned/typed identifier — plate first, then
        conduction number. Both are normalized (upper, no spaces) the same way.
        Returns the Vehicle or None."""
        norm = canonical_identifier(identifier)      # compare like-for-like with what is stored
        if not norm:
            return None                              # nothing readable was passed in
        return (cls.objects.select_related('user').filter(plate_number=norm).first()   # select_related: fetch the owner in the same query
                or cls.objects.select_related('user').filter(conduction_number=norm).first())

    def __str__(self):
        return self.identifier or f"Vehicle {self.pk}"   # fall back to the row number if it has neither

    class Meta:
        db_table = 'tbl_vehicle'
        constraints = [
            # Uniqueness among real values only; multiple blanks are allowed so a
            # conduction-only car (blank plate) and vice-versa don't collide.
            models.UniqueConstraint(
                fields=['plate_number'], condition=~models.Q(plate_number=''),   # "~" means NOT: skip blank rows
                name='uniq_vehicle_plate_number',
            ),
            models.UniqueConstraint(
                fields=['conduction_number'], condition=~models.Q(conduction_number=''),
                name='uniq_vehicle_conduction_number',
            ),
        ]


import uuid                                   # used below to mint the unguessable payment token
from django.utils import timezone             # not used at module level; the methods that need it import their own copy

# Matches an e-bike control number however it was typed: FM001, FM-1, FM-001.
_CONTROL_NUMBER_TYPED_RE = re.compile(r'^FM-?(\d{1,6})$')


# The one way a plate is tidied before it is stored or compared.
def _normalize_plate(value):
    """Canonical plate form used for uniqueness: upper-cased, no spaces."""
    return (value or '').strip().upper().replace(' ', '')   # tolerate None, trim, upper-case, drop spaces


# The same tidying, plus the e-bike control-number rule, used everywhere a
# vehicle is looked up so one car cannot exist under two spellings.
def canonical_identifier(value):
    """A plate, conduction number or e-bike control number in the one form it
    is stored and compared in: upper-cased, no spaces, and a control number as
    FM-001 however it was typed (FM001, FM-1). FM + digits is no Philippine
    plate shape, so canonicalizing it cannot capture a real plate."""
    norm = _normalize_plate(value)               # first the ordinary tidy-up
    control = _CONTROL_NUMBER_TYPED_RE.match(norm)   # then: does it look like a control number?
    if control:
        norm = f'FM-{int(control.group(1)):03d}'     # re-spell it as FM- plus three digits, e.g. FM-001
    return norm


# Emails are compared lower-cased, for the same reason plates are upper-cased.
def _normalize_email(value):
    return (value or '').strip().lower()


# One application for a vehicle pass: who is applying, for which vehicle, the
# documents attached, the fee, and how the CDSO decided. This is the longest
# model in the project because it carries every field the paper form had.
class VehicleRegistration(models.Model):
    # Where the application has got to.
    class Status(models.TextChoices):
        PENDING  = 'pending',  'Pending'          # submitted, nobody has reviewed it
        ACCEPTED = 'accepted', 'Accepted'         # approved; a pass and portal account exist
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

    # Which kind of applicant this is; it decides which fields below apply.
    class RegistrantType(models.TextChoices):
        STUDENT  = 'student',  'Student'
        EMPLOYEE = 'employee', 'Employee'
        FETCHER  = 'fetcher',  'Fetcher/Drop&Go'

    # The legacy day-pattern codes, kept in step with accounts.User.Schedule.
    class Schedule(models.TextChoices):
        MWF   = 'MWF',   'Monday-Wednesday-Friday'
        TTHF  = 'TTHF',  'Tuesday-Thursday-Friday'
        MIXED = 'MIXED', 'Mixed / Custom Days'
        # Spelled out rather than "Any Day": the campus is closed on Sunday, so
        # an unqualified "any day" overstates what the pass actually admits.
        ANY   = 'ANY',   'Any Campus Day (Monday-Saturday)'

    # How the application reached the system.
    class Source(models.TextChoices):
        PUBLIC = 'public', 'Online/Public Form'
        DIRECT = 'direct', 'CDSO Walk-in'

    # Employee applicants say which kind of department they work in, because
    # the fee depends on it.
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

    # Which part of the school a student applicant belongs to.
    class StudentLevel(models.TextChoices):
        COLLEGE    = 'college',    'College'
        SHS        = 'shs',        'Senior High School'
        JHS        = 'jhs',        'Junior High School'
        ELEMENTARY = 'elementary', 'Elementary'
        SPED       = 'sped',       'Special Education'

    # Who the driver is, when the student is not the one driving.
    class DriverRelationship(models.TextChoices):
        PARENT            = 'parent',            'Parent'
        GUARDIAN          = 'guardian',          'Guardian'
        AUTHORIZED_DRIVER = 'authorized_driver', 'Authorized Driver'

    # Two kinds of fetcher, which differ in whether they may stay parked.
    class FetcherType(models.TextChoices):
        DROP_AND_GO = 'drop_and_go', 'Fetcher / Drop & Go'
        STANDBY     = 'standby',     'Standby'

    id = models.BigAutoField(primary_key=True, db_column='vehicle_registration_id')
    user = models.ForeignKey(                        # the portal account created on acceptance
        'accounts.User',
        null=True, blank=True,
        on_delete=models.SET_NULL,                   # keep the paperwork if the account is deleted
        related_name='registrations',
    )
    vehicle = models.ForeignKey(                     # the Vehicle row this application produced
        'Vehicle',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='registrations',
    )

    # Common fields
    registrant_type = models.CharField(max_length=20, choices=RegistrantType.choices)
    full_name       = models.CharField(max_length=255)
    email           = models.EmailField(db_index=True)   # also the login for the portal account
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
    campus_days     = models.JSONField(default=list)     # the days requested, e.g. ["Monday", "Friday"]
    schedule        = models.CharField(max_length=10, choices=Schedule.choices, blank=True)

    # Student-specific
    student_id    = models.CharField(max_length=50, blank=True)      # the school's own ID number
    student_level = models.CharField(max_length=20, choices=StudentLevel.choices, blank=True)
    program_year  = models.CharField(max_length=100, blank=True)     # e.g. "BSIT - 3rd Year"

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
        limit_choices_to={'category': 'program'},    # only programs appear in this picker
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
        limit_choices_to={'category': 'department'},   # only departments appear in this picker
    )
    department_type = models.CharField(
        max_length=20, choices=DepartmentType.choices, null=True, blank=True,   # decides the fee, including exemption
    )

    # Vehicle fields — a registration carries EITHER a plate OR a conduction
    # number (brand-new car), never both; enforced in the views. plate_number is
    # blank-able so conduction-only registrations are valid.
    plate_number      = models.CharField(max_length=20, blank=True, default='', db_index=True)
    conduction_number = models.CharField(max_length=50, blank=True, default='', db_index=True)
    vehicle_type      = models.CharField(max_length=50)      # free text here; Vehicle.Type is the fixed list
    vehicle_color     = models.CharField(max_length=50, blank=True)
    body_number       = models.CharField(max_length=50, blank=True)   # chassis/body number, for motorcycles especially

    # Status & admin review
    status           = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    rejection_reason = models.TextField(blank=True)      # sent to the applicant, so they can fix and re-apply
    or_number        = models.CharField(max_length=100, blank=True)   # the Official Receipt number from Accounting
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

    # Auto-assigned unique system IDs (populated on acceptance). Two columns,
    # three registrant types: students get their own, and employees and fetchers
    # share the second as the "not a student" slot. The prefix, not the column,
    # is what says which — SLC-EMP- vs SLC-FET-. See _assign_system_id in
    # vehicles/views.py; every reader falls back across both columns.
    system_student_id  = models.CharField(max_length=30, blank=True, unique=True, null=True)
    system_employee_id = models.CharField(max_length=30, blank=True, unique=True, null=True)

    created_at  = models.DateTimeField(auto_now_add=True)    # when it was submitted
    reviewed_at = models.DateTimeField(null=True, blank=True)  # when the CDSO decided

    # Tidies the row every time it is saved, so the values stored are always in
    # the one form everything else compares against.
    def save(self, *args, **kwargs):
        # Minted on first save and never rotated — the link in the pending email
        # has to keep working for as long as the registration is reviewable.
        if not self.payment_token:
            self.payment_token = uuid.uuid4()        # a random, unguessable handle
            if kwargs.get('update_fields') is not None:
                kwargs['update_fields'] = list(kwargs['update_fields']) + ['payment_token']   # make sure the new token is actually written
        # Canonicalize so both the application-layer conflict checks and the
        # DB unique constraints below compare like-for-like values.
        self.plate_number = _normalize_plate(self.plate_number)
        self.conduction_number = _normalize_plate(self.conduction_number)
        self.email = _normalize_email(self.email)
        self.student_id = (self.student_id or '').strip()        # trim only: IDs are case-sensitive as issued
        self.employee_id = (self.employee_id or '').strip()
        self.drivers_license = (self.drivers_license or '').strip().upper()
        super().save(*args, **kwargs)                # hand the tidied row to Django to write

    # Is this applicant exempt from the fee entirely? Answerable before any row
    # exists, which the walk-in counter needs.
    @classmethod
    def is_fee_exempt(cls, registrant_type, department_type='') -> bool:
        """Whether this applicant owes nothing at all for a vehicle pass.

        Answerable without a row and without touching the database, which is
        what the CDSO walk-in path needs: it decides whether to demand an
        Official Receipt number before there is a registration to ask.
        """
        return (registrant_type == 'employee'
                and (department_type or '') in cls.FEE_EXEMPT_DEPARTMENTS)

    # The single place that answers "how much does this person owe?".
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
            return Decimal('0.00')                   # exempt: zero, whatever the configured rates say
        if settings_obj is None:
            settings_obj = SystemSettings.get()      # only fetch the settings row if the caller did not
        if registrant_type == 'employee':
            return settings_obj.vehicle_pass_fee_employee
        return settings_obj.vehicle_pass_fee         # students and fetchers pay the standard fee

    # The same answer for this particular registration.
    def pass_fee(self, settings_obj=None) -> Decimal:
        """What this applicant owes — see fee_for, which this delegates to."""
        return self.fee_for(self.registrant_type, self.department_type, settings_obj)

    def __str__(self):
        return f"{self.full_name} - {self.plate_number} ({self.status})"

    class Meta:
        db_table = 'tbl_vehicle_registration'
        indexes = [
            models.Index(fields=['-created_at'], name='vehreg_created_at'),        # newest-first listings
            models.Index(fields=['status', '-created_at'], name='vehreg_status_time'),  # the review queue
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
                Upper(Replace('plate_number', Value(' '), Value(''))),   # index the plate as UPPER with spaces removed
                name='vehreg_plate_norm',
            ),
            models.Index(Lower('email'), name='vehreg_email_norm'),      # index the email lower-cased
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
                condition=models.Q(status__in=['pending', 'accepted']),   # no blank exemption: an email is always required
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


# One uploaded enrolment document for one student named on a fetcher's
# application.
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
        VehicleRegistration, on_delete=models.CASCADE,   # CASCADE: the document is meaningless without its application
        related_name='fetcher_assessments',
    )
    student_index = models.PositiveIntegerField()        # which entry in fetcher_students this belongs to (0 = first)
    assessment_form = models.FileField(
        upload_to='assessments/fetcher/',
        validators=[FileExtensionValidator(
            allowed_extensions=['jpg', 'jpeg', 'png', 'webp', 'heic', 'heif', 'pdf'],
        )],
    )
    uploaded_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tbl_fetcher_student_assessment'
        ordering = ['student_index']                     # documents list in the same order as the students
        # One document per listed student: a re-upload replaces what is on file
        # rather than leaving the reviewer two copies with no way to tell which
        # one the applicant meant.
        constraints = [
            models.UniqueConstraint(
                fields=['registration', 'student_index'],
                name='uniq_fetcher_assessment_per_student',
            ),
        ]

    # Looks up the student this file belongs to, in the application's JSON list.
    def student(self):
        """The fetcher_students entry this document belongs to, or None."""
        students = self.registration.fetcher_students or []
        if 0 <= self.student_index < len(students):      # guard against an index the list no longer has
            entry = students[self.student_index]
            return entry if isinstance(entry, dict) else None   # ignore anything that is not a proper entry
        return None

    # A name to show the reviewer, even when the entry is missing or unnamed.
    def student_name(self):
        entry = self.student() or {}
        return entry.get('full_name') or f'Student #{self.student_index + 1}'   # +1 so the first student reads as #1

    def __str__(self):
        return f"Assessment for {self.student_name()} (registration {self.registration_id})"


# An owner's requested correction to an already-approved registration, held
# until the CDSO approves it.
class RegistrationChangeRequest(models.Model):
    """An owner's proposed correction to their own accepted registration,
    waiting on CDSO.

    An application still PENDING needs none of this — CDSO has not looked at it,
    so the applicant edits the row directly (see `RegistrationSelfEditView`).
    Once it is ACCEPTED the row is no longer just a form: it issued a vehicle
    pass, a gate QR, a `Vehicle` and a portal account, and the guard at the gate
    matches a car against it. So the owner's edit is filed here and the row is
    only touched when a reviewer approves it.

    `changes` holds the cleaned values, already through
    `registration_edits.clean_changes`, so approval is an apply rather than a
    re-parse — but it is re-validated at approval time anyway, because a plate
    that was free when the request was filed may have been taken since.

    `previous` is the snapshot from when the request was filed. It is NOT what
    the reviewer is shown (that is read live off the row, so the diff always
    describes what approving would actually overwrite) — it is there so the
    audit trail still says what the owner believed they were changing, even
    after the row has moved on.
    """

    class Status(models.TextChoices):
        PENDING   = 'pending',   'Pending CDSO Review'
        APPROVED  = 'approved',  'Approved'
        REJECTED  = 'rejected',  'Rejected'
        # The owner withdrew it themselves before anyone decided. Kept as a row
        # rather than deleted: "they asked and then thought better of it" is
        # part of the account's history.
        CANCELLED = 'cancelled', 'Cancelled by Owner'

    id = models.BigAutoField(primary_key=True, db_column='registration_change_request_id')
    registration = models.ForeignKey(
        VehicleRegistration, on_delete=models.CASCADE,   # a request cannot outlive the registration it edits
        related_name='change_requests',
    )
    # SET_NULL, like every other actor reference here: deleting an account must
    # not take the review history with it (see delete_user_with_owned_records).
    requested_by = models.ForeignKey(
        'accounts.User', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='registration_change_requests',
    )
    changes  = models.JSONField(default=dict)            # the proposed new values, already cleaned
    previous = models.JSONField(default=dict, blank=True)  # what they looked like when the request was filed
    status   = models.CharField(
        max_length=20, choices=Status.choices,
        default=Status.PENDING, db_index=True,           # indexed: the CDSO queue filters on it
    )
    reviewed_by = models.ForeignKey(
        'accounts.User', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='reviewed_change_requests',
    )
    reviewed_at   = models.DateTimeField(null=True, blank=True)
    # Required when rejecting — an owner told "no" with no reason has nothing to
    # correct and will simply file the same request again.
    decision_note = models.TextField(blank=True)
    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tbl_registration_change_request'
        ordering = ['-created_at']
        constraints = [
            # One open request per registration. Without this an owner could
            # stack three requests that each edit the plate, and approving them
            # in any order would leave the row holding whichever was approved
            # last rather than what anyone reviewed. Partial, so the decided
            # rows accumulate freely as history.
            models.UniqueConstraint(
                fields=['registration'],
                condition=models.Q(status='pending'),
                name='uniq_pending_change_request_per_registration',
            ),
        ]
        indexes = [
            # The CDSO queue: open requests, oldest first is what the screen
            # asks for, and the badge count reads the same index.
            models.Index(fields=['status', '-created_at'],
                         name='changereq_status_time'),
        ]

    def __str__(self):
        fields = ', '.join(sorted(self.changes or {}))   # list which fields the request touches
        return f"Change request {self.pk} ({self.status}): {fields or 'nothing'}"


# The campus-wide entry rule for one kind of entrant: which days and which
# hours they may come in. Read at every gate scan by scanning/entry_logic.py.
class RuleConstraint(models.Model):
    class ConstraintType(models.TextChoices):
        STUDENT_VEHICLE = 'student_vehicle', 'Student — Vehicle'
        EMPLOYEE        = 'employee',         'Employee'
        FETCHER         = 'fetcher',          'Fetcher / Drop & Go'
        SUPPLIER        = 'supplier',         'Supplier'

    id              = models.BigAutoField(primary_key=True, db_column='rule_constraint_id')
    name            = models.CharField(max_length=120)   # shown in the gate message, so the guard can name the rule
    constraint_type = models.CharField(max_length=20, choices=ConstraintType.choices)   # who the rule applies to
    days            = models.JSONField(default=list)     # short keys, e.g. ["mon","tue"]; see DAY_TO_WEEKDAY in entry_logic
    start_time      = models.CharField(max_length=5, default='06:00')   # "HH:MM" text, compared as minutes
    end_time        = models.CharField(max_length=5, default='20:00')
    max_stay_minutes = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Maximum allowed stay in minutes. Exceeding it on exit auto-issues a "
                  "time-exceed violation. Blank = no stay limit.",
    )
    enabled         = models.BooleanField(default=True)  # switching this off removes the restriction entirely
    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'tbl_rule_constraint'
        ordering = ['constraint_type', 'name']

    def __str__(self):
        return f"{self.name} ({self.constraint_type})"


# One parking area watched by one camera view: the lot itself, the picture the
# bays were drawn on, and how occupancy is decided there.
class ParkingZone(models.Model):
    class VehicleCategory(models.TextChoices):
        MOTORCYCLE = 'motorcycle', 'Motorcycle'
        CAR        = 'car',        'Car'

    # Two ways to decide whether a bay is taken.
    class OccupancyMethod(models.TextChoices):
        ML      = 'ml',      'Vehicle detector (YOLO)'          # a trained model finds vehicles
        CLASSIC = 'classic', 'Baseline comparison (no ML)'      # compare the bay against an empty photo

    id                = models.BigAutoField(primary_key=True, db_column='parking_zone_id')
    name              = models.CharField(max_length=100)
    vehicle_category  = models.CharField(max_length=20, choices=VehicleCategory.choices)   # cars and motorcycles are counted separately
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
    baseline_captured_at = models.DateTimeField(null=True, blank=True)   # so the admin can see how old the baseline is
    occupancy_method     = models.CharField(
        # Baseline by default: occupancy is a question about one fixed bay, and
        # the detector's false boxes on plants, stairs and air-conditioners in
        # a cluttered campus scene cost more than a model buys there. The
        # detector still runs for double parking, which only it can see.
        max_length=20, choices=OccupancyMethod.choices, default=OccupancyMethod.CLASSIC,
        help_text="How this zone decides a bay is taken. 'classic' compares each bay "
                  "against an empty baseline and needs no detector; it falls back to "
                  "the detector until a baseline is captured.",
    )
    camera            = models.ForeignKey(
        'Camera', null=True, blank=True, on_delete=models.SET_NULL,   # the zone survives the camera being removed
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
        return f"{self.name} ({self.get_vehicle_category_display()})"   # get_..._display() gives the readable label


# Every campus-wide setting the CDSO can change, kept as a single row so there
# is exactly one answer to each question.
class SystemSettings(models.Model):
    """Singleton row (pk=1) for CDSO/admin-configurable system-wide parameters."""
    id                   = models.BigAutoField(primary_key=True, db_column='system_settings_id')
    retention_years      = models.IntegerField(      # how long archived data is kept before the purge deletes it
        default=5,
        validators=[MinValueValidator(1), MaxValueValidator(10)],   # refuse nonsense values at the form layer
    )
    scan_dedup_seconds   = models.IntegerField(      # ignore a repeat read of the same plate within this many seconds
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
    open_campus_mode     = models.BooleanField(      # the master bypass checked first by check_entry
        default=False,
        help_text="When enabled, all vehicles are allowed entry regardless of registration or schedule rules.",
    )
    vehicle_pass_fee          = models.DecimalField(   # students and fetchers
        max_digits=8, decimal_places=2, default=300,
        validators=[MinValueValidator(0)],
        help_text="Vehicle Pass registration fee (₱) for students and fetchers.",
    )
    vehicle_pass_fee_employee = models.DecimalField(   # employees pay a different rate
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

    # ── Report signatories ──
    # Every branded PDF ends with a "Prepared by / Approved by" block, because
    # a report that leaves the office as a paper document needs to say who
    # stands behind it. The approver is stored here because the head of office
    # does not sign in to generate every report and the post changes hands.
    #
    # The preparer defaults to whoever is signed in and pressed the button -
    # the only honest answer, and what these two fields print when they are
    # blank. Named here, they override it, for the office that files every
    # report under one person's signature no matter who ran it. The PDF footer
    # still records the account that generated it either way, so overriding
    # the signature line never erases who actually pressed the button.
    #
    # The captions are settings too. "Prepared by" and "Approved by" are the
    # usual wording, but an office that files these under "Submitted by" or
    # "Noted by" should not need a code change to say so.
    report_preparer_name     = models.CharField(
        max_length=150, blank=True, default='',
        help_text="Name printed under 'Prepared by' on every PDF report. "
                  "Left blank, the report names whoever generated it.",
    )
    report_preparer_position = models.CharField(
        max_length=150, blank=True, default='',
        help_text="Position printed beneath the preparer's name. "
                  "Left blank, the report prints the generator's role.",
    )
    report_approver_name     = models.CharField(
        max_length=150, blank=True, default='',
        help_text="Name printed under 'Approved by' on every PDF report. "
                  "Left blank, the report prints a ruled line to sign on.",
    )
    report_approver_position = models.CharField(
        max_length=150, blank=True, default='Head, Campus Development and Security Office',
        help_text="Position printed beneath the approver's name.",
    )
    report_prepared_by_label = models.CharField(
        max_length=60, default='Prepared by',
        help_text="Caption above the signature of whoever generated the report.",
    )
    report_approved_by_label = models.CharField(
        max_length=60, default='Approved by',
        help_text="Caption above the approver's signature.",
    )

    class Meta:
        db_table = 'tbl_system_settings'
        verbose_name        = "System Settings"      # stop Django's admin calling it "System Settingss"
        verbose_name_plural = "System Settings"

    # The only way the rest of the code reads settings: fetch row 1, creating it
    # with the defaults above the first time anyone asks.
    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)     # the second value says whether it was just created; not needed
        return obj

    def __str__(self):
        return "System Settings"


# The record of which daily jobs have already run, which is what stops them
# running twice.
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
    job        = models.CharField(max_length=64)     # the job's name, e.g. 'auto_backup'
    run_date   = models.DateField()                  # the day being claimed
    started_at = models.DateTimeField(auto_now_add=True)
    # Null while in flight; a row that stays null is a job that crashed midway.
    finished_at = models.DateTimeField(null=True, blank=True)
    result      = models.CharField(max_length=255, blank=True, default='')   # a short summary for the admin screen

    class Meta:
        db_table = 'tbl_daily_job_run'
        ordering = ['-run_date', 'job']
        constraints = [
            models.UniqueConstraint(fields=['job', 'run_date'], name='uniq_daily_job_per_day'),   # the lock itself
        ]

    def __str__(self):
        return f"{self.job} @ {self.run_date}"


# The period during which applications are accepted, e.g. one semester.
class RegistrationPeriod(models.Model):
    """One row per registration window. Only one row may be active at a time."""
    id         = models.BigAutoField(primary_key=True, db_column='registration_period_id')
    label      = models.CharField(max_length=150)    # e.g. "AY 2026-2027 First Semester"
    start_date = models.DateField()
    end_date   = models.DateField()
    is_active  = models.BooleanField(default=False)  # exactly one row should carry this
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tbl_registration_period'
        ordering = ['-created_at']

    # The window in force now, or None when registration is closed.
    @classmethod
    def get_active(cls):
        return cls.objects.filter(is_active=True).first()

    def __str__(self):
        status = 'Active' if self.is_active else 'Archived'
        return f"{self.label} ({status})"


# A campus event. While one is under way it can reserve part of the parking and
# mark its organizers at the gate.
class Event(models.Model):
    """A campus event. Organizer plates are noted temporarily; activating closes parts of parking."""

    class ParkingShare(models.TextChoices):
        """How much of campus parking the event is expected to take up.

        Stored as the fraction's label rather than a raw percentage because
        that is how the CDSO actually plans an event — "half the parking is
        gone" — and a free-number field invites 37%, which nobody can act on.
        `share_fraction` turns it back into a number for the capacity maths.
        """
        NONE    = 'none',    'None — parking unaffected'
        QUARTER = 'quarter', 'About 1/4 of parking'
        THIRD   = 'third',   'About 1/3 of parking'
        HALF    = 'half',    'About 1/2 of parking'
        TWO_THIRDS = 'two_thirds', 'About 2/3 of parking'
        THREE_QUARTERS = 'three_quarters', 'About 3/4 of parking'
        FULL    = 'full',    'All of parking'

    # Fraction of declared capacity each share reserves. Kept beside the
    # choices so adding a share forces a decision about its size rather than
    # silently reserving nothing.
    SHARE_FRACTIONS = {
        'none': 0.0, 'quarter': 0.25, 'third': 1 / 3, 'half': 0.5,
        'two_thirds': 2 / 3, 'three_quarters': 0.75, 'full': 1.0,
    }

    id               = models.BigAutoField(primary_key=True, db_column='event_id')
    name             = models.CharField(max_length=200)
    date             = models.DateField()            # events are single-day
    # Both optional: an all-day event has no meaningful start, and a guard
    # reading "00:00–00:00" would think the event had already ended.
    start_time       = models.TimeField(null=True, blank=True)
    end_time         = models.TimeField(null=True, blank=True)
    parking_share    = models.CharField(
        max_length=20, choices=ParkingShare.choices, default=ParkingShare.NONE,
        help_text="How much of campus parking this event is expected to fill.",
    )
    is_active        = models.BooleanField(default=False)   # the switch that makes the event take effect
    archived         = models.BooleanField(default=False)   # finished events are kept but ignored
    organizer_plates = models.JSONField(default=list, blank=True)   # identifiers recognised at the gate as organizers
    created_by       = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='events_created',
    )
    created_at       = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tbl_event'
        ordering = ['-date', '-created_at']          # soonest-looking list: latest date first

    def __str__(self):
        return self.name

    # The chosen share as a number the capacity maths can multiply by.
    @property
    def share_fraction(self) -> float:
        """`parking_share` as a 0.0–1.0 multiplier of declared capacity."""
        return self.SHARE_FRACTIONS.get(self.parking_share, 0.0)   # unknown value reserves nothing

    # The event's times written the way they appear on a guard's card.
    @property
    def time_display(self) -> str:
        """"9:00 AM - 3:00 PM", "From 9:00 AM", or "All day" — what a guard
        needs to read off a card without doing arithmetic."""
        fmt = lambda t: t.strftime('%I:%M %p').lstrip('0')   # 12-hour time without the leading zero
        if self.start_time and self.end_time:
            return f'{fmt(self.start_time)} - {fmt(self.end_time)}'
        if self.start_time:
            return f'From {fmt(self.start_time)}'
        if self.end_time:
            return f'Until {fmt(self.end_time)}'
        return 'All day'                             # neither time set

    # Is the event running at this moment? Used both by the gate and by the
    # parking reserve, so they always agree.
    def is_under_way(self, now=None) -> bool:
        """True while the clock is inside the event's window today.

        An event with no times set is under way for the whole of its day — the
        absence of a window means "all day", not "never".
        """
        from django.utils import timezone as _tz     # imported here so the module stays import-light
        if not self.is_active or self.archived:
            return False                             # switched off, or finished
        now = now or _tz.localtime()                 # default to now, campus time
        if now.date() != self.date:
            return False                             # not today
        current = now.time()
        if self.start_time and current < self.start_time:
            return False                             # not started yet
        if self.end_time and current > self.end_time:
            return False                             # already finished
        return True


# A message the CDSO broadcasts to every vehicle owner.
class ParkingNotice(models.Model):
    """Admin/CDSO-authored broadcast message sent to all vehicle owners by email and shown in their portal."""
    id         = models.BigAutoField(primary_key=True, db_column='parking_notice_id')
    title      = models.CharField(max_length=200)
    body       = models.TextField()
    is_active  = models.BooleanField(default=True)   # clearing this hides it from the portal
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


# One parking bay drawn on a camera view, and whether it is currently taken.
class ParkingSpace(models.Model):
    id           = models.BigAutoField(primary_key=True, db_column='parking_space_id')
    zone         = models.ForeignKey(
        ParkingZone, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='spaces',
    )
    space_number = models.CharField(max_length=20)   # the label painted on the bay, e.g. "A12"
    x1           = models.FloatField(null=True, blank=True)   # the bay's box on the camera image,
    y1           = models.FloatField(null=True, blank=True)   # stored as fractions of the frame (0-1)
    x2           = models.FloatField(null=True, blank=True)   # so it survives a change of resolution
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
    is_occupied  = models.BooleanField(default=False)    # what the screens show as red or green
    occupied_by  = models.CharField(max_length=20, blank=True)   # the plate, when one was read
    # What this bay's own readings look like while it is empty, and therefore
    # what counts as a change worth claiming it for — see bay_occupancy.
    # {'samples': n, 'mad_mean', 'mad_std', 'edge_mean', 'edge_std', 'updated_at'}.
    #
    # Written with queryset .update(), never .save(): `updated_at` below is
    # auto_now and feeds bay_occupancy.layout_signature, so saving the model
    # would invalidate the zone's prepared baseline — discarding every bay's
    # live baseline and restarting its refresh clock — every time a bay learned
    # something about itself.
    noise_stats  = models.JSONField(
        null=True, blank=True,
        help_text="Measured noise of this bay while empty; sets its occupancy "
                  "thresholds. Cleared to fall back to the conservative defaults.",
    )
    updated_at   = models.DateTimeField(auto_now=True)   # touched on every occupancy change

    class Meta:
        db_table = 'tbl_parking_space'
        ordering = ['zone__vehicle_category', 'space_number']   # group by lot type, then by bay label

    def __str__(self):
        status = f"({self.occupied_by})" if self.is_occupied else "(free)"
        cat = self.zone.get_vehicle_category_display() if self.zone else '?'   # the bay may have lost its zone
        return f"{cat} Space {self.space_number} {status}"


# A company whose vehicles are expected at the gate.
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
    is_active    = models.BooleanField(default=True)   # cleared when a contract ends; their plates stop being recognised
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'tbl_supplier'
        ordering = ['company_name']

    def __str__(self):
        return self.company_name


# A visit arranged in advance, so the guard knows who to expect.
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
    supplier      = models.ForeignKey(               # set when the visit is on behalf of a known company
        Supplier, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='scheduled_visits',
    )
    plate_number  = models.CharField(max_length=20, blank=True)   # blank when the vehicle is not known in advance
    purpose       = models.CharField(max_length=255, blank=True)
    expected_date = models.DateField()
    notes         = models.TextField(blank=True)
    is_arrived    = models.BooleanField(default=False)   # ticked off when they turn up
    # Set by the gate (scheduled_visits.mark_arrived), not typed: the moment
    # the entry was logged. Blank on a visit ticked off by hand before this
    # field existed, or unticked since.
    arrived_at    = models.DateTimeField(null=True, blank=True)
    created_by    = models.ForeignKey(               # printed on the gate slip as "Arranged by"
        'accounts.User', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
    )
    # Archived instead of deleted, so a cancelled or abandoned booking stays on
    # record. An archived visit is off the guard's Expected Today and never
    # matched at the gate; restoring it clears all three.
    archived_at    = models.DateTimeField(null=True, blank=True)
    archived_by    = models.ForeignKey(
        'accounts.User', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
    )
    archive_reason = models.CharField(max_length=255, blank=True)
    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['expected_date', 'visitor_name']   # note: no db_table here, so Django names the table itself

    def __str__(self):
        return f"{self.visitor_name} — {self.expected_date}"


# One plate belonging to a supplier; this is what classify_entrant checks.
class SupplierPlate(models.Model):
    """A license plate registered under a supplier."""
    id           = models.BigAutoField(primary_key=True, db_column='supplier_plate_id')
    supplier     = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name='plates')   # plates go with the company
    plate_number = models.CharField(max_length=20, unique=True, db_index=True)   # one company per plate
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tbl_supplier_plate'
        ordering = ['plate_number']

    def __str__(self):
        return f"{self.plate_number} ({self.supplier.company_name})"


# A physical camera and how to reach its video stream.
class Camera(models.Model):
    # What the camera is for, which decides how the system uses its frames.
    class Assignment(models.TextChoices):
        ENTRY   = 'entry',   'Entry'         # watches a gate and reads plates
        PARKING = 'parking', 'Parking'       # watches a lot and scores bays

    class GateId(models.TextChoices):
        GATE1 = 'gate1', 'Gate 1'
        GATE4 = 'gate4', 'Gate 4'

    id         = models.BigAutoField(primary_key=True, db_column='camera_id')
    cam_number = models.PositiveIntegerField(unique=True)   # the number written on the device itself
    name       = models.CharField(max_length=50)
    ip         = models.CharField(max_length=100)           # address on the campus network
    device_id  = models.CharField(max_length=100)           # the manufacturer's serial / device identifier
    # Optional, but usually needed: IMOU/Dahua units refuse RTSP without it.
    # Blank is allowed so a genuinely open camera can still be added without
    # inventing a credential for it.
    password   = models.CharField(max_length=100, blank=True, default='')
    rtsp_url   = models.CharField(max_length=500)           # the full stream address the video pipeline opens
    assignment = models.CharField(max_length=20, choices=Assignment.choices)
    # Gate slug (e.g. 'gate1'). No choices constraint — gates are dynamic rows
    # in scanning.Gate so admins can add new ones from System Settings.
    gate_id    = models.CharField(max_length=10, null=True, blank=True,
                                  help_text='Required when assignment is Entry. Identifies which gate this camera covers.')
    is_active  = models.BooleanField(default=True)          # clearing this stops the system opening the stream
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'tbl_camera'
        ordering = ['cam_number']

    # The gate's readable name. Written by hand because Django cannot generate
    # one for a field that deliberately has no fixed list of choices.
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
            return ''                                # a parking camera has no gate
        if self.gate_id in self.GateId.values:
            return self.GateId(self.gate_id).label   # one of the two original gates: use its label
        return self.gate_id                          # a gate added later: show the slug as stored

    def __str__(self):
        gate = f' — {self.gate_label}' if self.gate_id else ''
        return f"{self.name} ({self.get_assignment_display()}{gate})"

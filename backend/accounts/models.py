import uuid
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models


class UserManager(BaseUserManager):
    """Custom manager that uses email instead of username."""

    def create_user(self, email, full_name, password=None, **extra_fields):
        if not email:
            raise ValueError('Email is required')
        if not full_name:
            raise ValueError('Full name is required')
        email = self.normalize_email(email)
        user = self.model(email=email, full_name=full_name, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, full_name, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('role', 'admin')
        return self.create_user(email, full_name, password, **extra_fields)


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN          = 'admin',          'Admin'
        SECURITY       = 'security',       'Security Personnel'
        VEHICLE_OWNER  = 'vehicle_owner',  'Registered Vehicle Owner'
        CDSO           = 'cdso',           'CDSO Staff'

    class OwnerType(models.TextChoices):
        STUDENT  = 'student',  'Student'
        FETCHER  = 'fetcher',  'Fetcher/Dropper'
        EMPLOYEE = 'employee', 'Employee'
        VISITOR  = 'visitor',  'Visitor'

    class Schedule(models.TextChoices):
        MWF   = 'MWF',   'Monday-Wednesday-Friday'
        TTHS  = 'TTHS',  'Tuesday-Thursday-Saturday'
        MIXED = 'MIXED', 'Custom / Mixed Days'
        ANY   = 'ANY',   'Any Day'
        ALL   = 'ALL',   'All Days'

    # Role-prefixed human-readable ID, e.g. SLC-ADM-000001
    _ROLE_PREFIX = {
        'admin':         'ADM',
        'security':      'SEC',
        'vehicle_owner': 'OWN',
        'cdso':          'CDS',
    }

    class Gate(models.TextChoices):
        GATE1 = 'gate1', 'Gate 1'
        GATE4 = 'gate4', 'Gate 4'

    id = models.BigAutoField(primary_key=True, db_column='user_id')
    full_name = models.CharField(max_length=150)
    email = models.EmailField(unique=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.VEHICLE_OWNER)
    user_code = models.CharField(max_length=20, unique=True, null=True, blank=True, db_index=True)
    must_change_password = models.BooleanField(default=False)

    # Security guard fields
    gate_assignment = models.CharField(max_length=10, choices=Gate.choices, null=True, blank=True)
    agency = models.CharField(max_length=150, null=True, blank=True)
    qr_token = models.UUIDField(default=uuid.uuid4, unique=True)

    # Owner profile fields — only populated for vehicle_owner role
    owner_type  = models.CharField(max_length=20, choices=OwnerType.choices, null=True, blank=True)
    schedule    = models.CharField(max_length=10, choices=Schedule.choices, null=True, blank=True)
    campus_days = models.JSONField(default=list)  # e.g. ["Monday", "Tuesday", "Wednesday"]
    contact     = models.CharField(max_length=50, null=True, blank=True)
    address     = models.TextField(null=True, blank=True)
    photo       = models.ImageField(upload_to='owners/', null=True, blank=True)

    # Security-guard QR badge secret — a UUID printed on the guard's badge as a QR code.
    # Format in QR: "SLC-GUARD:{user_code}:{guard_qr_secret}"
    guard_qr_secret = models.UUIDField(null=True, blank=True, unique=True)

    # Override username to be nullable/blank, email is used for login
    username = models.CharField(max_length=150, blank=True, null=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['full_name']  # email is already required via USERNAME_FIELD

    objects = UserManager()

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Generate user_code once after pk is available
        if not self.user_code:
            prefix = self._ROLE_PREFIX.get(self.role, 'USR')
            self.user_code = f"SLC-{prefix}-{str(self.pk).zfill(6)}"
            User.objects.filter(pk=self.pk).update(user_code=self.user_code)

    def __str__(self):
        return f"{self.full_name} ({self.role})"


class AuditLog(models.Model):
    class Action(models.TextChoices):
        USER_CREATED     = 'user_created',     'User Created'
        USER_UPDATED     = 'user_updated',     'User Updated'
        USER_DELETED     = 'user_deleted',     'User Deleted'
        USER_DISABLED    = 'user_disabled',    'User Disabled'
        USER_ENABLED     = 'user_enabled',     'User Enabled'
        ADMIN_REPLACED   = 'admin_replaced',   'Admin Replaced'
        SCAN             = 'scan',             'Vehicle Scanned'
        VEHICLE_ENTERED  = 'vehicle_entered',  'Vehicle Entered'
        VEHICLE_EXITED   = 'vehicle_exited',   'Vehicle Exited'
        VISITOR_ISSUED   = 'visitor_issued',   'Visitor Pass Issued'
        VISITOR_EXITED   = 'visitor_exited',   'Visitor Exited'
        ENTRY_OVERRIDE   = 'entry_override',   'Entry Override'
        # Generic CRUD actions for all other admin-managed records
        RECORD_CREATED   = 'created',          'Record Created'
        RECORD_UPDATED   = 'updated',          'Record Updated'
        RECORD_DELETED   = 'deleted',          'Record Deleted'

    id          = models.BigAutoField(primary_key=True, db_column='audit_log_id')
    actor       = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='audit_logs')
    action      = models.CharField(max_length=30, choices=Action.choices)
    target_user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='target_logs')
    details     = models.TextField(blank=True)
    ip_address  = models.GenericIPAddressField(null=True, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        actor_name = self.actor.full_name if self.actor else 'Unknown'
        return f"{actor_name} - {self.get_action_display()} - {self.created_at}"


class Notification(models.Model):
    """Admin notification-bell feed — important system events around
    violations and vehicle registration. Rows are created by signal handlers
    (see accounts/notifications.py), never directly by request handlers."""

    class Category(models.TextChoices):
        VIOLATION    = 'violation',    'Violation'
        REGISTRATION = 'registration', 'Registration'

    class Severity(models.TextChoices):
        INFO     = 'info',     'Info'
        WARNING  = 'warning',  'Warning'
        CRITICAL = 'critical', 'Critical'

    id           = models.BigAutoField(primary_key=True, db_column='notification_id')
    category     = models.CharField(max_length=20, choices=Category.choices)
    event        = models.CharField(max_length=40, blank=True)  # slug, e.g. 'violation_issued'
    severity     = models.CharField(max_length=10, choices=Severity.choices, default=Severity.INFO)
    title        = models.CharField(max_length=200)
    message      = models.TextField(blank=True)
    plate_number = models.CharField(max_length=20, blank=True)
    link         = models.CharField(max_length=200, blank=True)  # frontend route, e.g. '/admin/violations'
    is_read      = models.BooleanField(default=False)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.category}] {self.title}"
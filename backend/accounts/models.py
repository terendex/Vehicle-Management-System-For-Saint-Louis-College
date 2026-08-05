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

        # Owner accounts expire a configurable time after creation (System
        # Settings). Compute the frozen expiry date once, at creation, unless a
        # caller supplied one explicitly. Admin/security accounts never expire.
        if (extra_fields.get('role') == User.Role.VEHICLE_OWNER
                and 'expires_at' not in extra_fields):
            expiry = self._owner_expiry_date()
            if expiry is not None:
                extra_fields['expires_at'] = expiry

        user = self.model(email=email, full_name=full_name, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    @staticmethod
    def _owner_expiry_date():
        """Creation-date + configured (months, days), or None if expiry is off.

        Lazy import of SystemSettings avoids an accounts->vehicles import cycle.
        """
        try:
            from vehicles.models import SystemSettings
        except Exception:
            return None
        cfg = SystemSettings.get()
        if not cfg.account_expiry_enabled:
            return None
        if cfg.account_expiry_months <= 0 and cfg.account_expiry_days <= 0:
            return None
        from datetime import timedelta
        from dateutil.relativedelta import relativedelta
        from django.utils import timezone
        return (timezone.localdate()
                + relativedelta(months=cfg.account_expiry_months)
                + timedelta(days=cfg.account_expiry_days))

    def get_by_natural_key(self, username):
        # Email is unique only among live accounts (archived rows share it), so
        # authentication must resolve to the non-archived user.
        return self.get(**{self.model.USERNAME_FIELD: username, 'is_archived': False})

    def create_superuser(self, email, full_name, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('role', 'admin')
        return self.create_user(email, full_name, password, **extra_fields)


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN          = 'admin',          'CDSO'
        SECURITY       = 'security',       'Security Personnel'
        VEHICLE_OWNER  = 'vehicle_owner',  'Registered Vehicle Owner'

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
    # Not globally unique: an archived owner keeps their email so history is
    # preserved, while a new live account may reuse it. Uniqueness among *live*
    # accounts is enforced by the partial constraint in Meta.
    email = models.EmailField(db_index=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.VEHICLE_OWNER)
    user_code = models.CharField(max_length=20, unique=True, null=True, blank=True, db_index=True)
    must_change_password = models.BooleanField(default=False)

    # Security guard fields
    # Gate slug (e.g. 'gate1'). No choices constraint — gates are dynamic rows
    # in scanning.Gate so admins can add new ones from System Settings.
    gate_assignment = models.CharField(max_length=10, null=True, blank=True)
    agency = models.CharField(max_length=150, null=True, blank=True)
    qr_token = models.UUIDField(default=uuid.uuid4, unique=True)

    # Owner profile fields — only populated for vehicle_owner role
    owner_type  = models.CharField(max_length=20, choices=OwnerType.choices, null=True, blank=True)
    schedule    = models.CharField(max_length=10, choices=Schedule.choices, null=True, blank=True)
    campus_days = models.JSONField(default=list)  # e.g. ["Monday", "Tuesday", "Wednesday"]
    contact     = models.CharField(max_length=50, null=True, blank=True)
    address     = models.TextField(null=True, blank=True)
    photo       = models.ImageField(upload_to='owners/', null=True, blank=True)

    # Owner-account expiration (vehicle_owner only). expires_at is frozen at
    # creation from System Settings; the daily maintenance job archives the
    # account once it passes. Archiving sets is_archived + clears is_active.
    expires_at  = models.DateField(null=True, blank=True, db_index=True)
    is_archived = models.BooleanField(default=False, db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    # Set when an account is archived AND had reached the maximum violations
    # (a registration-blocking 3rd-offense). Such a person may not register a
    # new vehicle pass — their identity is NOT freed on archive.
    registration_banned = models.BooleanField(default=False, db_index=True)

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

    class Meta:
        db_table = 'tbl_user'
        constraints = [
            # Email is unique among live accounts only. An archived owner keeps
            # their email (history), but it no longer blocks a fresh account —
            # so an expired owner can register again with the same address.
            models.UniqueConstraint(
                fields=['email'],
                condition=models.Q(is_archived=False),
                name='uniq_active_user_email',
            ),
        ]
        indexes = [
            # The retention purge asks, every single day, "which archived
            # accounts passed the window?". Partial on is_archived so the index
            # holds only archived rows — the minority, and the only ones the
            # purge can ever delete — which keeps the daily scan proportional to
            # the accounts actually due rather than to every user on the system.
            models.Index(
                fields=['archived_at'],
                condition=models.Q(is_archived=True),
                name='user_archived_at',
            ),
            # The archive job's daily "who expired?" — expires_at alone is
            # indexed, but every run also filters the two flags, and this lets
            # the whole predicate be answered from the index.
            models.Index(
                fields=['expires_at'],
                condition=models.Q(is_archived=False, is_active=True),
                name='user_expiry_due',
            ),
        ]


def delete_users_with_owned_records(users):
    """Delete every user in the `users` queryset, with the records they own.

    Vehicle and VehicleRegistration point at User with SET_NULL, so a plain
    `.delete()` would orphan them — a plateless registration row and an unowned
    vehicle that still matches at the gate. Both callers (an admin deleting from
    User Management, and the retention purge) need the same sweep, and a
    destructive invariant duplicated in two places is one that drifts, so it
    lives here.

    AuditLog.actor / target_user are deliberately SET_NULL: the history of what
    happened stays readable after the account itself is gone.

    Takes a queryset, not a list, and passes it straight through as a subquery —
    so the statement count is fixed no matter how many accounts match, and no id
    list is ever materialised into an IN clause that Postgres would choke on.

    Returns (vehicles, registrations, accounts) deleted.
    """
    from vehicles.models import Vehicle, VehicleRegistration   # avoids an import cycle
    from violations.models import Violation

    # Violations go with the account, by policy: deleting an owner removes
    # their violation history rather than leaving it behind unattributed.
    #
    # This has to be explicit now. Violation.vehicle is SET_NULL (so a record
    # survives archiving, which merely unlinks the vehicle from its owner), and
    # under SET_NULL deleting the vehicle would leave the violation orphaned
    # instead of removing it.
    #
    # It must also run BEFORE the vehicles are deleted — afterwards vehicle_id
    # is null and there is no longer any path from a violation back to the
    # account that owned it.
    #
    # The cost: a 3rd-offense registration hold is enforced by
    # Violation.registration_blocked, so deleting an account clears any hold
    # against its plates. Someone deleted and re-registered starts clean.
    _, vio_counts  = Violation.objects.filter(vehicle__user__in=users).delete()

    # Registrations first: they reference Vehicle with SET_NULL, so clearing
    # them first avoids a pointless UPDATE-to-null on rows about to be deleted.
    _, reg_counts  = VehicleRegistration.objects.filter(user__in=users).delete()
    _, veh_counts  = Vehicle.objects.filter(user__in=users).delete()
    _, user_counts = users.delete()

    return (
        veh_counts.get('vehicles.Vehicle', 0),
        reg_counts.get('vehicles.VehicleRegistration', 0),
        user_counts.get('accounts.User', 0),
    )


def delete_user_with_owned_records(user):
    """Single-user form of :func:`delete_users_with_owned_records`.

    Returns (vehicles_deleted, registrations_deleted).
    """
    vehicles, regs, _ = delete_users_with_owned_records(
        User.objects.filter(pk=user.pk)
    )
    return vehicles, regs


class AuditLog(models.Model):
    class Action(models.TextChoices):
        USER_CREATED     = 'user_created',     'User Created'
        USER_UPDATED     = 'user_updated',     'User Updated'
        USER_DELETED     = 'user_deleted',     'User Deleted'
        USER_DISABLED    = 'user_disabled',    'User Disabled'
        USER_ENABLED     = 'user_enabled',     'User Enabled'
        USER_ARCHIVED    = 'user_archived',    'Account Auto-Archived (Expired)'
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
        db_table = 'tbl_audit_log'
        ordering = ['-created_at']
        indexes = [
            # Meta.ordering + the date-range filters on the Audit Log screen.
            models.Index(fields=['-created_at'], name='auditlog_created_at'),
            # Action filter, and the dashboard's per-actor-role recent lists.
            models.Index(fields=['action', '-created_at'], name='auditlog_action_time'),
            models.Index(fields=['actor', '-created_at'], name='auditlog_actor_time'),
        ]

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
        db_table = 'tbl_notification'
        ordering = ['-created_at']
        indexes = [
            # The bell feed: newest first, and the unread badge count.
            models.Index(fields=['-created_at'], name='notification_created_at'),
            models.Index(fields=['is_read', '-created_at'], name='notification_unread'),
        ]

    def __str__(self):
        return f"[{self.category}] {self.title}"
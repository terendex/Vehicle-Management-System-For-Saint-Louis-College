import uuid
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.db.models.functions import Upper


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
        TTHF  = 'TTHF',  'Tuesday-Thursday-Friday'
        MIXED = 'MIXED', 'Custom / Mixed Days'
        # "Any Day" / "All Days" read as Sunday included; the campus is closed
        # then, so both are spelled out as the week they really cover.
        ANY   = 'ANY',   'Any Campus Day (Monday-Saturday)'
        ALL   = 'ALL',   'All Campus Days (Monday-Saturday)'

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
    # Set when a password is reset through the "forgot password" email flow, and
    # cleared only once a two-factor code has actually been entered.
    #
    # A reset is the account-takeover path: whoever reads the mailbox can set a
    # new password without ever knowing the old one. Demanding the second factor
    # on the very next login is what stops a stolen inbox from being a stolen
    # account. Trusted devices and the weekly dormancy window are both ignored
    # while this is set — it outranks them.
    #
    # This is deliberately an explicit flag rather than a side effect. Changing
    # the password already invalidates the device token (see twofa._fingerprint),
    # which happens to force a challenge too, but that is emergent behaviour of
    # the signing scheme. Anyone reworking token signing later would silently
    # remove the protection; a stored flag and a test say what is actually meant.
    must_verify_2fa = models.BooleanField(default=False)

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

    # ── Confiscation (violation penalty) ─────────────────────────────────────
    # The penalty ladder replaced fines: 1st offence costs the account a week,
    # 2nd two weeks, 3rd the rest of the registration period. A confiscated
    # account may not enter campus and may not park.
    #
    # This is deliberately NOT is_active. Disabling an account is an
    # administrative act that also stops the person logging in to see why they
    # were penalised; confiscation only withdraws campus access, and the owner
    # keeps their portal so they can read the reason and the end date.
    #
    # `confiscated_until` is a date rather than a flag so the penalty expires on
    # its own: is_confiscated compares it to today on every read, and no job has
    # to run for the account to come back. NULL with a level set means
    # indefinite — the 3rd offence with no registration period to end against.
    confiscation_level  = models.PositiveSmallIntegerField(
        default=0,
        help_text='0 = not confiscated. 1, 2 or 3 = which offence imposed it.',
    )
    confiscated_at      = models.DateTimeField(null=True, blank=True)
    confiscated_until   = models.DateField(
        null=True, blank=True, db_index=True,
        help_text='Last day of the penalty, inclusive. NULL while a level is '
                  'set means indefinite (until the CDSO lifts it).',
    )
    confiscation_reason = models.TextField(blank=True, default='')

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

    # ── Confiscation helpers ─────────────────────────────────────────────────

    @property
    def is_confiscated(self) -> bool:
        """True while the account is serving a violation penalty.

        Evaluated from the stored end date rather than from a boolean that a
        scheduled job has to clear, so a one-week penalty ends on its own even
        if nothing is running. A level with no end date is indefinite.
        """
        if not self.confiscation_level:
            return False
        if self.confiscated_until is None:
            return True
        from django.utils import timezone as _tz
        return _tz.localdate() <= self.confiscated_until

    @property
    def confiscation_days_left(self):
        """Whole days remaining, or None when indefinite / not confiscated."""
        if not self.is_confiscated or self.confiscated_until is None:
            return None
        from django.utils import timezone as _tz
        return max(0, (self.confiscated_until - _tz.localdate()).days)

    def clear_confiscation(self):
        """Lift the penalty. Leaves the violations themselves untouched — the
        offence history is what the ladder counts, and forgiving the penalty is
        not the same as saying the offences never happened."""
        self.confiscation_level  = 0
        self.confiscated_at      = None
        self.confiscated_until   = None
        self.confiscation_reason = ''
        self.save(update_fields=[
            'confiscation_level', 'confiscated_at',
            'confiscated_until', 'confiscation_reason',
        ])

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
            # Every account lookup by address is case-insensitive — login, the
            # password reset, the registration duplicate check, and each of the
            # account serializers' uniqueness validators all use email__iexact.
            # On PostgreSQL that compiles to UPPER(email) = UPPER(%s), which the
            # plain db_index=True btree on email cannot answer, so all of them
            # were sequential scans of tbl_user that grew with the account count.
            # Indexing the same UPPER(email) expression the ORM emits makes them
            # index lookups without any query having to change.
            models.Index(Upper('email'), name='user_email_upper'),
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


class AuditLogManager(models.Manager):
    """Refuses to write rows that record a vehicle owner's own activity.

    The rule lives here rather than at each call site because that is not where
    the leak came from: rows were reaching the table from view helpers, from
    ad-hoc server-shell sessions, and from code written long after the policy
    was set. A manager is the one place all of them pass through.

    create() returns None when a row is suppressed. No caller uses the return
    value of a suppressed write; staff writes are unaffected and still return
    the saved instance.
    """

    def create(self, **kwargs):
        if AuditLog.records_owner_activity(
            kwargs.get('action'), kwargs.get('actor'), kwargs.get('target_user')
        ):
            return None
        return super().create(**kwargs)

    def bulk_create(self, objs, *args, **kwargs):
        kept = [
            o for o in objs
            if not AuditLog.records_owner_activity(o.action, o.actor, o.target_user)
        ]
        return super().bulk_create(kept, *args, **kwargs)


class AuditLog(models.Model):
    """Administrative accountability trail: what *staff* did to the system.

    Deliberately NOT a record of where vehicle owners went. Routine gate
    movement (a plate being scanned, entering, or exiting) is personal data
    about the owner, and re-filing it here turned an admin-only screen into a
    searchable, exportable movement profile of every registered driver — which
    the campus privacy notice does not cover. Gate activity already lives in
    scanning.AccessLog, which is what the guard and operations screens read and
    what data retention prunes.

    So: never add an action here that records an owner simply arriving or
    leaving. Exception events that need a named accountable staff member
    (ENTRY_OVERRIDE, VISITOR_ISSUED) are the deliberate carve-out.

    The same boundary applies to account activity, and it turns on *who acted*,
    not on who is named. An owner enabling two-factor on their own account is
    the owner's business and is not recorded. A CDSO resetting that owner's
    two-factor is a staff act on someone else's account and is recorded, owner
    named, because that is exactly the kind of privilege use an audit trail
    exists to hold someone answerable for. AuditLogManager enforces this.
    """

    class Action(models.TextChoices):
        USER_CREATED     = 'user_created',     'User Created'
        USER_UPDATED     = 'user_updated',     'User Updated'
        USER_DELETED     = 'user_deleted',     'User Deleted'
        USER_DISABLED    = 'user_disabled',    'User Disabled'
        USER_ENABLED     = 'user_enabled',     'User Enabled'
        USER_ARCHIVED    = 'user_archived',    'Account Auto-Archived (Expired)'
        ADMIN_REPLACED   = 'admin_replaced',   'Admin Replaced'
        # Two-factor lifecycle. Enrollment and removal are security-relevant
        # account changes; a repeated TWOFA_FAILED against one account is the
        # signal that someone is guessing codes against a known password.
        TWOFA_ENABLED    = 'twofa_enabled',    'Two-Factor Enabled'
        TWOFA_DISABLED   = 'twofa_disabled',   'Two-Factor Disabled'
        TWOFA_RESET      = 'twofa_reset',      'Two-Factor Reset by Admin'
        TWOFA_FAILED     = 'twofa_failed',     'Two-Factor Verification Failed'
        TWOFA_BACKUP_USED = 'twofa_backup_used', 'Two-Factor Backup Code Used'
        # Guard shift sign-in (QR, credentials, or gate kiosk). This is staff
        # authentication, not vehicle activity; it used to be filed under the
        # old 'scan' action, which is why migration 0037 re-points those rows.
        GUARD_LOGIN      = 'guard_login',      'Guard Shift Login'
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

    # Actions an account holder performs on their own account. The person who
    # acted is the target_user, so these are owner activity whenever that user
    # is an owner — including the rows written before this rule existed, whose
    # actor was never captured and reads as 'System'.
    SELF_SERVICE_ACTIONS = frozenset({
        'twofa_enabled', 'twofa_disabled', 'twofa_failed', 'twofa_backup_used',
    })

    objects = AuditLogManager()

    @staticmethod
    def records_owner_activity(action, actor, target_user):
        """True when this row would record what a vehicle owner did.

        Two shapes count. The plain one is an owner as the actor. The other is
        a self-service action with no actor recorded: nobody else can perform
        those, so the target is the one who acted.
        """
        def _is_owner(u):
            return u is not None and getattr(u, 'role', None) == User.Role.VEHICLE_OWNER

        if _is_owner(actor):
            return True
        return actor is None and action in AuditLog.SELF_SERVICE_ACTIONS and _is_owner(target_user)

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

class TwoFactorDevice(models.Model):
    """A user's enrolled TOTP authenticator (Google Authenticator and friends).

    One per account. The row exists from the moment enrollment starts, but
    `confirmed_at` stays NULL until the person proves they can read a code off
    the app — so an abandoned setup never locks anyone out of their own account.

    Guards never get a row here; see accounts.twofa.TWO_FACTOR_ROLES for why.
    """

    id = models.BigAutoField(primary_key=True, db_column='two_factor_device_id')
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name='twofa_device',
    )
    # Base32 TOTP secret. Stored in the clear, as it must be to compute codes —
    # the database is the trust boundary, the same one the password hashes and
    # the guard QR secrets already sit behind.
    secret = models.CharField(max_length=64)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    # Highest TOTP timestep already spent, so a code cannot be replayed inside
    # its validity window. See twofa.verify_code.
    last_used_step = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    last_verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'tbl_two_factor_device'

    @property
    def is_confirmed(self) -> bool:
        return self.confirmed_at is not None

    def __str__(self):
        state = 'confirmed' if self.is_confirmed else 'pending'
        return f"2FA device for {self.user.email} ({state})"


class TwoFactorBackupCode(models.Model):
    """One single-use recovery code, stored only as a hash.

    These are what stand between a lost phone and an unrecoverable system: the
    CDSO account can hold the only admin login, and a wiped authenticator with
    no way back would take the whole administration surface with it.
    """

    id = models.BigAutoField(primary_key=True, db_column='two_factor_backup_code_id')
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='twofa_backup_codes',
    )
    code_hash = models.CharField(max_length=64, db_index=True)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tbl_two_factor_backup_code'
        indexes = [
            # The lookup on every backup-code login: this user's unused codes.
            models.Index(
                fields=['user'],
                condition=models.Q(used_at__isnull=True),
                name='twofa_backup_unused',
            ),
        ]

    def __str__(self):
        return f"Backup code for {self.user.email} ({'used' if self.used_at else 'unused'})"

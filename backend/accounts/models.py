# =============================================================================
# WHAT THIS FILE IS FOR
#
# These are the database tables behind accounts and accountability:
#
#   User                 every person who can sign in: the CDSO (admin), the
#                        security guards, and the registered vehicle owners.
#   AuditLog             what STAFF did to the system, kept for accountability.
#                        Deliberately not a record of owners' movements.
#   Notification         the admin bell feed (violations, registrations).
#   TwoFactorDevice      a person's enrolled authenticator app.
#   TwoFactorBackupCode  single-use recovery codes for a lost phone.
#
# A "model" here is one database table, and each attribute inside the class is
# one column. Django writes the SQL; this file says what should exist.
#
# Two ideas recur and are worth knowing before reading on:
#   * Archiving, not deleting. An expired owner is marked is_archived so their
#     history survives, which is why email is unique only among live accounts.
#   * Confiscation. A violation penalty that withdraws campus access for a
#     period, kept as an end DATE so it expires by itself.
# =============================================================================

import uuid                                              # for unguessable badge/QR secrets
from django.contrib.auth.models import AbstractUser, BaseUserManager  # Django's ready-made account plumbing
from django.db import models                             # field types, constraints and indexes
from django.db.models.functions import Upper             # used to index UPPER(email) for case-insensitive lookups


# How accounts get created. A Django "manager" is the object behind
# User.objects, so this is where User.objects.create_user(...) is defined.
# This one exists because the project signs people in by email, not username.
class UserManager(BaseUserManager):
    """Custom manager that uses email instead of username."""

    # Creates an ordinary account. Everything that must be true of a brand-new
    # user — an email, a name, a hashed password, an expiry date for owners —
    # is settled here, so no caller can skip a step.
    def create_user(self, email, full_name, password=None, **extra_fields):
        if not email:
            raise ValueError('Email is required')        # the login identifier; refuse rather than save a broken row
        if not full_name:
            raise ValueError('Full name is required')    # every screen shows this; a blank name is useless
        email = self.normalize_email(email)              # tidy the address (lower-cases the domain part)

        # Owner accounts expire a configurable time after creation (System
        # Settings). Compute the frozen expiry date once, at creation, unless a
        # caller supplied one explicitly. Admin/security accounts never expire.
        if (extra_fields.get('role') == User.Role.VEHICLE_OWNER
                and 'expires_at' not in extra_fields):
            expiry = self._owner_expiry_date()           # None when expiry is switched off
            if expiry is not None:
                extra_fields['expires_at'] = expiry      # freeze the date now, so later settings changes cannot move it

        user = self.model(email=email, full_name=full_name, **extra_fields)  # build the row in memory
        user.set_password(password)                      # store the password hashed, never as typed
        user.save(using=self._db)                        # write it to the database
        return user

    # Works out the day an owner account should expire, from the CDSO's
    # settings. Returns None when expiry is turned off, which means "never".
    @staticmethod
    def _owner_expiry_date():
        """Creation-date + configured (months, days), or None if expiry is off.

        Lazy import of SystemSettings avoids an accounts->vehicles import cycle.
        """
        try:
            from vehicles.models import SystemSettings   # imported late: accounts and vehicles refer to each other
        except Exception:
            return None                                  # settings unavailable: treat expiry as off rather than fail
        cfg = SystemSettings.get()                       # the single settings row
        if not cfg.account_expiry_enabled:
            return None                                  # the CDSO has expiry switched off
        if cfg.account_expiry_months <= 0 and cfg.account_expiry_days <= 0:
            return None                                  # a zero-length period would expire accounts immediately
        from datetime import timedelta
        from dateutil.relativedelta import relativedelta  # months are not a fixed number of days, so use calendar maths
        from django.utils import timezone
        return (timezone.localdate()                     # count from today, campus time
                + relativedelta(months=cfg.account_expiry_months)
                + timedelta(days=cfg.account_expiry_days))

    # Django calls this to find the account behind a login identifier.
    def get_by_natural_key(self, username):
        # Email is unique only among live accounts (archived rows share it), so
        # authentication must resolve to the non-archived user.
        return self.get(**{self.model.USERNAME_FIELD: username, 'is_archived': False})

    # Creates an account with full administrative rights — used by Django's
    # "createsuperuser" command and the seeding migration.
    def create_superuser(self, email, full_name, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)        # may reach Django's own admin site
        extra_fields.setdefault('is_superuser', True)    # bypasses per-permission checks
        extra_fields.setdefault('role', 'admin')         # in this system, admin means the CDSO
        return self.create_user(email, full_name, password, **extra_fields)


# Everyone who can sign in. Extends Django's AbstractUser, which already brings
# the password, is_active, is_staff and date_joined columns; what follows adds
# the fields this campus system needs on top of that.
class User(AbstractUser):
    # What someone is allowed to do. There are exactly three roles.
    class Role(models.TextChoices):
        ADMIN          = 'admin',          'CDSO'            # the office that runs the system
        SECURITY       = 'security',       'Security Personnel'   # guards on the gates
        VEHICLE_OWNER  = 'vehicle_owner',  'Registered Vehicle Owner'

    # For owners only: which kind of person they are, which decides the entry
    # rules applied at the gate (see scanning/entry_logic.py).
    class OwnerType(models.TextChoices):
        STUDENT  = 'student',  'Student'
        FETCHER  = 'fetcher',  'Fetcher/Dropper'                 # drops off or collects a student
        EMPLOYEE = 'employee', 'Employee'
        VISITOR  = 'visitor',  'Visitor'                         # enters on a gate-issued pass

    # Legacy way of recording which days an owner attends. Newer accounts store
    # campus_days instead; these codes are kept so old rows still make sense.
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
        'cdso':          'CDS',                          # retired role, kept so old codes still resolve
    }

    # The two physical gates a guard can be posted to. Kept for the older
    # fixed-gate screens; gates themselves are rows in scanning.Gate.
    class Gate(models.TextChoices):
        GATE1 = 'gate1', 'Gate 1'
        GATE4 = 'gate4', 'Gate 4'

    id = models.BigAutoField(primary_key=True, db_column='user_id')   # the row's own number; column named user_id by convention
    full_name = models.CharField(max_length=150)         # shown on every screen and in gate messages
    # Not globally unique: an archived owner keeps their email so history is
    # preserved, while a new live account may reuse it. Uniqueness among *live*
    # accounts is enforced by the partial constraint in Meta.
    email = models.EmailField(db_index=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.VEHICLE_OWNER)   # new accounts are owners unless told otherwise
    user_code = models.CharField(max_length=20, unique=True, null=True, blank=True, db_index=True)  # the readable ID, filled in by save()
    must_change_password = models.BooleanField(default=False)   # true after a staff-set temporary password
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
    agency = models.CharField(max_length=150, null=True, blank=True)   # the security agency a guard works for
    qr_token = models.UUIDField(default=uuid.uuid4, unique=True)       # identifies the account in QR codes

    # Owner profile fields — only populated for vehicle_owner role
    owner_type  = models.CharField(max_length=20, choices=OwnerType.choices, null=True, blank=True)   # drives the gate rules
    schedule    = models.CharField(max_length=10, choices=Schedule.choices, null=True, blank=True)    # legacy day code
    campus_days = models.JSONField(default=list)  # e.g. ["Monday", "Tuesday", "Wednesday"]
    contact     = models.CharField(max_length=50, null=True, blank=True)   # phone number, for the CDSO to reach them
    address     = models.TextField(null=True, blank=True)
    photo       = models.ImageField(upload_to='owners/', null=True, blank=True)   # stored under owners/ in media storage

    # Owner-account expiration (vehicle_owner only). expires_at is frozen at
    # creation from System Settings; the daily maintenance job archives the
    # account once it passes. Archiving sets is_archived + clears is_active.
    expires_at  = models.DateField(null=True, blank=True, db_index=True)   # indexed: the daily job searches on it
    is_archived = models.BooleanField(default=False, db_index=True)        # archived accounts are kept but cannot sign in
    archived_at = models.DateTimeField(null=True, blank=True)              # when archiving happened; the retention purge counts from here
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
    confiscated_at      = models.DateTimeField(null=True, blank=True)   # when the penalty started
    confiscated_until   = models.DateField(
        null=True, blank=True, db_index=True,
        help_text='Last day of the penalty, inclusive. NULL while a level is '
                  'set means indefinite (until the CDSO lifts it).',
    )
    confiscation_reason = models.TextField(blank=True, default='')      # shown to the owner in their portal

    # Security-guard QR badge secret — a UUID printed on the guard's badge as a QR code.
    # Format in QR: "SLC-GUARD:{user_code}:{guard_qr_secret}"
    guard_qr_secret = models.UUIDField(null=True, blank=True, unique=True)

    # Override username to be nullable/blank, email is used for login
    username = models.CharField(max_length=150, blank=True, null=True)

    USERNAME_FIELD = 'email'                             # tells Django to authenticate on email
    REQUIRED_FIELDS = ['full_name']  # email is already required via USERNAME_FIELD

    objects = UserManager()                              # User.objects uses the manager defined above

    # Saves the row, then gives brand-new accounts their readable code. The code
    # contains the row's number, which only exists once the row has been saved,
    # so this has to happen after the save rather than before it.
    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)                    # Django's own save: writes the row
        # Generate user_code once after pk is available
        if not self.user_code:
            prefix = self._ROLE_PREFIX.get(self.role, 'USR')      # ADM / SEC / OWN, or USR for anything unexpected
            self.user_code = f"SLC-{prefix}-{str(self.pk).zfill(6)}"   # e.g. SLC-OWN-000042
            User.objects.filter(pk=self.pk).update(user_code=self.user_code)  # direct UPDATE: avoids calling save() again

    # ── Confiscation helpers ─────────────────────────────────────────────────

    # Is this account serving a penalty right now? Worked out from the end date
    # each time it is asked, so no scheduled job is needed for it to expire.
    @property
    def is_confiscated(self) -> bool:
        """True while the account is serving a violation penalty.

        Evaluated from the stored end date rather than from a boolean that a
        scheduled job has to clear, so a one-week penalty ends on its own even
        if nothing is running. A level with no end date is indefinite.
        """
        if not self.confiscation_level:
            return False                                 # level 0 means no penalty was ever imposed
        if self.confiscated_until is None:
            return True                                  # a level with no end date: indefinite
        from django.utils import timezone as _tz
        return _tz.localdate() <= self.confiscated_until  # inclusive: the last day still counts as confiscated

    # How much longer the penalty has to run, for the message the guard reads.
    @property
    def confiscation_days_left(self):
        """Whole days remaining, or None when indefinite / not confiscated."""
        if not self.is_confiscated or self.confiscated_until is None:
            return None                                  # nothing to count down
        from django.utils import timezone as _tz
        return max(0, (self.confiscated_until - _tz.localdate()).days)   # never report a negative number

    # Lifts a penalty early, when the CDSO decides to.
    def clear_confiscation(self):
        """Lift the penalty. Leaves the violations themselves untouched — the
        offence history is what the ladder counts, and forgiving the penalty is
        not the same as saying the offences never happened."""
        self.confiscation_level  = 0                     # back to "no penalty"
        self.confiscated_at      = None
        self.confiscated_until   = None
        self.confiscation_reason = ''
        self.save(update_fields=[                        # write only these columns, leaving the rest alone
            'confiscation_level', 'confiscated_at',
            'confiscated_until', 'confiscation_reason',
        ])

    # How one account prints in admin screens and logs.
    def __str__(self):
        return f"{self.full_name} ({self.role})"

    # Database-level settings: the table name, the rules the database itself
    # enforces, and the indexes that keep the common searches fast.
    class Meta:
        db_table = 'tbl_user'
        constraints = [
            # Email is unique among live accounts only. An archived owner keeps
            # their email (history), but it no longer blocks a fresh account —
            # so an expired owner can register again with the same address.
            models.UniqueConstraint(
                fields=['email'],
                condition=models.Q(is_archived=False),   # the rule applies to live rows only
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


# Deletes accounts together with everything they own. Used both by an admin
# deleting someone and by the retention purge. The ORDER of the deletions below
# is the whole point: get it wrong and rows are left behind pointing at nobody.
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
    _, vio_counts  = Violation.objects.filter(vehicle__user__in=users).delete()   # step 1: their violations

    # Registrations first: they reference Vehicle with SET_NULL, so clearing
    # them first avoids a pointless UPDATE-to-null on rows about to be deleted.
    _, reg_counts  = VehicleRegistration.objects.filter(user__in=users).delete()  # step 2: their applications
    _, veh_counts  = Vehicle.objects.filter(user__in=users).delete()              # step 3: their vehicles
    _, user_counts = users.delete()                                               # step 4: the accounts themselves

    # Hand back how many of each kind went, so callers can report it.
    return (
        veh_counts.get('vehicles.Vehicle', 0),
        reg_counts.get('vehicles.VehicleRegistration', 0),
        user_counts.get('accounts.User', 0),
    )


# The same sweep for exactly one account, so callers do not have to build a
# queryset themselves.
def delete_user_with_owned_records(user):
    """Single-user form of :func:`delete_users_with_owned_records`.

    Returns (vehicles_deleted, registrations_deleted).
    """
    vehicles, regs, _ = delete_users_with_owned_records(
        User.objects.filter(pk=user.pk)                  # a one-row queryset, so the shared code path is reused
    )
    return vehicles, regs


# The gatekeeper for the audit trail. Every write goes through here, and rows
# that would record an ordinary owner's own activity are quietly dropped.
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

    # Writing one row.
    def create(self, **kwargs):
        if AuditLog.records_owner_activity(
            kwargs.get('action'), kwargs.get('actor'), kwargs.get('target_user')
        ):
            return None                                  # owner activity: silently not recorded
        return super().create(**kwargs)                  # staff activity: saved as normal

    # Writing many rows at once.
    def bulk_create(self, objs, *args, **kwargs):
        kept = [                                         # filter the batch before it reaches the database
            o for o in objs
            if not AuditLog.records_owner_activity(o.action, o.actor, o.target_user)
        ]
        return super().bulk_create(kept, *args, **kwargs)


# The record of staff actions. Read by the Audit Log screen and exported in
# reports; what may and may not be written here is a privacy decision.
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

    # The kinds of event that may be recorded. The stored value is the short
    # one; the second is the label people read on screen.
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
    actor       = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='audit_logs')   # who did it; kept as blank if the account is later deleted
    action      = models.CharField(max_length=30, choices=Action.choices)    # what kind of thing they did
    target_user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='target_logs')  # whose account it was done to, when that applies
    details     = models.TextField(blank=True)           # the human-readable sentence shown in the log
    ip_address  = models.GenericIPAddressField(null=True, blank=True)   # where the action came from
    created_at  = models.DateTimeField(auto_now_add=True)   # stamped automatically when the row is written

    # Actions an account holder performs on their own account. The person who
    # acted is the target_user, so these are owner activity whenever that user
    # is an owner — including the rows written before this rule existed, whose
    # actor was never captured and reads as 'System'.
    SELF_SERVICE_ACTIONS = frozenset({
        'twofa_enabled', 'twofa_disabled', 'twofa_failed', 'twofa_backup_used',
    })

    objects = AuditLogManager()                          # all writes pass the privacy filter above

    # The privacy test itself, kept next to the data it judges so both the
    # manager and any future caller apply exactly the same rule.
    @staticmethod
    def records_owner_activity(action, actor, target_user):
        """True when this row would record what a vehicle owner did.

        Two shapes count. The plain one is an owner as the actor. The other is
        a self-service action with no actor recorded: nobody else can perform
        those, so the target is the one who acted.
        """
        def _is_owner(u):
            return u is not None and getattr(u, 'role', None) == User.Role.VEHICLE_OWNER   # getattr: tolerate a missing role

        if _is_owner(actor):
            return True                                  # an owner acting on their own account
        return actor is None and action in AuditLog.SELF_SERVICE_ACTIONS and _is_owner(target_user)

    class Meta:
        db_table = 'tbl_audit_log'
        ordering = ['-created_at']                       # newest first wherever the log is listed
        indexes = [
            # Meta.ordering + the date-range filters on the Audit Log screen.
            models.Index(fields=['-created_at'], name='auditlog_created_at'),
            # Action filter, and the dashboard's per-actor-role recent lists.
            models.Index(fields=['action', '-created_at'], name='auditlog_action_time'),
            models.Index(fields=['actor', '-created_at'], name='auditlog_actor_time'),
        ]

    # One log line in plain text, e.g. for Django's admin site.
    def __str__(self):
        actor_name = self.actor.full_name if self.actor else 'Unknown'   # the account may since have been deleted
        return f"{actor_name} - {self.get_action_display()} - {self.created_at}"


# One item in the admin's notification bell.
class Notification(models.Model):
    """Admin notification-bell feed — important system events around
    violations and vehicle registration. Rows are created by signal handlers
    (see accounts/notifications.py), never directly by request handlers."""

    # Which part of the system the notice is about.
    class Category(models.TextChoices):
        VIOLATION    = 'violation',    'Violation'
        REGISTRATION = 'registration', 'Registration'

    # How much attention it deserves; the bell colours items by this.
    class Severity(models.TextChoices):
        INFO     = 'info',     'Info'
        WARNING  = 'warning',  'Warning'
        CRITICAL = 'critical', 'Critical'

    id           = models.BigAutoField(primary_key=True, db_column='notification_id')
    category     = models.CharField(max_length=20, choices=Category.choices)
    event        = models.CharField(max_length=40, blank=True)  # slug, e.g. 'violation_issued'
    severity     = models.CharField(max_length=10, choices=Severity.choices, default=Severity.INFO)
    title        = models.CharField(max_length=200)      # the one-line headline in the bell
    message      = models.TextField(blank=True)          # the longer explanation, when there is one
    plate_number = models.CharField(max_length=20, blank=True)   # so the notice can name the vehicle
    link         = models.CharField(max_length=200, blank=True)  # frontend route, e.g. '/admin/violations'
    is_read      = models.BooleanField(default=False)    # drives the unread count on the bell
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tbl_notification'
        ordering = ['-created_at']                       # newest notice first
        indexes = [
            # The bell feed: newest first, and the unread badge count.
            models.Index(fields=['-created_at'], name='notification_created_at'),
            models.Index(fields=['is_read', '-created_at'], name='notification_unread'),
        ]

    def __str__(self):
        return f"[{self.category}] {self.title}"

# The authenticator app someone has enrolled for two-factor sign-in.
class TwoFactorDevice(models.Model):
    """A user's enrolled TOTP authenticator (Google Authenticator and friends).

    One per account. The row exists from the moment enrollment starts, but
    `confirmed_at` stays NULL until the person proves they can read a code off
    the app — so an abandoned setup never locks anyone out of their own account.

    Guards never get a row here; see accounts.twofa.TWO_FACTOR_ROLES for why.
    """

    id = models.BigAutoField(primary_key=True, db_column='two_factor_device_id')
    user = models.OneToOneField(                         # one device per account; deleted with the account
        User, on_delete=models.CASCADE, related_name='twofa_device',
    )
    # Base32 TOTP secret. Stored in the clear, as it must be to compute codes —
    # the database is the trust boundary, the same one the password hashes and
    # the guard QR secrets already sit behind.
    secret = models.CharField(max_length=64)
    confirmed_at = models.DateTimeField(null=True, blank=True)   # NULL until the first correct code proves setup worked
    # Highest TOTP timestep already spent, so a code cannot be replayed inside
    # its validity window. See twofa.verify_code.
    last_used_step = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    last_verified_at = models.DateTimeField(null=True, blank=True)   # used to decide when to ask again

    class Meta:
        db_table = 'tbl_two_factor_device'

    # Shorthand for "setup was completed", used wherever a code is demanded.
    @property
    def is_confirmed(self) -> bool:
        return self.confirmed_at is not None

    def __str__(self):
        state = 'confirmed' if self.is_confirmed else 'pending'
        return f"2FA device for {self.user.email} ({state})"


# A single recovery code, for when the authenticator app is gone.
class TwoFactorBackupCode(models.Model):
    """One single-use recovery code, stored only as a hash.

    These are what stand between a lost phone and an unrecoverable system: the
    CDSO account can hold the only admin login, and a wiped authenticator with
    no way back would take the whole administration surface with it.
    """

    id = models.BigAutoField(primary_key=True, db_column='two_factor_backup_code_id')
    user = models.ForeignKey(                            # several codes per account
        User, on_delete=models.CASCADE, related_name='twofa_backup_codes',
    )
    code_hash = models.CharField(max_length=64, db_index=True)   # only the hash is kept, so a database read cannot reveal the codes
    used_at = models.DateTimeField(null=True, blank=True)        # set the moment it is spent; a code works once
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

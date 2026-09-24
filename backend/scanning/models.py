# =============================================================================
# WHAT THIS FILE IS FOR
#
# These are the database tables for everything that happens AT the gate:
#
#   Gate                   a campus entry gate (gate1, gate4, or ones added later)
#   Office                 a campus office a visitor can be visiting
#   VisitorPass            a day pass issued at the gate, and its printed slip
#   AccessLog              one row per scan: who, when, which gate, what was decided
#   GuardShift             a guard clocking in and out of a gate
#   MLTrainingSample       a captured image kept for improving the plate model
#   PlateRecognitionRecord raw output from the plate-reading pipeline
#
# Two helper functions live here too, next to the table they work on:
#   active_guard_for_gate  who is on duty at a gate right now
#   open_shift_for         start a shift, closing whatever it displaces
#
# AccessLog is the important one. It is the campus movement record, which the
# entries screens, the dashboards and the reports all read, and it is written on
# every scan — so its save() below fills in the two things a caller can forget.
# =============================================================================

from django.db import models
from django.utils import timezone            # used for shift timestamps and the pass's valid_date default
from vehicles.models import Vehicle          # a scan is always about a vehicle row (or none, for a plate we don't know)

# Which days a schedule covers lives in entry_logic (_SCHEDULE_DAYS_FALLBACK),
# and the rotations themselves in vehicles/campus_days.py. An unused copy sat
# here through the TTHS → TTHF change still claiming Saturday, which is how
# three disagreeing definitions of a campus day happened the first time.

# One gate. Guards clock in at one, cameras are assigned to one, and every scan
# records which one it happened at.
class Gate(models.Model):
    """A campus entry gate. Seeded with gate1/gate4; admins can add more from
    System Settings as the school expands. gate_id is the stable slug stored
    on shifts, access logs and camera assignments (e.g. 'gate2')."""
    id         = models.BigAutoField(primary_key=True, db_column='gate_pk')
    gate_id    = models.SlugField(max_length=20, unique=True)   # the short name everything else stores, e.g. 'gate1'
    label      = models.CharField(max_length=100)               # how it reads on screen, e.g. 'Gate 1'
    is_active  = models.BooleanField(default=True)              # retire a gate without deleting its history
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tbl_gate'
        ordering = ['gate_id']

    def __str__(self):
        return f"{self.label} ({self.gate_id})"

    # The gates a guard may sign in at. The fallback matters: an empty table
    # would otherwise leave nobody able to start a shift.
    @classmethod
    def active_ids(cls):
        """Slugs of active gates; falls back to the two founding gates so login
        never locks out if the table is empty."""
        ids = list(cls.objects.filter(is_active=True).values_list('gate_id', flat=True))   # just the slugs, not whole rows
        return ids or ['gate1', 'gate4']


# A campus office, so a visitor pass can record who is being visited.
class Office(models.Model):
    id      = models.BigAutoField(primary_key=True, db_column='office_id')
    name    = models.CharField(max_length=100)
    contact = models.CharField(max_length=50, blank=True)   # phone, for the guard to confirm an unexpected visitor
    email   = models.EmailField(blank=True)

    def __str__(self):
        return self.name

    class Meta:
        db_table = 'tbl_office'


# A day pass issued at the gate to someone with no registered vehicle. The
# printed slip carries a QR code that is scanned again on the way out.
class VisitorPass(models.Model):
    class Status(models.TextChoices):
        ACTIVE  = 'active',  'Active'   # pass issued, visitor is inside
        EXITED  = 'exited',  'Exited'   # guard scanned exit, visitor has left
        EXPIRED = 'expired', 'Expired'  # valid_date passed without exit scan

    id         = models.BigAutoField(primary_key=True, db_column='visitor_pass_id')
    vehicle    = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name='visitor_passes')   # gate-created row for this car
    plate_number = models.CharField(max_length=20, blank=True)      # denormalised for quick display
    # Printed on the slip, and what a guard can type at the exit instead of
    # scanning the slip QR. Blank on passes issued before the field existed.
    visitor_name = models.CharField(max_length=150, blank=True, default='')
    # Optional: the sticker number on a new car that has one. Kept on the pass
    # rather than the gate-created Vehicle, whose conduction_number is unique
    # across registered vehicles. Copied onto any violation the visit earns, and
    # one of the identifiers a visitor penalty is matched on.
    conduction_number = models.CharField(max_length=50, blank=True, default='', db_index=True)
    office     = models.ForeignKey(
        Office, on_delete=models.SET_NULL, null=True, blank=True,   # office being visited (optional)
    )
    purpose    = models.TextField(blank=True)
    status     = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    issued_by  = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL,     # keep the pass if the guard's account is removed
        null=True, blank=True, related_name='issued_passes',
    )
    allowed_duration = models.PositiveIntegerField(default=60, help_text="Allowed time inside in minutes")
    valid_date = models.DateField(default=timezone.now)   # a pass is for one day; Django converts this datetime to the campus-local date on save
    entered_at = models.DateTimeField(auto_now_add=True)
    # Set when the guard confirms the thermal slip was printed — the visitor's
    # entry is only logged in the AccessLog at that moment.
    printed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)   # entry time plus allowed_duration, when one applies
    exited_at  = models.DateTimeField(null=True, blank=True)
    # Serial of the most recently printed slip, part of its QR. Every print —
    # first print or reprint, thermal or browser — draws a new one, so no two
    # paper slips share a code and only the newest copy opens or exits. Blank
    # until the first print (and on passes printed before serials existed,
    # whose plain SLC-VISITOR:{id} code keeps working).
    slip_token = models.CharField(max_length=16, blank=True, default='')

    class Meta:
        db_table = 'tbl_visitor_pass'
        indexes = [
            # Dashboard: passes still active for today.
            models.Index(fields=['status', 'valid_date'],
                         name='visitorpass_status_date'),
        ]

    # The text encoded in the slip's QR code.
    @property
    def qr_payload(self):
        """Encoded in the QR printed on the slip; scanned at the gate to record exit.
        Carries the current slip serial, so it names the newest printed copy."""
        return f'SLC-VISITOR:{self.pk}-{self.slip_token}' if self.slip_token else f'SLC-VISITOR:{self.pk}'

    def __str__(self):
        office_name = self.office.name if self.office else 'No office'   # the office may have been deleted
        return f"{self.plate_number} → {office_name} ({self.status})"


# One scan at a gate: the campus movement record. Written on every entry, every
# exit and every refusal, and read by the entries screens, dashboards and reports.
class AccessLog(models.Model):
    # What was decided about this scan.
    class Status(models.TextChoices):
        AUTHORIZED   = 'authorized',    'Authorized'
        DENIED       = 'denied',        'Denied'
        WRONG_DAY    = 'wrong_day',     'Wrong Day'      # refused because of the day, not the person
        UNKNOWN      = 'unknown',       'Unknown Plate'  # no registration matches
        UNREADABLE   = 'unreadable',    'Unreadable'     # the camera could not make out a plate
        EXITED       = 'exited',        'Exited'         # the matching exit for an earlier entry

    class Category(models.TextChoices):
        """Who is coming in, as opposed to what decision was made about them.

        A second axis from `status` on purpose: a student can be authorized or
        denied, and a report that asks "how many students entered today" cannot
        be answered from status alone. Stored on the row rather than derived at
        read time because an owner's type can change later (a student graduates
        into an employee) and last term's log must keep saying "student".
        """
        STUDENT  = 'student',  'Student'
        EMPLOYEE = 'employee', 'Employee'
        FETCHER  = 'fetcher',  'Fetcher / Drop & Go'
        VISITOR  = 'visitor',  'Visitor'
        SUPPLIER = 'supplier', 'Supplier'
        EVENT    = 'event',    'Event Organizer'
        UNKNOWN  = 'unknown',  'Unregistered'

    id             = models.BigAutoField(primary_key=True, db_column='access_log_id')
    vehicle        = models.ForeignKey(Vehicle, on_delete=models.SET_NULL, null=True, blank=True)   # null for a plate with no record
    plate_number   = models.CharField(max_length=20, blank=True)   # copied onto the row so the log survives the vehicle being deleted
    vehicle_type   = models.CharField(max_length=20, blank=True)
    # Blank means "never classified" (rows written before this field existed);
    # the serializer falls back to deriving it so old rows still read sensibly.
    entrant_category = models.CharField(
        max_length=20, choices=Category.choices, blank=True, default='',
    )
    # ── Unrecognized vehicle (no plate the system could use) ──────────
    # A vehicle with no plate and no conduction sticker still drives onto
    # campus, and before this it left no record at all. The guard records it by
    # hand; these fields are the description that stands in for a plate.
    is_unrecognized = models.BooleanField(default=False)
    driver_name     = models.CharField(max_length=255, blank=True)
    vehicle_color   = models.CharField(max_length=50, blank=True)
    vehicle_model   = models.CharField(max_length=100, blank=True)
    entry_note      = models.CharField(max_length=255, blank=True)
    digital_id_used = models.CharField(max_length=50, blank=True)   # the ID the driver showed instead of a plate
    status         = models.CharField(max_length=20, choices=Status.choices)
    gate_id         = models.CharField(max_length=50, default='main')   # which gate; 'main' is the fallback for an unattributed scan
    denied_reason   = models.CharField(max_length=255, blank=True)      # the sentence shown to the guard when refused
    is_override     = models.BooleanField(default=False)                # a guard let them in despite the decision
    override_reason = models.CharField(max_length=255, blank=True)      # why, so the override is accountable
    paired_entry    = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL,       # 'self': an exit row points at its own entry row
        related_name='exit_log',
        help_text="For exit logs: points to the matching entry log.",
    )
    # The event an unregistered organizer plate was admitted for. Kept on the
    # row, not re-derived from the event's plate list, so the slip still says
    # which event it was after the list is edited — and SET_NULL so deleting
    # an event never deletes the record that its organizers came and went.
    event          = models.ForeignKey(
        'vehicles.Event', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='access_logs',
    )
    snapshot       = models.ImageField(upload_to='snapshots/', blank=True)   # the frame the camera captured, when there is one
    scanned_at     = models.DateTimeField(auto_now_add=True)
    scanned_by     = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='scans',
    )
    on_duty_guard  = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='on_duty_scans',
        help_text="Guard clocked in at this gate when the scan happened.",
    )

    class Meta:
        db_table = 'tbl_access_log'
        ordering = ['-scanned_at']                   # newest scan first, everywhere
        indexes = [
            # The scan hot path: _inside_state / _already_inside / _pair_entry_exit
            # and the three cooldown checks all ask
            # "(plate, status) within a time window, newest first".
            models.Index(fields=['plate_number', 'status', '-scanned_at'],
                         name='accesslog_plate_status_time'),
            # Meta.ordering — every unfiltered list page sorts by this.
            models.Index(fields=['-scanned_at'], name='accesslog_scanned_at'),
            # Dashboard: per-status counts over today / the past week.
            models.Index(fields=['status', '-scanned_at'],
                         name='accesslog_status_time'),
            # Security dashboard + per-guard stats ("my scans today").
            models.Index(fields=['scanned_by', '-scanned_at'],
                         name='accesslog_scanned_by_time'),
            # Per-vehicle entry history.
            models.Index(fields=['vehicle', '-scanned_at'],
                         name='accesslog_vehicle_time'),
            # Gate filtering on the entries/operations screens.
            models.Index(fields=['gate_id', '-scanned_at'],
                         name='accesslog_gate_time'),
            # Entry Management's per-category breakdown and its filter chips.
            models.Index(fields=['entrant_category', '-scanned_at'],
                         name='accesslog_category_time'),
        ]

    # Fills in the two things every scan needs but a caller can forget: who was
    # on duty, and which category of entrant this was. Both only on creation —
    # editing an old row must not rewrite history.
    def save(self, *args, **kwargs):
        # scanned_by records whose session triggered the scan (may be an admin
        # watching a camera feed); on_duty_guard records who was clocked in at
        # the gate at that moment.
        if self._state.adding and self.on_duty_guard_id is None and self.gate_id:   # _state.adding is True only for a new row
            self.on_duty_guard = active_guard_for_gate(self.gate_id)
        # Classify here rather than at each of the dozen create() call sites, so
        # a new scan path cannot forget to and quietly write uncategorised rows.
        # A caller that already knows the category (an unrecognized vehicle the
        # guard typed in by hand) sets it and this leaves it alone.
        if self._state.adding and not self.entrant_category:
            from .entry_logic import classify_entrant   # imported here to avoid a circular import
            try:
                self.entrant_category = classify_entrant(self.vehicle, self.plate_number)
            except Exception:
                # A scan must still be recorded when classification fails; a
                # blank category reads as "unclassified", a lost log reads as
                # a car that was never at the gate.
                self.entrant_category = ''
        super().save(*args, **kwargs)                 # then save as normal


# One guard's spell of duty at one gate: when they clocked in, and when (and by
# whom) it was ended.
class GuardShift(models.Model):
    id = models.BigAutoField(primary_key=True, db_column='guard_shift_id')
    guard = models.ForeignKey(
        'accounts.User', on_delete=models.CASCADE, related_name='shifts',   # shifts go with the guard's account
    )
    gate = models.CharField(max_length=10)  # 'gate1' or 'gate4'
    # (That comment predates gates becoming editable: gates are now rows in the
    # Gate model above, so this slug can also be one an admin added later.)
    clocked_in_at = models.DateTimeField(auto_now_add=True)
    clocked_out_at = models.DateTimeField(null=True, blank=True)   # NULL means the shift is still open
    clocked_out_by = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='clocked_out_shifts',  # who ended it: themselves, or the guard relieving them
    )

    class Meta:
        db_table = 'tbl_guard_shift'
        ordering = ['-clocked_in_at']
        indexes = [
            # "shifts for this guard today" and the open-shift lookup.
            models.Index(fields=['guard', '-clocked_in_at'],
                         name='guardshift_guard_time'),
            models.Index(fields=['gate', '-clocked_in_at'],
                         name='guardshift_gate_time'),
            # Finding the currently-active shift (clocked_out_at IS NULL).
            models.Index(fields=['clocked_out_at'], name='guardshift_clocked_out'),
        ]

    def __str__(self):
        return f"{self.guard.full_name} @ {self.gate} — {self.clocked_in_at.strftime('%Y-%m-%d %H:%M')}"


# Who is on duty at a gate right now. Used by AccessLog.save() to attribute
# each scan to the guard actually standing there.
def active_guard_for_gate(gate: str):
    """The guard currently clocked in at this gate, or None."""
    if not gate:
        return None                                  # no gate given: nobody to attribute to
    shift = (
        GuardShift.objects
        .filter(gate=gate, clocked_out_at__isnull=True)   # open shifts at this gate
        .select_related('guard')                          # fetch the guard in the same query
        .order_by('-clocked_in_at')                       # newest first, in case two are open
        .first()
    )
    return shift.guard if shift else None


# Starts a guard's shift, and closes the shifts that starting it displaces.
def open_shift_for(guard, gate, *, now=None):
    """Start `guard`'s shift at `gate`, closing whatever that displaces.

    Returns `(shift, displaced_gates)` — the new shift, and the gates this
    guard was signed out of to open it.

    Two kinds of open shift get closed, and the second is the one the three
    login paths all used to miss:

      * whoever else was still clocked in AT THIS GATE. That is the guard being
        relieved, and closing their shift is what "handing over" means.

      * this guard's OWN open shift at ANY OTHER GATE. A guard is one person
        and can only stand at one gate, so an open shift elsewhere is a session
        they walked away from without signing out. Leaving it open put the same
        name on duty at two gates at once — the Operations Center showed it
        faithfully, one of them running for weeks — and, worse, every scan the
        abandoned gate attributed by `gate_assignment` was credited to someone
        who was not there.

    `clocked_out_by` is the guard themselves for their own stale session: they
    are the one whose sign-in ended it, and naming a different guard there
    would read as though somebody else had signed them out.
    """
    now = now or timezone.now()                      # one timestamp for every change made here

    relieved = list(                                 # NOTE: computed, but not used below or returned
        GuardShift.objects
        .filter(gate=gate, clocked_out_at__isnull=True)
        .exclude(guard=guard)                        # anyone else still open at this gate
        .values_list('gate', flat=True)
    )
    GuardShift.objects.filter(gate=gate, clocked_out_at__isnull=True).update(
        clocked_out_at=now, clocked_out_by=guard,    # close them in one statement: the hand-over
    )

    # The same guard, still open somewhere else.
    displaced = list(
        GuardShift.objects
        .filter(guard=guard, clocked_out_at__isnull=True)
        .exclude(gate=gate)                          # ...at any gate but this one
        .values_list('gate', flat=True)              # collect the gate names before closing them
    )
    if displaced:
        GuardShift.objects.filter(
            guard=guard, clocked_out_at__isnull=True,
        ).exclude(gate=gate).update(clocked_out_at=now, clocked_out_by=guard)   # close the abandoned sessions

    shift = GuardShift.objects.create(guard=guard, gate=gate)   # finally, open the new shift
    # De-duplicated but order-stable, so a guard abandoned at two gates reads
    # in the order the gates are named rather than at random.
    seen, ordered = set(), []
    for g in displaced:
        if g not in seen:                            # skip a gate already listed
            seen.add(g)
            ordered.append(g)
    return shift, ordered                            # the caller tells the guard which gates they were signed out of


# A captured image kept so the plate-reading model can be improved later.
class MLTrainingSample(models.Model):
    # Where the image came from.
    SOURCE_CHOICES = [
        ('scan',         'Live Scan'),               # captured at a gate during normal use
        ('manual',       'Manual Label'),            # added and labelled by hand
        ('imported',     'Dataset Import'),          # brought in from an outside dataset
    ]

    # How far along the labelling is.
    STATUS_CHOICES = [
        ('unlabeled',    'Unlabeled'),
        ('auto_labeled', 'Auto-Labeled'),            # the model guessed; a person has not checked
        ('verified',     'Verified'),                # a person confirmed it
        ('rejected',     'Rejected'),                # unusable, kept so it is not re-imported
    ]

    id = models.BigAutoField(primary_key=True, db_column='ml_training_sample_id')
    image = models.ImageField(upload_to='ml_samples/')
    plate_number = models.CharField(max_length=20, blank=True)   # the text, once known
    bbox = models.JSONField(default=dict, blank=True)            # where in the image the plate sits
    confidence = models.FloatField(null=True, blank=True)        # how sure the model was
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='scan')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='unlabeled')
    used_in_training = models.BooleanField(default=False)        # so a sample is not trained on twice
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tbl_ml_training_sample'
        ordering = ['-created_at']
        # 10k+ rows and growing with every scan — the review queue filters by
        # status and always sorts newest-first.
        indexes = [
            models.Index(fields=['-created_at'], name='mlsample_created_at'),
            models.Index(fields=['status', '-created_at'], name='mlsample_status_time'),
        ]

    def __str__(self):
        return f"[{self.status}] {self.plate_number or '?'} ({self.source})"


# Raw output from the plate-reading pipeline: one row per plate read off a
# tracked vehicle, kept for diagnosis rather than for the gate decision.
class PlateRecognitionRecord(models.Model):
    id = models.BigAutoField(primary_key=True, db_column='plate_recognition_record_id')
    track_id = models.IntegerField(db_index=True)                # which followed vehicle this reading belongs to
    plate_text = models.CharField(max_length=20, db_index=True)  # what the reader made of it
    detection_confidence = models.FloatField()                   # how sure it was a plate
    ocr_confidence = models.FloatField()                         # how sure it read the characters correctly
    timestamp = models.DateTimeField(db_index=True)
    snapshot_path = models.CharField(max_length=255, blank=True) # where the captured frame was written

    class Meta:
        db_table = 'tbl_plate_recognition_record'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['plate_text', '-timestamp'], name='plate_text_timestamp_idx'),   # history for one plate
            models.Index(fields=['track_id'], name='track_id_idx'),                               # all readings of one vehicle
        ]

    def __str__(self):
        return f"Track {self.track_id}: {self.plate_text} ({self.timestamp})"

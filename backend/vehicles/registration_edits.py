"""What a person may change about their own vehicle registration, and how a
proposed change is checked before it lands.

Two flows share this module, and they differ only in *when* the change takes
effect:

  * An application still PENDING is edited in place. CDSO has not reviewed it
    yet, so a detail the applicant mistyped is still theirs to fix — see
    `RegistrationSelfEditView`.
  * An ACCEPTED registration is not. The row behind it issued a vehicle pass, a
    gate QR, a Vehicle record and a portal account, so the owner's edit is
    filed as a `RegistrationChangeRequest` and applied only once CDSO approves
    it — see `OwnerChangeRequestView` and `ChangeRequestDecisionView`.

Both flows call `clean_changes` against the same `EDITABLE_FIELDS` whitelist, so
they cannot drift into accepting different things — and neither can reach a
field the review process owns (status, payment, system IDs, campus days), which
is the whole reason the whitelist is a whitelist rather than an exclusion list.
"""
import re

from .control_numbers import is_control_number, is_ebike
from .models import ReferenceItem, VehicleRegistration, _normalize_plate


# ── Field vocabularies, mirrored from the registration form ──
# The form's <select> options are the authority on what a person can pick; a
# hand-rolled POST must not be able to store a vehicle type the rest of the
# system has never heard of. Kept as frozensets of the exact stored values.
VEHICLE_TYPES = frozenset({
    'Sedan', 'SUV', 'Motorcycle', 'Tricycle', 'E-Bike', 'Van', 'Truck', 'Other',
})

# Only this type carries a body number; see `apply_changes` for the clearing
# rule that keeps a leftover number off a vehicle that no longer has one.
BODY_NUMBER_TYPE = 'Tricycle'

# LTO licence format — one office letter, a 2-digit district, a 2-digit year and
# a 6-digit serial. The same rule the form's `drivers_license` pattern applies,
# restated here because the form is not the only thing that can POST.
LICENSE_RE = re.compile(r'^[A-Z]\d{2}-\d{2}-\d{6}$')

CONDUCTION_RE = re.compile(r'^[A-Z0-9]{5,12}$')


def _text(value):
    """A submitted scalar as clean text. Lists come from form-encoded posts."""
    if isinstance(value, list):
        value = value[0] if value else ''
    if value is None:
        return ''
    return str(value).strip()


# ── Per-field cleaners ──
# Each returns (cleaned_value, error). A cleaner never looks at other fields:
# cross-field rules (either/or plate, the authorized driver, uniqueness) live in
# `clean_changes`, which can see the whole proposal and the row it applies to.

def _clean_full_name(value, registration):
    name = _text(value).upper()          # the form's naming convention
    if not name:
        return None, 'Your full name cannot be blank.'
    if len(name) < 2:
        return None, 'Enter your full name.'
    return name, None


def _clean_license(value, registration):
    lic = _text(value).upper()
    if not lic:
        return None, "A driver's license number is required."
    if not LICENSE_RE.match(lic):
        return None, "Invalid LTO license number. Use the format A00-00-000000."
    return lic, None


def _clean_program_year(value, registration):
    text = _text(value)
    if not text:
        return None, 'Program and year cannot be blank.'
    return text, None


def _clean_department(value, registration):
    """Stores the label, and resolves the fee-bearing `department_type` with it.

    The column the fee reads is `department_type`, not this one, so returning
    the label alone would let an employee move to Cleaning and Services (which
    is fee-exempt) without the exemption following — or, worse, off it while
    still counting as exempt. `apply_changes` writes both together.
    """
    label = _text(value)
    if not label:
        return None, 'A department is required.'
    known = {lbl for _, lbl in VehicleRegistration.DepartmentType.choices}
    if label not in known:
        return None, 'Choose one of the listed departments.'
    return label, None


def _clean_vehicle_type(value, registration):
    vtype = _text(value)
    if vtype not in VEHICLE_TYPES:
        return None, 'Choose one of the listed vehicle types.'
    # Becoming an e-bike means trading the plate for a system-issued control
    # number, which an edit cannot do. (An e-bike is never offered this field.)
    if is_ebike(vtype) and not is_ebike(registration.vehicle_type):
        return None, ('E-Bikes are registered with a control number issued at '
                      'application — please contact the CDSO Office.')
    return vtype, None


def _clean_vehicle_color(value, registration):
    color = _text(value).upper()
    if not color:
        return None, 'A vehicle colour is required.'
    return color, None


def _clean_body_number(value, registration):
    # Blank is legitimate here, unlike the fields above: only a tricycle has a
    # body number, so clearing it is how someone corrects a value that was
    # never theirs to give.
    return _text(value).upper(), None


def _clean_plate(value, registration):
    plate = _normalize_plate(_text(value))
    if not plate:
        return None, 'A plate number is required.'
    # Imported here, not at module scope: scanning.ml pulls in the detector
    # stack, and this module is imported by vehicles.models' readers.
    from scanning.ml.validator import is_valid_ph_plate
    if not is_valid_ph_plate(plate):
        return None, 'Enter a valid Philippine plate number.'
    return plate, None


def _clean_conduction(value, registration):
    conduction = _normalize_plate(_text(value))
    if not conduction:
        return None, 'A conduction number is required.'
    if not CONDUCTION_RE.match(conduction):
        return None, 'Invalid conduction number. Use 5-12 letters or digits.'
    return conduction, None


def _clean_driver_name(value, registration):
    # Blank clears the authorized driver entirely — see the cross-field rule in
    # `clean_changes`, which refuses that for a student who may not self-drive.
    return _text(value).upper(), None


def _clean_driver_relationship(value, registration):
    rel = _text(value)
    if not rel:
        return '', None
    valid = {v for v, _ in VehicleRegistration.DriverRelationship.choices}
    if rel not in valid:
        return None, "Choose the driver's relationship from the list."
    return rel, None


def _student_only(registration):
    return registration.registrant_type == VehicleRegistration.RegistrantType.STUDENT


def _employee_only(registration):
    return registration.registrant_type == VehicleRegistration.RegistrantType.EMPLOYEE


def _always(registration):
    return True


class Field:
    """One editable field: what to call it, who may edit it, how to clean it.

    `applies_to` is checked against the registration rather than hard-coded per
    flow, because the answer depends on the row: `program_year` is a real field
    for a student and meaningless for a fetcher, and a fetcher must not be able
    to acquire one by POSTing it.
    """
    __slots__ = ('name', 'label', 'clean', 'applies_to')

    def __init__(self, name, label, clean, applies_to=_always):
        self.name = name
        self.label = label
        self.clean = clean
        self.applies_to = applies_to


EDITABLE_FIELDS = {
    f.name: f for f in (
        Field('full_name',           'Full Name',                _clean_full_name),
        Field('drivers_license',     "Driver's License",         _clean_license),
        Field('program_year',        'Program & Year',           _clean_program_year, _student_only),
        Field('department',          'Department',               _clean_department,   _employee_only),
        Field('driver_name',         'Authorized Driver',        _clean_driver_name,  _student_only),
        Field('driver_relationship', "Driver's Relationship",    _clean_driver_relationship, _student_only),
        Field('vehicle_type',        'Vehicle Type',             _clean_vehicle_type),
        Field('vehicle_color',       'Vehicle Colour',           _clean_vehicle_color),
        Field('body_number',         'Body Number',              _clean_body_number),
        Field('plate_number',        'Plate Number',             _clean_plate),
        Field('conduction_number',   'Conduction Number',        _clean_conduction),
    )
}

# Deliberately NOT editable, and why — so the next person to be asked for one of
# these has the reasoning rather than just the absence:
#
#   email            The account's login and the address every notice about this
#                    application is sent to. Changing it from a link that was
#                    itself emailed to that address is circular; CDSO does it.
#   registrant_type  Decides which half of the form applies, which email rule
#                    the address is judged by and which system ID was minted.
#                    A student becoming an employee is a new application.
#   student_level    Same: it drives the email rule and the schedule rules.
#   campus_days      CDSO allocates these against a per-day slot limit at
#   schedule         approval time. Self-service would let an owner take a slot
#                    the limit had already given to someone else.
#   fetcher_students Editable in principle, but it is a list needing its own
#                    editor in both flows; out of scope here, done by CDSO.
#   or_number        Payment evidence. Filed once, through the receipt step.
#   status           The review decision itself.
READ_ONLY_REASONS = {
    'email':           'Your email address is your login — please contact the CDSO Office to change it.',
    'registrant_type': 'Changing registrant type means filing a new application.',
    'student_level':   'Please contact the CDSO Office to change your education level.',
    'campus_days':     'Your campus days are assigned by the CDSO Office.',
    'schedule':        'Your schedule is assigned by the CDSO Office.',
}

# An e-bike's identity is its system-issued control number (stored in
# plate_number, see control_numbers). Neither it nor the type that earned it may
# be edited; swapping either would leave a plate-less car holding an FM- number.
EBIKE_LOCKED_FIELDS = frozenset({'plate_number', 'conduction_number', 'vehicle_type'})
EBIKE_LOCKED_REASON = 'Your E-Bike control number is issued by the system and cannot be changed.'


def holds_control_number(registration):
    """An e-bike carrying a system-issued FM- number. E-bikes registered before
    control numbers existed have a real plate and stay editable as before."""
    return is_ebike(registration.vehicle_type) and is_control_number(registration.plate_number)


def editable_for(registration):
    """The fields this particular registration's holder may edit.

    Both identifier fields are offered only when they are the one in use: a
    registration carries either a plate or a conduction number, never both
    (enforced on submission), and offering the empty one as editable is how a
    row would end up holding two.
    """
    fields = []
    locked = holds_control_number(registration)
    for field in EDITABLE_FIELDS.values():
        if not field.applies_to(registration):
            continue
        if locked and field.name in EBIKE_LOCKED_FIELDS:
            continue
        if field.name == 'plate_number' and not registration.plate_number:
            continue
        if field.name == 'conduction_number' and not registration.conduction_number:
            continue
        if field.name == 'body_number' and registration.vehicle_type != BODY_NUMBER_TYPE:
            # Shown only for the type that has one. A change of type reveals or
            # clears it on the next load.
            continue
        fields.append(field)
    return fields


def current_values(registration):
    """What the editable fields hold right now, keyed as the client sends them.

    `department` reads the label the form shows, not the FK: the column is a
    ReferenceItem for the programme list and a plain choice label for the
    department type, and the edit form only ever deals in labels.
    """
    values = {}
    for field in editable_for(registration):
        if field.name == 'department':
            values[field.name] = registration.get_department_type_display() \
                if registration.department_type else ''
        else:
            values[field.name] = getattr(registration, field.name) or ''
    return values


def _active_registrations(exclude_pk):
    """Rows that hold a plate/licence against re-use, minus the one being edited.

    EXPIRED is excluded by `Status` design — an auto-archived account releases
    its plate — so "active" is pending plus accepted, exactly as the submission
    checks define it.
    """
    return (VehicleRegistration.objects
            .filter(status__in=[VehicleRegistration.Status.PENDING,
                                VehicleRegistration.Status.ACCEPTED])
            .exclude(pk=exclude_pk))


def clean_changes(registration, raw):
    """Validate a proposed set of changes against `registration`.

    Returns `(changes, errors)`:

      * `changes` maps field name to its cleaned value, and holds only fields
        that genuinely differ from what the row already has. A no-op edit is
        not an error — it is simply nothing to do, and both callers treat an
        empty dict as "nothing changed" rather than as a failure.
      * `errors` maps field name to a message the person can act on, plus the
        key `'__all__'` for cross-field problems.

    Uniqueness is re-checked here every time, which is what makes this safe to
    call twice: the owner flow validates once when the request is filed and
    again when CDSO approves it, and a plate that was free in between may not
    be free any more.
    """
    changes, errors = {}, {}
    allowed = {f.name: f for f in editable_for(registration)}

    for key, value in (raw or {}).items():
        if key in READ_ONLY_REASONS:
            errors[key] = READ_ONLY_REASONS[key]
            continue
        field = allowed.get(key)
        if field is None and key in EBIKE_LOCKED_FIELDS and holds_control_number(registration):
            errors[key] = EBIKE_LOCKED_REASON
            continue
        if field is None:
            # Unknown, or not applicable to this registrant — either way it is
            # not silently dropped: a quietly ignored edit reads to the person
            # as one that was saved.
            if key in EDITABLE_FIELDS:
                errors[key] = 'This field does not apply to your registration.'
            else:
                errors[key] = 'This field cannot be changed here.'
            continue
        cleaned, error = field.clean(value, registration)
        if error:
            errors[key] = error
            continue
        if cleaned == (getattr(registration, key) or '') and key != 'department':
            continue
        if key == 'department' and cleaned == (
            registration.get_department_type_display() if registration.department_type else ''
        ):
            continue
        changes[key] = cleaned

    if errors:
        return {}, errors

    # ── Cross-field rules ──
    # The authorized driver is a pair: a name without a relationship names
    # nobody in particular, and JHS/Elementary students may never self-drive,
    # so clearing the name would leave a minor registered as their own driver.
    final_driver_name = changes.get('driver_name', registration.driver_name)
    final_driver_rel = changes.get('driver_relationship', registration.driver_relationship)
    if 'driver_name' in changes or 'driver_relationship' in changes:
        if final_driver_name and not final_driver_rel:
            errors['driver_relationship'] = (
                "Please say how the authorized driver is related to the student.")
        if not final_driver_name and registration.student_level in ('jhs', 'elementary'):
            errors['driver_name'] = (
                "Junior High and Elementary students are minors and cannot drive, so an "
                "authorized driver is required.")
        if not final_driver_name and final_driver_rel:
            errors['driver_name'] = (
                "Enter the authorized driver's name, or clear the relationship as well.")

    # A plate or conduction number moving has to stay unique among live
    # registrations and unclaimed by an existing pass, the same test submission
    # applies. Imported here to avoid a circular import: views imports this
    # module for the whitelist.
    if 'plate_number' in changes or 'conduction_number' in changes:
        from .views import _conduction_conflict, _plate_conflict
        active = _active_registrations(registration.pk)
        if 'plate_number' in changes:
            conflict = _plate_conflict(changes['plate_number'], active)
            if conflict:
                errors['plate_number'] = conflict
        if 'conduction_number' in changes:
            conflict = _conduction_conflict(changes['conduction_number'], active)
            if conflict:
                errors['conduction_number'] = conflict

    if 'drivers_license' in changes:
        from .views import _license_conflict
        conflict = _license_conflict(changes['drivers_license'],
                                     _active_registrations(registration.pk))
        if conflict:
            errors['drivers_license'] = conflict

    if errors:
        return {}, errors
    return changes, {}


def describe(registration, changes, previous=None):
    """The change set as `[{field, label, old, new}, ...]`.

    `old` comes from the row as it stands right now, so a reviewer looking at a
    request still waiting always sees the change against what approving it
    would actually overwrite — not against a snapshot that may be days stale.

    `previous` overrides that, and is what a *decided* request must be rendered
    with. Once a change is applied the row holds the new value, so reading `old`
    off it would render an approved "BLUE to RED" as "RED to RED" — the one
    reading that tells the owner nothing about what was done. That is what
    `RegistrationChangeRequest.previous` is for.
    """
    before = dict(current_values(registration))
    if previous:
        before.update(previous)
    rows = []
    for name, value in changes.items():
        field = EDITABLE_FIELDS.get(name)
        rows.append({
            'field': name,
            'label': field.label if field else name,
            'old': before.get(name, getattr(registration, name, '') or ''),
            'new': value,
        })
    return rows


def apply_changes(registration, changes):
    """Write `changes` onto `registration` and return the fields touched.

    Does not save — the caller owns the transaction, because an accepted
    registration's approval has to move the Vehicle and the User account in the
    same breath (see `_mirror_registration_change`).

    Two knock-on rules live here rather than in the cleaners, because both
    depend on the combination rather than on one value:

      * `department` writes `department_type`, the column the Vehicle Pass fee
        is read from, alongside the label.
      * a vehicle that stops being a tricycle stops having a body number.
    """
    touched = []
    for name, value in changes.items():
        if name == 'department':
            label_to_value = {
                lbl: val for val, lbl in VehicleRegistration.DepartmentType.choices
            }
            registration.department_type = label_to_value.get(value, '')
            # The FK points at the ReferenceItem department list, which is
            # curated by admin; match by name and leave it alone if absent
            # rather than minting a reference item from user input.
            registration.department = ReferenceItem.objects.filter(
                category='department', name=value, is_active=True,
            ).first()
            touched += ['department', 'department_type']
            continue
        setattr(registration, name, value)
        touched.append(name)

    if 'vehicle_type' in changes and changes['vehicle_type'] != BODY_NUMBER_TYPE:
        if registration.body_number:
            registration.body_number = ''
            if 'body_number' not in touched:
                touched.append('body_number')

    return touched

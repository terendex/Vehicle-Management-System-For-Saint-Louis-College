/* The violation types the system actually issues, and how each is named.

   Only four types are ever written today: the gate and the parking cameras
   issue them (scanning/views.py _auto_log_violation, parking_camera.py), and
   nothing creates the others. Those four are what a filter offers. The older
   types stay readable on rows issued before them, and a legacy "unauthorized"
   row is the same offense as Unauthorized Entry, so it reads as one. */

export const VIOLATION_TYPES = [
  { value: 'unauthorized_entry',   label: 'Unauthorized Entry' },
  { value: 'double_parking',       label: 'Double Parking' },
  { value: 'time_exceed',          label: 'Overstaying' },
  { value: 'confiscated_activity', label: 'Activity While Confiscated' },
]

const LEGACY_LABELS = {
  unauthorized:         'Unauthorized Entry',
  no_sticker:           'No Sticker',
  expired_registration: 'Expired Registration',
  other:                'Other',
}

const LABELS = {
  ...Object.fromEntries(VIOLATION_TYPES.map(t => [t.value, t.label])),
  ...LEGACY_LABELS,
}

/** The filter value a stored type falls under: legacy "unauthorized" is Unauthorized Entry. */
export const violationTypeKey = (type) => (type === 'unauthorized' ? 'unauthorized_entry' : type)

/** '45 min', '2 hr', '1 hr 25 min'. */
export function formatOverstay(minutes) {
  const total = Math.max(0, Math.round(Number(minutes) || 0))
  const hours = Math.floor(total / 60), mins = total % 60
  if (!hours) return `${mins} min`
  return mins ? `${hours} hr ${mins} min` : `${hours} hr`
}

/** A type's name. */
export const violationTypeName = (type) => LABELS[type] ?? type

/** A violation row's type, with how long it overstayed: 'Overstaying (1 hr 25 min)'. */
export function violationLabel(v) {
  const name = violationTypeName(v.violation_type)
  return v.overstay_minutes ? `${name} (${formatOverstay(v.overstay_minutes)})` : name
}

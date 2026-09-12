import {
  DEPARTMENTS, DRIVER_RELATIONSHIPS, VEHICLE_COLORS, VEHICLE_TYPES,
  formatDetailValue,
} from './detailRules'

/* The editable-detail fields, rendered from whatever the server says is
   editable for this particular registration.

   Shared by both places a person can change their own details — the public page
   an applicant reaches from their acknowledgement email, and the modal an
   approved owner opens on their dashboard. They render the same fields because
   the server offers the same fields: `editable` is `registration_edits`'
   whitelist, filtered for this registrant. Driving the form off that list rather
   than hard-coding inputs is what keeps the two screens from drifting apart when
   the whitelist changes, and stops either one showing a student a Department
   box. */

const HINTS = {
  plate_number:      'e.g. AAA 0000 · AA 0000 · A000AA',
  conduction_number: 'e.g. CS12345A678',
  drivers_license:   'e.g. A00-00-000000',
  full_name:         'Last name, First name, Middle name',
  body_number:       'Tricycles only — leave blank if yours has none',
}

export default function DetailFields({
  editable, values, onChange, errors = {}, disabled = false, idPrefix = 'rd',
}) {
  const set = (field) => (event) => onChange(field, formatDetailValue(field, event.target.value))

  return (
    <div className="rd-fields">
      {editable.map(({ field, label }) => {
        const id = `${idPrefix}-${field}`
        const value = values[field] ?? ''
        const error = errors[field]
        const hint = HINTS[field]

        let control
        if (field === 'vehicle_type') {
          control = (
            <select id={id} value={value} onChange={set(field)} disabled={disabled}>
              {VEHICLE_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
          )
        } else if (field === 'department') {
          control = (
            <select id={id} value={value} onChange={set(field)} disabled={disabled}>
              <option value="">Select department</option>
              {DEPARTMENTS.map(d => <option key={d} value={d}>{d}</option>)}
            </select>
          )
        } else if (field === 'driver_relationship') {
          control = (
            <select id={id} value={value} onChange={set(field)} disabled={disabled}>
              <option value="">Not applicable</option>
              {DRIVER_RELATIONSHIPS.map(r => (
                <option key={r.value} value={r.value}>{r.label}</option>
              ))}
            </select>
          )
        } else if (field === 'vehicle_color') {
          /* A datalist rather than a <select>: the preset list covers almost
             everything, and the registration form's "Other" branch exists
             because it does not cover all of it. Typing straight into the box
             is the same escape hatch with one fewer step. */
          control = (
            <>
              <input
                id={id} type="text" value={value} onChange={set(field)}
                disabled={disabled} list={`${id}-options`} autoComplete="off"
              />
              <datalist id={`${id}-options`}>
                {VEHICLE_COLORS.map(c => <option key={c} value={c.toUpperCase()} />)}
              </datalist>
            </>
          )
        } else {
          control = (
            <input
              id={id} type="text" value={value} onChange={set(field)}
              disabled={disabled} autoComplete="off"
            />
          )
        }

        return (
          <div className={`rd-field${error ? ' rd-field--error' : ''}`} key={field}>
            <label htmlFor={id}>{label}</label>
            {control}
            {error
              ? <span className="rd-field-error">{error}</span>
              : hint && <span className="rd-field-hint">{hint}</span>}
          </div>
        )
      })}
    </div>
  )
}

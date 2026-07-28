import './TableLoader.css'

/**
 * Loading state for a table.
 *
 * The spinner matches the one AuditLog and UserManagement already use
 * (36px, brand ring) — this just puts it in one place so every table shows
 * the same thing instead of each page redefining it, or showing nothing.
 *
 * Two ways to use it:
 *   1. Around a table — replace the whole <table> while data is loading:
 *        {loading ? <TableLoader label="Loading users…" /> : <table>…</table>}
 *   2. Inside a table — keep the header visible and fill the body:
 *        <tbody>{loading
 *          ? <TableLoaderRow colSpan={7} label="Loading registrations…" />
 *          : rows.map(…)}</tbody>
 */
export default function TableLoader({ label = 'Loading…' }) {
  return (
    <div className="tbl-loading" role="status" aria-live="polite">
      <div className="tbl-spinner" />
      <p>{label}</p>
    </div>
  )
}

/** Same loader as a table row, so column headers stay put while data loads. */
export function TableLoaderRow({ colSpan, label = 'Loading…' }) {
  return (
    <tr className="tbl-loading-row">
      <td colSpan={colSpan}>
        <TableLoader label={label} />
      </td>
    </tr>
  )
}

/** Matching empty state, so "no data" and "still loading" never look alike. */
export function TableEmptyRow({ colSpan, label = 'No records found.' }) {
  return (
    <tr className="tbl-empty-row">
      <td colSpan={colSpan}>
        <div className="tbl-empty">{label}</div>
      </td>
    </tr>
  )
}

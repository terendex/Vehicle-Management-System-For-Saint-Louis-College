import { useState } from 'react'
import { format } from 'date-fns'
import { Car, Search } from 'lucide-react'
import TableLoader from '../../components/TableLoader'
import './InsideCampus.css'

/* Operations Center, Inside Campus tab: every vehicle on campus now, with its
   owner, from GET /scan/inside/ (fetched by the page's own refresh, so the
   stat card and this table always agree). The chips narrow by who came in. */

function stayText(minutes) {
  const h = Math.floor(minutes / 60), m = minutes % 60
  if (!h) return `${m} min`
  return m ? `${h} hr ${m} min` : `${h} hr`
}

export default function InsideCampus({ data, loading }) {
  const [group, setGroup] = useState('all')
  const [search, setSearch] = useState('')

  if (!data) {
    return (
      <div className="oc-section">
        <div className="oc-section-head"><Car size={15} /><span>Vehicles Inside Campus</span></div>
        {loading ? <TableLoader label="Loading vehicles inside…" /> : <p className="ic-empty">Could not load the vehicles inside.</p>}
      </div>
    )
  }

  const q = search.trim().toLowerCase()
  const rows = data.results.filter((r) =>
    (group === 'all' || r.group === group)
    && (!q || r.plate.toLowerCase().includes(q) || (r.name || '').toLowerCase().includes(q)))
  const chips = [{ key: 'all', label: 'All' }, ...data.groups]

  return (
    <div className="oc-section">
      <div className="oc-section-head">
        <Car size={15} />
        <span>Vehicles Inside Campus</span>
        <span className="oc-duty-count">{data.counts.all} inside</span>
      </div>

      <div className="ic-toolbar">
        <div className="ic-chips" role="group" aria-label="Who came in">
          {chips.map((c) => (
            <button
              key={c.key}
              type="button"
              className={`ic-chip ${group === c.key ? 'ic-chip--active' : ''}`}
              aria-pressed={group === c.key}
              onClick={() => setGroup(c.key)}
            >
              {c.label}<span className="ic-chip-count">{data.counts[c.key] ?? 0}</span>
            </button>
          ))}
        </div>
        <label className="ic-search">
          <Search size={14} />
          <input
            type="search"
            placeholder="Plate or name"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
      </div>

      {rows.length === 0 ? (
        <p className="ic-empty">
          {data.counts.all === 0 ? 'No vehicles are inside the campus right now.' : 'No vehicle inside matches.'}
        </p>
      ) : (
        <div className="ic-table-wrap">
          <table className="ic-table">
            <thead>
              <tr>
                <th>Plate</th>
                <th>Owner / Driver</th>
                <th>Category</th>
                <th>Vehicle</th>
                <th>Gate</th>
                <th>Time In</th>
                <th>Inside For</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td className="ic-plate" data-label="Plate">{r.plate}</td>
                  <td data-label="Owner / Driver">{r.name || <span className="ic-muted">Not recorded</span>}</td>
                  <td data-label="Category"><span className={`ic-cat ic-cat--${r.group}`}>{r.category_label}</span></td>
                  <td data-label="Vehicle">{[r.vehicle_type, r.vehicle].filter(Boolean).join(', ') || '—'}</td>
                  <td data-label="Gate">{r.gate}</td>
                  <td data-label="Time In">{format(new Date(r.entered_at), 'h:mm a')}</td>
                  <td data-label="Inside For">{stayText(r.minutes_inside)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

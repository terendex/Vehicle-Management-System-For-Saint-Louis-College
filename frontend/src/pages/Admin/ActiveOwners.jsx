import { useState } from 'react'
import { format } from 'date-fns'
import { AlertTriangle, Search, Users } from 'lucide-react'
import TableLoader from '../../components/TableLoader'
import { activeOwnerItems, activeOwnerChips, filterActiveOwners } from '../../utils/activeOwners'
import './ActiveOwners.css'

/* Operations Center, Active Owners tab: everyone on campus now, from
   GET /scan/inside/ (fetched by the page's own refresh, so the stat card and
   this table always agree). The same list the guard's entry page shows in its
   Active Owners panel (utils/activeOwners.js); here it is a table. A visitor's
   row carries their pass, so its time left and overstay show in Time Left. */

function stayText(minutes) {
  const h = Math.floor(minutes / 60), m = minutes % 60
  if (!h) return `${m} min`
  return m ? `${h} hr ${m} min` : `${h} hr`
}

// Time left / overstay on a visitor pass (the guard entry page reads it the same way).
function passTimeInfo(p) {
  if (!p.expires_at) return { label: 'No limit', overdue: false, soon: false }
  const diffMin = Math.round((new Date(p.expires_at).getTime() - Date.now()) / 60000)
  if (diffMin >= 0) return { label: `${stayText(diffMin)} left`, overdue: false, soon: diffMin <= 10 }
  return { label: `Overstay ${stayText(-diffMin)}`, overdue: true, soon: false }
}

function clock(ts) {
  try { return format(new Date(ts), 'h:mm a') } catch { return '' }
}

export default function ActiveOwners({ data, loading }) {
  const [group, setGroup] = useState('all')
  const [search, setSearch] = useState('')

  if (!data) {
    return (
      <div className="oc-section">
        <div className="oc-section-head"><Users size={15} /><span>Active Owners</span></div>
        {loading ? <TableLoader label="Loading who is inside…" /> : <p className="ic-empty">Could not load who is inside.</p>}
      </div>
    )
  }

  const items = activeOwnerItems(data)
  const chips = activeOwnerChips(items, data.groups)
  const { group: shown, rows } = filterActiveOwners(items, chips, group, search)

  return (
    <div className="oc-section">
      <div className="oc-section-head">
        <Users size={15} />
        <span>Active Owners</span>
        <span className="oc-duty-count">{chips[0].count} inside</span>
      </div>

      <div className="ic-toolbar">
        <div className="ic-chips" role="group" aria-label="Who is inside">
          {chips.map((c) => (
            <button
              key={c.key}
              type="button"
              className={`ic-chip ${shown === c.key ? 'ic-chip--active' : ''}`}
              aria-pressed={shown === c.key}
              onClick={() => setGroup(c.key)}
            >
              {c.label}<span className="ic-chip-count">{c.count}</span>
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
          {items.length === 0 ? 'No one is inside the campus right now.' : 'No one inside matches.'}
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
                <th>Time Left</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({ key, row: r, pass: p, notInside }) => {
                const t = p ? passTimeInfo(p) : null
                const name = r?.name || p?.visitor_name
                return (
                  <tr key={key} className={t?.overdue ? 'ic-row--overdue' : undefined}>
                    <td className="ic-plate" data-label="Plate">{r?.plate ?? p.plate_number}</td>
                    <td data-label="Owner / Driver">
                      <div className="ic-who">
                        {name || <span className="ic-muted">Not recorded</span>}
                        {/* The visit itself, off the pass. */}
                        {p && (
                          <span className="ic-sub">
                            {[p.office_name || 'No office', p.purpose].filter(Boolean).join(' · ')}
                            {p.issued_by_name && ` · Issued by ${p.issued_by_name}`}
                          </span>
                        )}
                      </div>
                    </td>
                    <td data-label="Category">
                      <span className="ic-cat-cell">
                        <span className={`ic-cat ic-cat--${r?.group ?? 'visitor'}`}>{r?.category_label ?? 'Visitor'}</span>
                        {/* Open, but no entry inside: the slip never printed (the
                            entry is logged on print), or it has gone stale. */}
                        {notInside && (
                          <span className="ic-flag">{p.printed_at ? 'No entry logged' : 'Slip not printed'}</span>
                        )}
                      </span>
                    </td>
                    <td data-label="Vehicle">{r ? [r.vehicle_type, r.vehicle].filter(Boolean).join(', ') : ''}</td>
                    <td data-label="Gate">{r?.gate ?? ''}</td>
                    <td data-label="Time In">{clock(r?.entered_at ?? p.entered_at)}</td>
                    <td data-label="Inside For">{r ? stayText(r.minutes_inside) : ''}</td>
                    <td data-label="Time Left">
                      {t && (
                        <span className={`ic-left${t.overdue ? ' ic-left--overdue' : t.soon ? ' ic-left--soon' : ''}`}>
                          {t.overdue && <AlertTriangle size={12} />}
                          {t.label}
                        </span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

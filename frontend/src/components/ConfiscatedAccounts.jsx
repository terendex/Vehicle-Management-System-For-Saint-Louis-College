import { useState, useEffect, useCallback } from 'react'
import { ShieldOff, Loader2, RotateCcw, CheckCircle } from 'lucide-react'
import { toast } from 'sonner'
import { useLiveUpdates } from '../realtime/useLiveUpdates'
import useAuthStore from '../stores/authStore'
import {
  getConfiscatedAccounts, liftConfiscation, setRegistrationPermission,
} from '../api/violations'
import './ConfiscatedAccounts.css'

// Accounts serving a violation penalty. Shown at the gate and in the parking
// view because a guard is the person who meets these vehicles — the penalty is
// only real if the people on the ground can see who is under it.
//
// One component, two placements: the guard screens render it read-only, and
// the CDSO gets the lift and re-registration controls. A second copy of this
// list would be a second definition of "confiscated" waiting to drift.

const LEVEL_LABEL = {
  1: '1st offence · 1 week',
  2: '2nd offence · 2 weeks',
  3: '3rd offence · rest of the period',
}

function formatDate(d) {
  if (!d) return null
  return new Date(d + 'T00:00:00').toLocaleDateString('en-PH', {
    month: 'short', day: 'numeric', year: 'numeric',
  })
}

export default function ConfiscatedAccounts({ compact = false }) {
  const { user } = useAuthStore()
  const isAdmin = user?.role === 'admin'

  const [rows, setRows]       = useState([])
  const [loading, setLoading] = useState(true)
  const [busyId, setBusyId]   = useState(null)

  const load = useCallback(() => {
    getConfiscatedAccounts()
      .then(setRows)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])
  useLiveUpdates(load, ['violation', 'user'])

  const handleLift = async (row) => {
    setBusyId(row.id)
    try {
      await liftConfiscation(row.id)
      setRows(prev => prev.filter(r => r.id !== row.id))
      toast.success(`Confiscation lifted for ${row.full_name}.`)
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Could not lift the confiscation.')
    } finally {
      setBusyId(null)
    }
  }

  const handleAllowRegister = async (row) => {
    setBusyId(row.id)
    try {
      const updated = await setRegistrationPermission(row.id, row.registration_banned)
      setRows(prev => prev.map(r => (r.id === row.id ? { ...r, ...updated } : r)))
      toast.success(
        updated.registration_banned
          ? `${row.full_name} may not register again.`
          : `${row.full_name} may register again.`,
      )
    } catch {
      toast.error('Could not change the registration permission.')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className={`ca-card${compact ? ' ca-compact' : ''}`}>
      <div className="ca-head">
        <span className="ca-title">
          <ShieldOff size={15} />
          Confiscated accounts
        </span>
        <span className={`ca-count${rows.length ? ' ca-count-active' : ''}`}>
          {rows.length}
        </span>
      </div>

      <div className="ca-body">
        {loading ? (
          <div className="ca-loading"><Loader2 size={18} className="ca-spin" /></div>
        ) : rows.length === 0 ? (
          <p className="ca-empty">
            No accounts are confiscated. Everyone registered may enter and park.
          </p>
        ) : (
          <>
            <p className="ca-note">
              These owners may <strong>not enter and may not park</strong>. If one is
              detected at a gate or in a parking area, that counts as a further offence.
            </p>
            <ul className="ca-list">
              {rows.map(row => (
                <li key={row.id} className={`ca-row ca-level-${row.confiscation_level}`}>
                  <div className="ca-row-main">
                    <span className="ca-name">{row.full_name}</span>
                    <span className="ca-plates">
                      {row.plates?.length ? row.plates.join(' · ') : 'No plate on file'}
                    </span>
                  </div>

                  <div className="ca-row-meta">
                    <span className={`ca-level ca-level-tag-${row.confiscation_level}`}>
                      {LEVEL_LABEL[row.confiscation_level] || `Offence ${row.confiscation_level}`}
                    </span>
                    <span className="ca-until">
                      {row.is_indefinite
                        ? 'Until the CDSO lifts it'
                        : `Until ${formatDate(row.confiscated_until)} · ${row.days_left} day${row.days_left === 1 ? '' : 's'} left`}
                    </span>
                    {row.registration_banned && (
                      <span className="ca-banned">Cannot register again</span>
                    )}
                  </div>

                  {isAdmin && (
                    <div className="ca-actions">
                      <button
                        className="ca-btn"
                        disabled={busyId === row.id}
                        title="End the penalty now. The violations themselves stay on record."
                        onClick={() => handleLift(row)}
                      >
                        {busyId === row.id
                          ? <Loader2 size={12} className="ca-spin" />
                          : <RotateCcw size={12} />}
                        Lift
                      </button>
                      {row.confiscation_level >= 3 && (
                        <button
                          className="ca-btn"
                          disabled={busyId === row.id}
                          title="A 3rd offence blocks re-registration. Allowing it is the CDSO's decision."
                          onClick={() => handleAllowRegister(row)}
                        >
                          <CheckCircle size={12} />
                          {row.registration_banned ? 'Allow re-register' : 'Block re-register'}
                        </button>
                      )}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </div>
  )
}

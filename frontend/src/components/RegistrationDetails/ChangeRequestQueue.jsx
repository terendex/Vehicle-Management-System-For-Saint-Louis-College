import { useCallback, useEffect, useState } from 'react'
import { Check, Clock3, X } from 'lucide-react'
import { format } from 'date-fns'

import { registrationApi } from '../../api/registration'
import notify from '../../components/Feedback/notify'
import { fieldProblems } from '../../components/Feedback/formProblems'
import { useLiveUpdates } from '../../realtime/useLiveUpdates'
import ChangeDiff from './ChangeDiff'
import './detailFields.css'
import './changeRequestQueue.css'

/* The CDSO queue for owners asking to correct their own details.

   Self-contained on purpose — it fetches, decides and reports entirely on its
   own — because it lives on User Management, which is about accounts, while
   the thing it edits is a registration. Nothing is threaded through the host
   page, so it can be dropped anywhere an admin would look for it without that
   page learning about change requests.

   It reports through `notify`, the project-wide acknowledge-to-dismiss
   convention, rather than through whatever result modal the host page happens
   to own.

   An approved registration is not the owner's to rewrite: it issued their
   pass, their gate QR, a Vehicle row a guard resolves against and their portal
   account. So they file a request, it waits here, and approving is what applies
   it — see backend/vehicles/registration_edits.py.

   Applications still *pending* are not in this queue at all. Nobody has
   reviewed those, so the applicant corrects them straight from the link in
   their acknowledgement email; a correction of that kind arrives as a
   notification rather than as work. */

const FILTERS = [
  { value: 'pending',   label: 'Waiting for review' },
  { value: 'approved',  label: 'Approved' },
  { value: 'rejected',  label: 'Declined' },
  { value: 'cancelled', label: 'Withdrawn by owner' },
  { value: 'all',       label: 'All' },
]

export default function ChangeRequestQueue() {
  const [requests, setRequests] = useState([])
  const [filter, setFilter]     = useState('pending')
  const [loading, setLoading]   = useState(true)
  const [deciding, setDeciding] = useState(null)   // id being decided

  const [declineTarget, setDeclineTarget] = useState(null)
  const [declineReason, setDeclineReason] = useState('')

  const fetchRequests = useCallback(async (statusOverride) => {
    setLoading(true)
    try {
      setRequests(await registrationApi.getChangeRequests(statusOverride ?? filter))
    } catch (error) {
      console.error('Failed to fetch change requests:', error)
    } finally {
      setLoading(false)
    }
  }, [filter])

  useEffect(() => { fetchRequests() }, [fetchRequests])

  // An owner filing a request has to appear here without a reload — this is
  // the screen a reviewer sits on waiting for work.
  useLiveUpdates(() => fetchRequests(), ['registrationchangerequest'])

  const approve = async (request) => {
    // Asked first, the way the accept flow asks. Approving writes straight
    // through to a live pass — the registration, the Vehicle row a guard
    // resolves against, and the owner's account — from a single click in a
    // list, so the fields being changed are restated before it happens.
    if (!(await notify.confirm({
      title: 'Approve this change?',
      message: `${request.full_name}'s registration will be updated immediately.`,
      description: 'Their vehicle record and portal account are updated with it, and they '
                 + 'are emailed the outcome.',
      details: (request.changes || []).map(
        row => `${row.label}: ${row.old || '(blank)'} → ${row.new}`),
      confirmLabel: 'Approve & apply',
    }))) return

    setDeciding(request.id)
    try {
      await registrationApi.approveChangeRequest(request.id)
      await fetchRequests()
      await notify.success('Change approved and applied to the registration.',
        { title: 'Change applied' })
    } catch (error) {
      // 409 is the interesting one: the request was valid when it was filed but
      // the detail it asks for has been taken since, so it has to be declined
      // rather than forced through. The server says which field.
      const data = error.response?.data
      const detail = data?.errors ? Object.values(data.errors).join(' ') : ''
      await notify.error([data?.error || 'Failed to approve the change.', detail]
        .filter(Boolean).join(' '), { title: 'Change not applied' })
    } finally {
      setDeciding(null)
    }
  }

  const decline = async (event) => {
    event.preventDefault()
    if (!declineTarget) return
    if (await notify.validation(fieldProblems(event.currentTarget))) return

    setDeciding(declineTarget.id)
    try {
      await registrationApi.rejectChangeRequest(declineTarget.id, declineReason.trim())
      setDeclineTarget(null)
      setDeclineReason('')
      await fetchRequests()
      await notify.success('Change declined. The owner has been emailed the reason.',
        { title: 'Change declined' })
    } catch (error) {
      await notify.error(error.response?.data?.error || 'Failed to decline the change.',
        { title: 'Change not declined' })
    } finally {
      setDeciding(null)
    }
  }

  return (
    <>
      <div className="crq-head">
        <div>
          <h2 className="crq-title">
            <Clock3 size={16} />
            Detail Change Requests
          </h2>
          <p className="crq-sub">
            Approved owners asking to correct their own details. Nothing changes on their
            registration until you approve it.
          </p>
        </div>
        <select
          className="crq-filter"
          value={filter}
          onChange={(e) => { setFilter(e.target.value); fetchRequests(e.target.value) }}
          aria-label="Filter change requests"
        >
          {FILTERS.map(f => <option key={f.value} value={f.value}>{f.label}</option>)}
        </select>
      </div>

      {loading ? (
        <p className="crq-empty">Loading change requests…</p>
      ) : requests.length === 0 ? (
        <p className="crq-empty">
          {filter === 'pending'
            ? 'No change requests waiting for review.'
            : 'Nothing here.'}
        </p>
      ) : (
        <div className="crq-list">
          {requests.map(request => (
            <div className="crq-card" key={request.id}>
              <div className="crq-card-head">
                <div className="crq-who">
                  <span className="crq-name">{request.full_name}</span>
                  <span className="crq-meta">
                    <span className="crq-plate">{request.plate_number || '—'}</span>
                    {' · '}<span className="crq-type">{request.registrant_type}</span>
                    {' · '}{request.email}
                  </span>
                  {/* Who filed it is not always the registrant: a name change
                      is exactly the case where the two differ. */}
                  {request.requested_by && request.requested_by !== request.full_name && (
                    <span className="crq-meta">Filed by {request.requested_by}</span>
                  )}
                </div>
                <div className="crq-when">
                  <span className={`crq-status crq-status--${request.status}`}>
                    {request.status_label}
                  </span>
                  <span className="crq-date">
                    {request.created_at
                      ? format(new Date(request.created_at), 'MMM d, yyyy · h:mm a')
                      : ''}
                  </span>
                </div>
              </div>

              <ChangeDiff changes={request.changes} />

              {request.decision_note && (
                <p className="crq-note">
                  <strong>{request.status === 'rejected' ? 'Reason given:' : 'Note:'}</strong>{' '}
                  {request.decision_note}
                </p>
              )}
              {request.reviewed_by && (
                <p className="crq-note crq-note--muted">
                  Decided by {request.reviewed_by}
                  {request.reviewed_at
                    ? ` on ${format(new Date(request.reviewed_at), 'MMM d, yyyy')}`
                    : ''}
                </p>
              )}

              {request.status === 'pending' && (
                <div className="crq-actions">
                  <button
                    type="button"
                    className="crq-btn crq-btn--ghost"
                    onClick={() => { setDeclineTarget(request); setDeclineReason('') }}
                    disabled={deciding === request.id}
                  >
                    <X size={14} /> Decline
                  </button>
                  <button
                    type="button"
                    className="crq-btn crq-btn--primary"
                    onClick={() => approve(request)}
                    disabled={deciding === request.id}
                  >
                    <Check size={14} />
                    {deciding === request.id ? 'Applying…' : 'Approve & apply'}
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* A reason is mandatory — the server refuses without one, and an owner
          told "no" with nothing to correct will simply file the same request
          again. It is emailed to them verbatim. */}
      {declineTarget && (
        <div className="crq-modal-overlay" onClick={() => !deciding && setDeclineTarget(null)}>
          <div className="crq-modal" onClick={e => e.stopPropagation()}>
            <div className="crq-modal-head">
              <h2>Decline Detail Change</h2>
              <button
                type="button"
                className="crq-modal-close"
                onClick={() => setDeclineTarget(null)}
                aria-label="Close"
              >
                <X size={18} />
              </button>
            </div>
            <form onSubmit={decline} noValidate>
              <p className="crq-modal-lede">
                Declining <strong>{declineTarget.full_name}</strong>'s request. Their
                registration stays exactly as it is, and this reason is emailed to them.
              </p>
              <div className="crq-card crq-card--inline">
                <ChangeDiff changes={declineTarget.changes} />
              </div>
              <label className="crq-label" htmlFor="crq-reason">
                Reason <span className="crq-req">*</span>
              </label>
              <textarea
                id="crq-reason"
                className="crq-textarea"
                rows={4}
                value={declineReason}
                onChange={(e) => setDeclineReason(e.target.value)}
                placeholder="Tell them what to do instead — e.g. bring your OR/CR to the CDSO Office."
                required
              />
              <div className="crq-modal-actions">
                <button
                  type="button"
                  className="crq-btn crq-btn--ghost"
                  onClick={() => setDeclineTarget(null)}
                  disabled={deciding === declineTarget.id}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="crq-btn crq-btn--danger"
                  disabled={deciding === declineTarget.id}
                >
                  {deciding === declineTarget.id ? 'Processing…' : 'Confirm Decline'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </>
  )
}

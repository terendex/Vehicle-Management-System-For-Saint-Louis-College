import { useEffect, useState } from 'react'
import { Clock3, Play, RotateCcw, FastForward, Loader2, Mail, Database } from 'lucide-react'
import notify from '../../components/Feedback/notify'
import { getTestClock, testClockAction } from '../../api/simClock'
import { useSimClock } from '../../stores/simClockStore'
import './TestClock.css'

const STEPS = [
  { step: '1h',  label: '+1 hour' },
  { step: '1d',  label: '+1 day' },
  { step: '1wd', label: '+1 working day' },
  { step: '7d',  label: '+1 week' },
]

/* The instructor demo's Test Clock (dev.ps1 -SimClock only).

   Moves the date the SERVER lives by. The real rules then run against it:
   applications expire, reminders and expiry emails go out, accounts are
   archived on August 1. Run Time-Based Jobs Now does at once what the
   scheduler would otherwise do within the hour. */
export default function TestClock() {
  const setSim = useSimClock((s) => s.setSim)
  const [clock, setClock] = useState(null)
  const [missing, setMissing] = useState(false)
  const [when, setWhen] = useState('')
  const [busy, setBusy] = useState('')
  const [jobs, setJobs] = useState(null)

  useEffect(() => {
    getTestClock()
      .then(({ data }) => { setClock(data); setSim(data) })
      .catch(() => setMissing(true))
  }, [setSim])

  const act = async (action, extra = {}) => {
    setBusy(action + (extra.step || ''))
    try {
      const { data } = await testClockAction(action, extra)
      setClock(data)
      setSim(data)                       // the red bar follows without waiting for its poll
      if (action === 'run_jobs') setJobs(data.jobs)
    } catch (err) {
      notify.error(err.response?.data?.error || 'The test clock did not respond.', { title: 'Test Clock' })
    } finally {
      setBusy('')
    }
  }

  if (missing) {
    return (
      <div className="tc-page">
        <h1 className="tc-title"><Clock3 size={22} /> Test Clock</h1>
        <p className="tc-note">
          The test clock only runs on the local instructor demo. Start it with
          <code>.\dev.ps1 -SimClock</code>. It never runs on the live system.
        </p>
      </div>
    )
  }
  if (!clock) return <div className="tc-page"><Loader2 className="tc-spin" size={22} /></div>

  return (
    <div className="tc-page">
      <div className="tc-header">
        <div>
          <h1 className="tc-title"><Clock3 size={22} /> Test Clock</h1>
          <p className="tc-subtitle">
            Moves the date this demo server runs on. Every time based rule uses it: the 3 working
            day payment window, registration closing, and passes ending on July 31.
          </p>
        </div>
        <label className="tc-toggle">
          <input
            type="checkbox"
            checked={clock.enabled}
            disabled={!!busy}
            onChange={(e) => act(e.target.checked ? 'enable' : 'disable')}
          />
          <span>Enable simulated date</span>
        </label>
      </div>

      <div className="tc-grid">
        <section className={`tc-card ${clock.enabled ? 'tc-card--on' : ''}`}>
          <h2>Simulated date</h2>
          <p className="tc-big">{clock.enabled ? clock.now_display : 'Off: using the real date'}</p>
          <p className="tc-muted">Real date: {clock.real_now_display}</p>
          <p className="tc-muted">Offset: {clock.enabled ? clock.offset_text : '0'}</p>
        </section>

        <section className="tc-card">
          <h2>Set the date</h2>
          <div className="tc-row">
            <input
              type="datetime-local"
              className="tc-input"
              value={when}
              onChange={(e) => setWhen(e.target.value)}
            />
            <button
              type="button"
              className="tc-btn tc-btn--primary"
              disabled={!when || !!busy}
              onClick={() => act('set', { when: when.replace('T', ' ') })}
            >
              Set
            </button>
          </div>
          <div className="tc-row tc-row--wrap">
            {STEPS.map(({ step, label }) => (
              <button
                key={step}
                type="button"
                className="tc-btn"
                disabled={!!busy}
                onClick={() => act('advance', { step })}
              >
                <FastForward size={14} /> {label}
              </button>
            ))}
            <button type="button" className="tc-btn" disabled={!!busy} onClick={() => act('reset')}>
              <RotateCcw size={14} /> Back to the real date
            </button>
          </div>
        </section>

        <section className="tc-card">
          <h2>Run time based jobs now</h2>
          <p className="tc-muted">
            Expires overdue applications, sends payment reminders, archives expired accounts and
            rolls events and scheduled visits over, at the simulated date. The scheduler does the
            same by itself within the hour.
          </p>
          <button
            type="button"
            className="tc-btn tc-btn--primary"
            disabled={!!busy}
            onClick={() => act('run_jobs')}
          >
            {busy === 'run_jobs' ? <Loader2 size={14} className="tc-spin" /> : <Play size={14} />}
            Run Time Based Jobs Now
          </button>
          {jobs && (
            <ul className="tc-jobs">
              {jobs.map((j) => (
                <li key={j.job} className={j.ok ? '' : 'tc-job--failed'}>
                  <strong>{j.label}:</strong> {j.result}
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="tc-card">
          <h2>This demo</h2>
          <p className="tc-muted"><Database size={13} /> Database: {clock.database} (local copy, not the live system)</p>
          <p className="tc-muted">
            <Mail size={13} /> Emails: {clock.email_to
              ? <>sent for real, all to <strong>{clock.email_to}</strong></>
              : <>printed in the backend window (set SIM_EMAIL_TO in backend/.env to send them)</>}
          </p>
        </section>
      </div>
    </div>
  )
}

import { useEffect, useState } from 'react'
import { Clock, AlertTriangle } from 'lucide-react'
import './PaymentDeadline.css'

/* The 3-day receipt deadline, as one banner every applicant screen shares:
   the confirmation after submitting, the payment link, and the edit link.

   Everything it shows comes from the server. `display` is the deadline already
   formatted in campus time, and `secondsLeft` was counted on the server, so a
   phone set to the wrong time zone, or a wrong clock, cannot make the banner
   disagree with the deadline that is actually enforced. The countdown only
   measures time elapsed since the page loaded, which a wrong clock still gets
   right. */

function remaining(seconds) {
  if (seconds <= 0) return null
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.max(1, Math.floor((seconds % 3600) / 60))
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`
  if (days > 0) return `${plural(days, 'day')} ${plural(hours, 'hour')} left`
  if (hours > 0) return `${plural(hours, 'hour')} ${plural(minutes, 'minute')} left`
  return `${plural(minutes, 'minute')} left`
}

export default function PaymentDeadline({ display, secondsLeft, windowDays = 3, compact = false }) {
  const [loadedAt] = useState(() => Date.now())
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (secondsLeft == null) return undefined
    const id = setInterval(() => setNow(Date.now()), 30000)
    return () => clearInterval(id)
  }, [secondsLeft])

  if (!display || secondsLeft == null) return null

  const left = secondsLeft - Math.floor((now - loadedAt) / 1000)
  const label = remaining(left)
  // The last day is when people need pushing; before that it is information.
  const urgent = left < 86400

  return (
    <div className={`pay-deadline${urgent ? ' pay-deadline--urgent' : ''}${compact ? ' pay-deadline--compact' : ''}`}
         role="note">
      {urgent ? <AlertTriangle size={16} /> : <Clock size={16} />}
      <div className="pay-deadline-body">
        <p className="pay-deadline-when">
          Pay and file your receipt by <strong>{display}</strong>
          {label && <span className="pay-deadline-left"> · {label}</span>}
        </p>
        {!compact && (
          <p className="pay-deadline-why">
            You have {windowDays} working days (Monday to Friday) from applying. If the Official Receipt is not filed by
            then, this application <strong>expires automatically</strong> and you will need to
            submit a new one.
            {!label && ' The deadline has passed. If you have already paid, submit now. If it is refused, please apply again.'}
          </p>
        )}
      </div>
    </div>
  )
}

/* A set of changes as "was → is", from the `changes` array every endpoint on
   this feature returns: [{ field, label, old, new }, ...].

   Shown in three places — the applicant's confirmation after a correction, the
   owner's pending-request card, and the CDSO review panel — so it lives here
   rather than being written out three times with three different ideas of how
   to render a blank old value. */
export default function ChangeDiff({ changes, emptyLabel = 'Nothing changed.' }) {
  if (!changes?.length) {
    return <p className="rd-diff-empty">{emptyLabel}</p>
  }
  return (
    <div className="rd-diff">
      {changes.map(row => (
        <div className="rd-diff-row" key={row.field}>
          <span className="rd-diff-label">{row.label || row.field}</span>
          <span className="rd-diff-values">
            {/* An em dash, not an empty span: a field being filled in for the
                first time still needs a left-hand side, or the arrow reads as
                though it points out of nowhere. */}
            <span className="rd-diff-old">{row.old || '—'}</span>
            <span className="rd-diff-arrow" aria-hidden="true">→</span>
            <span className="rd-diff-new">{row.new || '—'}</span>
          </span>
        </div>
      ))}
    </div>
  )
}

import './pageTabs.css'

/* Page-level category tabs — one row of pills that splits a long admin screen
   into the few things somebody actually came to do.

   Lifted from System Settings, which is where this pattern was established and
   which still owns the look. It is shared rather than copied because three more
   screens now need it (User Management, Parking Space Management, Operations
   Center) and four hand-rolled tab strips would drift apart on padding, radius
   and active colour within a release.

   Deliberately *not* the same thing as the tabs already inside some of those
   pages. User Management's role tabs pick which rows a table shows; these pick
   which section of the page you are looking at. They read differently on
   purpose — these are raised pills above the panel, those are underlined text
   inside it — so a screen with both does not look like it has two of the same
   control.

   tabs: [{ id, label, icon?, count?, dot? }]
     count  a number shown as a pill on the tab. Renders only when > 0, so an
            empty queue costs nothing visually — a "0" badge reads as a thing
            needing attention when it is the opposite.
     dot    a small marker for "something in here is unsaved/unread". */
export default function PageTabs({ tabs, active, onChange, ariaLabel, id = 'pt' }) {
  return (
    <div className="pt-tabs" role="tablist" aria-label={ariaLabel}>
      {tabs.map(({ id: tabId, label, icon: Icon, count, dot }) => (
        <button
          key={tabId}
          type="button"
          role="tab"
          id={`${id}-tab-${tabId}`}
          aria-selected={active === tabId}
          aria-controls={`${id}-tabpanel`}
          className={`pt-tab ${active === tabId ? 'pt-tab--active' : ''}`}
          onClick={() => onChange(tabId)}
        >
          {Icon && <Icon size={15} />}
          <span className="pt-tab-label">{label}</span>
          {count > 0 && <span className="pt-tab-count">{count}</span>}
          {dot && <span className="pt-tab-dot" aria-label="Unsaved changes" title="Unsaved changes" />}
        </button>
      ))}
    </div>
  )
}

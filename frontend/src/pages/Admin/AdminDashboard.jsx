import { useState, useEffect, useCallback } from 'react'
import { useLiveUpdates } from '../../realtime/useLiveUpdates'
import { usersApi } from '../../api/users'
import {
  Users, ShieldCheck, ClipboardList,
  Activity, Shield, RefreshCw, CheckCircle, XCircle,
  AlertTriangle, Car as CarIcon, Inbox, BarChart2, PieChart as PieIcon,
} from 'lucide-react'
import {
  PieChart, Pie, Cell, BarChart, Bar, XAxis, YAxis,
  CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts'
import './AdminDashboard.css'

// ── Shared chart config ────────────────────────────────────────────────────────

const TOOLTIP_STYLE = {
  background: '#fff',
  border: '1px solid #D3E1EC',
  borderRadius: 10,
  fontSize: 12,
  boxShadow: '0 4px 16px rgba(3, 57, 108,0.08)',
}

// ── Chart palette ───────────────────────────────────────────────────────────────
// Colour-blind-safe by construction: every ordering used below was checked with
// the dataviz validator (adjacent-pair CVD ΔE ≥ 8, normal-vision ≥ 15). Every chart
// also renders a labelled legend, so identity never rests on colour alone.
//
// STATUS hues carry meaning and are reused consistently across charts —
// Authorized is always green, Denied always red, Pending always amber.
//
// Pending was #fab219, which failed the validator's lightness band (L 0.811,
// outside 0.43–0.77) and sat at 1.79:1 against a white card — a pale amber bar
// that was genuinely hard to see. #b87d00 passes lightness, chroma, contrast
// (≥3:1) and the normal-vision floor.
//
// The green↔amber↔red triad cannot pass adjacent-pair CVD separation at any
// step — that collision IS red-green colour blindness, and re-stepping only
// moves it (green↔red scores worse still). Per the skill's rule for reserved
// status colours, identity never rests on hue here: every status row carries
// its own icon and a written label and count.
const STATUS = {
  good:     '#0ca30c',   // authorized / accepted
  warning:  '#b87d00',   // pending
  critical: '#d03b3b',   // denied / rejected
}
// CAT hues are identity keys only, assigned per entity in a fixed order (never
// cycled). `muted` is the reserved neutral for "Other" / disabled slices.
const CAT = {
  blue:    '#2a78d6',
  orange:  '#eb6834',
  aqua:    '#1baf7a',
  yellow:  '#eda100',
  magenta: '#e87ba4',
  green:   '#008300',
  violet:  '#4a3aa7',
  muted:   '#898781',
}

// ── Breakdown (part-to-whole) ─────────────────────────────────────────────────
// Replaces the donuts this page used to draw. A donut costs ~200px of height to
// say what one 10px bar says. A small one sitting BESIDE its legend rather than
// above it costs about the same height as the bar did, so the layout stays
// short.
//
// The ring carries the share, so the rows carry only the name and the count.
// Printing "40%" and "6" side by side gave every row two competing right-aligned
// number columns, which is what made the numbers look busy — the exact
// percentage is on hover, where a precise figure is actually wanted.
//
// Every row is labelled and numbered, so identity never depends on the colour —
// which is also the relief the palette validator requires for the few hues that
// sit under 3:1 against a white card.

function Breakdown({ slices, total, emptyMessage }) {
  const sum = slices.reduce((s, d) => s + d.value, 0)
  const whole = total ?? sum

  if (sum === 0) {
    return <p className="ad-breakdown-empty">{emptyMessage}</p>
  }

  const pct = (v) => (whole > 0 ? Math.round((v / whole) * 100) : 0)

  // A total larger than the slices means there is an unclassified remainder.
  // Draw it as a neutral gap so the ring stays a true part-to-whole instead of
  // silently rescaling the slices to fill the circle.
  const remainder = Math.max(0, whole - sum)
  const ringData = remainder > 0
    ? [...slices, { name: 'Unclassified', value: remainder, color: '#E3ECF4' }]
    : slices

  return (
    <div className="ad-breakdown">
      <div className="ad-breakdown-chart">
        <PieChart width={104} height={104}>
          <Pie
            data={ringData}
            cx="50%"
            cy="50%"
            innerRadius={31}
            outerRadius={50}
            paddingAngle={ringData.length > 1 ? 2 : 0}
            dataKey="value"
            stroke="#fff"
            strokeWidth={2}
            isAnimationActive={false}
          >
            {ringData.map((s, i) => <Cell key={i} fill={s.color} />)}
          </Pie>
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            formatter={(val, name) => [`${val} (${pct(val)}%)`, name]}
          />
        </PieChart>
        <div className="ad-breakdown-center">
          <span className="ad-breakdown-center-val">{whole}</span>
        </div>
      </div>

      <ul className="ad-breakdown-rows">
        {slices.map((s, i) => (
          <li key={i} className="ad-breakdown-row" title={`${s.name}: ${s.value} (${pct(s.value)}%)`}>
            {s.Icon
              ? <s.Icon size={13} style={{ color: s.color, flexShrink: 0 }} />
              : <span className="ad-breakdown-dot" style={{ background: s.color }} />}
            <span className="ad-breakdown-name">{s.name}</span>
            <span className="ad-breakdown-val">{s.value}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

// ── Bar Chart ─────────────────────────────────────────────────────────────────

function DayBarChart({ data, weekTotal }) {
  if (!data || data.length === 0) {
    return <p className="ad-breakdown-empty">No entries recorded this week.</p>
  }
  return (
    <div className="ad-bar-wrap">
      {weekTotal != null && (
        <div className="ad-bar-summary">
          <span className="ad-bar-summary-val">{weekTotal}</span>
          <span className="ad-bar-summary-label">vehicles let in this week</span>
        </div>
      )}
      <ResponsiveContainer width="100%" height={150}>
        <BarChart data={data} margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
          {/* Solid hairline: a dashed grid reads as a threshold line when it is
              only a grid. */}
          <CartesianGrid stroke="#EEF4F9" vertical={false} />
          <XAxis
            dataKey="day"
            tick={{ fontSize: 11.5, fill: '#5C7B92', fontWeight: 600 }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            tick={{ fontSize: 11, fill: '#9DB6C9' }}
            axisLine={false}
            tickLine={false}
            allowDecimals={false}
          />
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            cursor={{ fill: '#EEF4F9', radius: 4 }}
            formatter={(val) => [val, 'Entries']}
          />
          <Bar dataKey="count" name="Entries" fill="#03396C" radius={[6, 6, 0, 0]} maxBarSize={48} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

// ── Registrations-by-day Chart ────────────────────────────────────────────────
// Stacked accepted/pending registrations per campus day (Mon–Sat) against the
// daily slot capacity (dashed line).

const DAY_ABBREV = {
  Monday: 'Mon', Tuesday: 'Tue', Wednesday: 'Wed',
  Thursday: 'Thu', Friday: 'Fri', Saturday: 'Sat',
}

function DayRegistrationChart({ data }) {
  if (!data || data.length === 0) {
    return <p className="ad-breakdown-empty">No registrations to show yet.</p>
  }
  const capacity = data[0]?.capacity ?? 0
  const rows = data.map(d => ({ ...d, label: DAY_ABBREV[d.day] || d.day }))
  const busiest = rows.reduce((a, b) => (a.accepted + a.pending >= b.accepted + b.pending ? a : b), rows[0])
  const busiestUsed = busiest.accepted + busiest.pending
  return (
    <div className="ad-bar-wrap">
      <div className="ad-bar-summary">
        <span className="ad-bar-summary-val">{busiestUsed}</span>
        <span className="ad-bar-summary-label">
          of {capacity} used on {busiest.day}, the busiest day
        </span>
      </div>
      <ResponsiveContainer width="100%" height={150}>
        <BarChart data={rows} margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
          {/* Solid hairline grid; only the capacity line below is dashed, because
              that one really is a threshold. */}
          <CartesianGrid stroke="#EEF4F9" vertical={false} />
          <XAxis
            dataKey="label"
            tick={{ fontSize: 11.5, fill: '#5C7B92', fontWeight: 600 }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            tick={{ fontSize: 11, fill: '#9DB6C9' }}
            axisLine={false}
            tickLine={false}
            allowDecimals={false}
            domain={[0, dataMax => Math.max(dataMax, capacity)]}
          />
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            cursor={{ fill: '#EEF4F9', radius: 4 }}
            formatter={(val, name) => [val, name]}
            labelFormatter={(label, payload) => {
              const row = payload?.[0]?.payload
              return row ? `${row.day} — ${row.accepted + row.pending}/${row.capacity} slots used` : label
            }}
          />
          <Bar dataKey="accepted" stackId="regs" name="Accepted" fill={STATUS.good} maxBarSize={48} />
          <Bar dataKey="pending"  stackId="regs" name="Pending"  fill={STATUS.warning} radius={[6, 6, 0, 0]} maxBarSize={48} />
          <ReferenceLine y={capacity} stroke={STATUS.critical} strokeDasharray="4 4" />
        </BarChart>
      </ResponsiveContainer>
      {/* Inline, one line — a stacked legend cost 72px under a 150px chart. */}
      <div className="ad-chart-legend">
        <span className="ad-chart-legend-item">
          <span className="ad-breakdown-dot" style={{ background: STATUS.good }} />
          Approved
        </span>
        <span className="ad-chart-legend-item">
          <span className="ad-breakdown-dot" style={{ background: STATUS.warning }} />
          Waiting
        </span>
        <span className="ad-chart-legend-item">
          <span className="ad-chart-legend-rule" style={{ borderColor: STATUS.critical }} />
          Daily limit ({capacity})
        </span>
      </div>
    </div>
  )
}

// ── Chart Card wrapper ────────────────────────────────────────────────────────

function ChartCard({ icon: Icon, title, subtitle, children }) {
  return (
    <div className="ad-chart-card">
      <div className="ad-chart-head">
        <h2 className="ad-section-title">
          <Icon size={15} />
          {title}
        </h2>
        {subtitle && <span className="ad-chart-subtitle">{subtitle}</span>}
      </div>
      <div className="ad-chart-body">
        {children}
      </div>
    </div>
  )
}

// ── KPI Strip ─────────────────────────────────────────────────────────────────

function KpiStrip({ items }) {
  return (
    <div className="ad-kpi-strip">
      {items.map((item, i) => (
        <div key={i} className="ad-kpi-item">
          <div className="ad-kpi-icon" style={{ background: item.color + '18', color: item.color }}>
            <item.icon size={14} />
          </div>
          <div className="ad-kpi-text">
            <span className="ad-kpi-val">{item.value ?? '—'}</span>
            <span className="ad-kpi-label">{item.label}</span>
            {item.sub && <span className="ad-kpi-sub">{item.sub}</span>}
          </div>
        </div>
      ))}
    </div>
  )
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function SectionLabel({ children, live }) {
  return (
    <div className="ad-section-label">
      {live && <span className="ad-live-dot" />}
      {children}
    </div>
  )
}

function relativeTime(iso) {
  const diffMs = Date.now() - new Date(iso).getTime()
  const mins = Math.round(diffMs / 60000)
  if (mins < 1)   return 'just now'
  if (mins < 60)  return `${mins}m ago`
  const hours = Math.round(mins / 60)
  if (hours < 24) return `${hours}h ago`
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function ActivityItem({ log }) {
  // Audit details are pipe-delimited ("Plate: X | Owner: Y | …") — render as chips
  const chips = (log.details || '').split('|').map(s => s.trim()).filter(Boolean)
  return (
    <div className="ad-activity-item">
      <div className="ad-activity-dot" />
      <div className="ad-activity-content">
        <span className="ad-activity-text">
          <strong>{log.actor_name || 'Unknown'}</strong> — {log.action_label || log.action}
          <span className="ad-activity-time" style={{ marginLeft: 8 }}>{relativeTime(log.created_at)}</span>
        </span>
        {log.target_name && (
          <span className="ad-activity-target">Target: {log.target_name}</span>
        )}
        {chips.length > 0 && (
          <span style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 3 }}>
            {chips.map((c, i) => (
              <span key={i} style={{
                fontSize: 10.5, lineHeight: 1.4, padding: '2px 7px', borderRadius: 5,
                background: '#EEF4F9', color: '#4A6B85', border: '1px solid #D3E1EC',
                maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>
                {c}
              </span>
            ))}
          </span>
        )}
      </div>
    </div>
  )
}

function EmptyActivity({ message }) {
  return (
    <div className="ad-activity-empty">
      <Inbox size={30} strokeWidth={1.4} className="ad-activity-empty-icon" />
      <p>{message}</p>
    </div>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function AdminDashboard() {
  const [stats, setStats] = useState(null)
  const [loading, setLoading] = useState(true)
  const [lastUpdated, setLastUpdated] = useState(null)

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const data = await usersApi.getDashboardStats()
      setStats(data)
      setLastUpdated(new Date())
    } catch {
      // ignore
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchData() }, [fetchData])

  // Instant refresh on any data change (dashboard aggregates everything)
  useLiveUpdates(fetchData)

  // Keep the dashboard live — silent refresh every 60s (spinner only shows on first load)
  useEffect(() => {
    const timer = setInterval(fetchData, 60000)
    return () => clearInterval(timer)
  }, [fetchData])

  const lastUpdatedStr = lastUpdated
    ? lastUpdated.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
    : null

  const today = new Date().toLocaleDateString('en-US', {
    weekday: 'long', month: 'long', day: 'numeric'
  })

  // ── Derived chart data ───────────────────────────────────────────────────────

  // Application outcomes — reserved status hues. Each row also carries its own
  // icon, because green/amber/red cannot be told apart by hue under red-green
  // colour blindness at any step.
  const vehicleSlices = stats ? [
    { name: 'Approved',        value: stats.registrations?.accepted ?? 0, color: STATUS.good,     Icon: CheckCircle },
    { name: 'Waiting for you', value: stats.registrations?.pending  ?? 0, color: STATUS.warning,  Icon: ClipboardList },
    { name: 'Rejected',        value: stats.registrations?.rejected ?? 0, color: STATUS.critical, Icon: XCircle },
  ].filter(s => s.value > 0) : []

  // Registered categories — one distinct hue per type; disabled uses the muted
  // neutral. Validated order: blue, orange, aqua, yellow, (muted).
  const userSlices = stats ? [
    { name: 'Students',  value: stats.owners?.student   ?? 0, color: CAT.blue },
    { name: 'Employees', value: stats.owners?.employee  ?? 0, color: CAT.orange },
    { name: 'Fetchers',  value: stats.owners?.fetcher   ?? 0, color: CAT.aqua },
    { name: 'Suppliers', value: stats.suppliers?.active ?? 0, color: CAT.yellow },
    { name: 'Disabled',  value: stats.owners?.disabled  ?? 0, color: CAT.muted },
    { name: 'Archived',  value: stats.owners?.archived  ?? 0, color: CAT.violet },
  ].filter(s => s.value > 0) : []

  // Today's entry outcomes. Authorized/Denied keep their status hues; the rest
  // take distinct categorical hues. Order is arranged so no two hard-to-separate
  // hues sit adjacent (validated: green, blue, orange, violet, yellow, red, muted).
  const scanSlices = stats ? (() => {
    const s = stats.scans?.today_by_status ?? {}
    return [
      { name: 'Authorized',   value: stats.scans?.registered_today ?? 0, color: STATUS.good },
      { name: 'Exited',       value: s.exited ?? 0,                      color: CAT.blue },
      { name: 'Wrong Day',    value: s.wrong_day ?? 0,                   color: CAT.orange },
      { name: 'Visitor',      value: stats.scans?.visitor_today ?? 0,    color: CAT.violet },
      { name: 'Unregistered', value: s.unknown ?? 0,                     color: CAT.yellow },
      { name: 'Denied',       value: s.denied ?? 0,                      color: STATUS.critical },
      { name: 'Unreadable',   value: s.unreadable ?? 0,                  color: CAT.muted },
    ].filter(sl => sl.value > 0)
  })() : []

  const VEHICLE_TYPE_LABELS = { car: 'Car', motorcycle: 'Motorcycle', ebike: 'E-Bike', van: 'Van', truck: 'Truck', bus: 'Bus' }
  // Vehicle types — six distinct hues in validated order, plus the reserved
  // neutral for everything else.
  //
  // The tail bucket is not cosmetic. vehicle_type is free text, so values
  // outside this list exist in the data ("SUV"). Matching only the known keys
  // dropped them, and the card claimed a total it was not drawing — 9 vehicles
  // above a bar built from 4. Anything unrecognised now lands in "Other", so
  // the slices always reconcile with the total.
  const vehicleTypeSlices = stats ? (() => {
    const byType = stats.vehicles?.by_type ?? {}
    const known = [
      { key: 'car',        color: CAT.blue },
      { key: 'motorcycle', color: CAT.orange },
      { key: 'ebike',      color: CAT.aqua },
      { key: 'van',        color: CAT.yellow },
      { key: 'truck',      color: CAT.magenta },
      { key: 'bus',        color: CAT.green },
    ]
    const slices = known
      .map(t => ({ name: VEHICLE_TYPE_LABELS[t.key], value: byType[t.key] ?? 0, color: t.color }))
      .filter(s => s.value > 0)

    const knownKeys = new Set(known.map(t => t.key))
    const other = Object.entries(byType)
      .filter(([k]) => !knownKeys.has(k))
      .reduce((sum, [, v]) => sum + v, 0)
    if (other > 0) slices.push({ name: 'Other', value: other, color: CAT.muted })

    return slices
  })() : []

  const VIOLATION_TYPE_LABELS = {
    unauthorized_entry: 'Unauthorized Entry', double_parking: 'Double Parking',
    time_exceed: 'Time Exceed', no_sticker: 'No Sticker',
    expired_registration: 'Expired Registration', unauthorized: 'Unauthorized (Legacy)', other: 'Other',
  }
  // Violation types — distinct hues; the most severe (unauthorized entry) leads
  // in red, "other" uses the muted neutral. Order validated so red and orange
  // are not adjacent (red, yellow, blue, aqua, violet, magenta, muted).
  const violationTypeSlices = stats ? [
    { key: 'unauthorized_entry',   color: STATUS.critical },
    { key: 'double_parking',       color: CAT.yellow },
    { key: 'time_exceed',          color: CAT.blue },
    { key: 'no_sticker',           color: CAT.aqua },
    { key: 'expired_registration', color: CAT.violet },
    { key: 'unauthorized',         color: CAT.magenta },
    { key: 'other',                color: CAT.muted },
  ].map(t => ({ name: VIOLATION_TYPE_LABELS[t.key], value: stats.violations?.by_type?.[t.key] ?? 0, color: t.color }))
    .filter(s => s.value > 0) : []

  // Labels are written as a plain answer to "what is this number?", because the
  // people reading this dashboard are not all CDSO staff. Every tile is styled
  // identically — the icon chip carries the only colour, so the six read as one
  // strip rather than as two groups.
  const kpiItems = stats ? [
    { icon: Users,         label: 'People with accounts', value: stats.users?.total,                 color: '#03396C', sub: `${stats.users?.active ?? 0} can sign in` },
    { icon: CarIcon,       label: 'Vehicles registered',  value: stats.vehicles?.total,              color: '#0F7A5A', sub: `${stats.vehicles?.authorized ?? 0} cleared for entry` },
    { icon: ClipboardList, label: 'Applications to review', value: stats.registrations?.pending,     color: '#8A6B00', sub: 'waiting for approval' },
    { icon: AlertTriangle, label: 'Violations unresolved', value: stats.violations?.open,            color: '#C62828', sub: `${stats.violations?.fee_imposed ?? 0} have a fine to pay` },
    { icon: ShieldCheck,   label: 'Visitor passes today', value: stats.visitor_passes?.active_today, color: '#1072B3', sub: 'currently valid' },
    { icon: Activity,      label: 'Gate scans today',     value: stats.scans?.today,                 color: '#1072B3', sub: `${stats.scans?.week ?? 0} so far this week` },
  ] : []

  return (
    <>
      <div className="ad-page">
        <div className="ad-header">
          <div>
            <h1 className="ad-title">Dashboard Overview</h1>
            <p className="ad-subtitle">
              {today}
              {lastUpdatedStr && <span className="ad-last-updated"> · Updated at {lastUpdatedStr}</span>}
            </p>
          </div>
          <button className="ad-refresh-btn" onClick={fetchData} disabled={loading} title="Refresh">
            <RefreshCw size={14} className={loading ? 'ad-spin' : ''} />
            <span>Refresh</span>
          </button>
        </div>

        {loading && !stats ? (
          <div className="ad-loading">
            <div className="ad-spinner" />
            <p>Loading dashboard…</p>
          </div>
        ) : (
          <>
            {/* ── Compact KPI strip ──────────────────────────────────── */}
            {kpiItems.length > 0 && <KpiStrip items={kpiItems} />}

            {/* ── Breakdowns & charts ────────────────────────────────── */}
            {/* Two bands: five short part-to-whole cards on top, then the two
                real time/day charts. Each subtitle says what the card means in
                a sentence rather than naming the metric. */}
            <SectionLabel>The numbers, broken down</SectionLabel>
            <div className="ad-charts-grid">

              <ChartCard
                icon={PieIcon}
                title="Vehicle pass applications"
                subtitle={`${stats?.registrations?.total ?? 0} received in total`}
              >
                <Breakdown
                  slices={vehicleSlices}
                  total={stats?.registrations?.total}
                  emptyMessage="No applications have been submitted yet."
                />
              </ChartCard>

              <ChartCard
                icon={Users}
                title="Who is registered"
                subtitle={`${stats?.owners?.total ?? 0} vehicle owners and ${stats?.suppliers?.active ?? 0} supplier${(stats?.suppliers?.active ?? 0) === 1 ? '' : 's'}`}
              >
                <Breakdown
                  slices={userSlices}
                  emptyMessage="Nobody is registered yet."
                />
              </ChartCard>

              <ChartCard
                icon={CarIcon}
                title="Kinds of vehicle"
                subtitle={`${stats?.vehicles?.total ?? 0} registered in total`}
              >
                <Breakdown
                  slices={vehicleTypeSlices}
                  total={stats?.vehicles?.total}
                  emptyMessage="No vehicles registered yet."
                />
              </ChartCard>

              <ChartCard
                icon={Activity}
                title="What happened at the gate today"
                subtitle={`${stats?.scans?.today ?? 0} scan${(stats?.scans?.today ?? 0) === 1 ? '' : 's'} so far`}
              >
                <Breakdown
                  slices={scanSlices}
                  total={stats?.scans?.today}
                  emptyMessage="No vehicles have been scanned today."
                />
              </ChartCard>

              <ChartCard
                icon={AlertTriangle}
                title="Violations in the last 30 days"
                subtitle={`${stats?.violations?.open ?? 0} still unresolved`}
              >
                <Breakdown
                  slices={violationTypeSlices}
                  emptyMessage="No violations in the last 30 days."
                />
              </ChartCard>

              <ChartCard
                icon={BarChart2}
                title="Vehicles let in each day this week"
                subtitle="Monday to Saturday"
              >
                <DayBarChart
                  data={stats?.day_distribution}
                  weekTotal={stats?.scans?.authorized_week}
                />
              </ChartCard>

              <ChartCard
                icon={BarChart2}
                title="How full each campus day is"
                subtitle="Approved and waiting applications against the daily limit"
              >
                <DayRegistrationChart data={stats?.day_registrations} />
              </ChartCard>

            </div>

            {/* ── Recent Activity ────────────────────────────────────── */}
            <SectionLabel>What people have been doing</SectionLabel>
            <div className="ad-activity-section">
              <div className="ad-activity-grid">
                <div className="ad-activity-card">
                  <div className="ad-activity-card-head">
                    <Shield size={13} />
                    <span>Office staff</span>
                  </div>
                  <div className="ad-activity-list">
                    {stats?.recent_activity?.admin?.length > 0
                      ? stats.recent_activity.admin.map(log => <ActivityItem key={log.id} log={log} />)
                      : <EmptyActivity message="Nothing from the office yet." />}
                  </div>
                </div>

                <div className="ad-activity-card">
                  <div className="ad-activity-card-head">
                    <ShieldCheck size={13} />
                    <span>Guards at the gate</span>
                  </div>
                  <div className="ad-activity-list">
                    {stats?.recent_activity?.security?.length > 0
                      ? stats.recent_activity.security.map(log => <ActivityItem key={log.id} log={log} />)
                      : <EmptyActivity message="Nothing from the gates yet." />}
                  </div>
                </div>
              </div>
            </div>
          </>
        )}
      </div>
    </>
  )
}

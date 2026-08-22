import { useState } from 'react'
import { FileText, Download, Calendar, Loader2, FileBarChart2 } from 'lucide-react'
import { toast } from 'sonner'
import { reportFileName } from '../utils/reportName'
import './ReportExportBar.css'

// Reusable report controls: Date From / Date To (validated) + branded PDF/Excel
// download. `fetchBlob(format, params)` must return a Promise<Blob>, where
// format is 'pdf' | 'excel' and params carries date_from / date_to.
//
// `extraReports` hangs additional one-click PDFs off the same date range —
// a page that also wants a counts-only summary should not need a second date
// picker that can silently disagree with this one. Each entry is
// { key, label, fileBase?, fetch(params) -> Promise<Blob> }.
export default function ReportExportBar({ label = 'Report', fetchBlob, extraReports = [] }) {
  const [from, setFrom] = useState('')
  const [to, setTo]     = useState('')
  const [busy, setBusy] = useState(null) // 'pdf' | 'excel' | null
  const today = new Date().toISOString().slice(0, 10)

  const dateParams = () => {
    const params = {}
    if (from) params.date_from = from
    if (to)   params.date_to   = to
    return params
  }

  const download = async (key, fetch, fileName, successLabel) => {
    setBusy(key)
    try {
      const blob = await fetch(dateParams())
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = fileName
      a.click()
      URL.revokeObjectURL(url)
      toast.success(`${successLabel} downloaded.`)
    } catch {
      toast.error('Failed to generate report.')
    } finally {
      setBusy(null)
    }
  }

  const run = (format) => download(
    format,
    (params) => fetchBlob(format, params),
    reportFileName(label, format === 'excel' ? 'xlsx' : 'pdf'),
    `${format === 'excel' ? 'Excel' : 'PDF'} report`,
  )

  return (
    <div className="report-bar">
      <span className="report-bar-label"><FileBarChart2 size={14} /> {label}</span>
      <div className="report-bar-dates">
        <Calendar size={13} />
        <input
          type="date"
          className="report-bar-date"
          value={from}
          max={to || today}
          onChange={(e) => { const v = e.target.value; if (v && to && v > to) setTo(v); setFrom(v) }}
          title="Date From"
          aria-label="Date From"
        />
        <span className="report-bar-sep">to</span>
        <input
          type="date"
          className="report-bar-date"
          value={to}
          min={from || undefined}
          max={today}
          onChange={(e) => { const v = e.target.value; if (v && from && v < from) setFrom(v); setTo(v) }}
          title="Date To"
          aria-label="Date To"
        />
      </div>
      <button className="report-bar-btn report-bar-btn--pdf" disabled={busy !== null} onClick={() => run('pdf')}>
        {busy === 'pdf' ? <Loader2 size={14} className="report-bar-spin" /> : <FileText size={14} />} PDF
      </button>
      <button className="report-bar-btn report-bar-btn--excel" disabled={busy !== null} onClick={() => run('excel')}>
        {busy === 'excel' ? <Loader2 size={14} className="report-bar-spin" /> : <Download size={14} />} Excel
      </button>
      {extraReports.map((r) => (
        <button
          key={r.key}
          className="report-bar-btn report-bar-btn--summary"
          disabled={busy !== null}
          onClick={() => download(r.key, r.fetch, reportFileName(r.fileBase || r.label, 'pdf'), r.label)}
        >
          {busy === r.key ? <Loader2 size={14} className="report-bar-spin" /> : <FileBarChart2 size={14} />} {r.label}
        </button>
      ))}
    </div>
  )
}

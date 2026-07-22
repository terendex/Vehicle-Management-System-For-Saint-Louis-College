import { useState } from 'react'
import { FileText, Download, Calendar, Loader2, FileBarChart2 } from 'lucide-react'
import { toast } from 'sonner'
import { reportFileName } from '../utils/reportName'
import './ReportExportBar.css'

// Reusable report controls: Date From / Date To (validated) + branded PDF/Excel
// download. `fetchBlob(format, params)` must return a Promise<Blob>, where
// format is 'pdf' | 'excel' and params carries date_from / date_to.
export default function ReportExportBar({ label = 'Report', fetchBlob }) {
  const [from, setFrom] = useState('')
  const [to, setTo]     = useState('')
  const [busy, setBusy] = useState(null) // 'pdf' | 'excel' | null
  const today = new Date().toISOString().slice(0, 10)

  const run = async (format) => {
    setBusy(format)
    try {
      const params = {}
      if (from) params.date_from = from
      if (to)   params.date_to   = to
      const blob = await fetchBlob(format, params)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = reportFileName(label, format === 'excel' ? 'xlsx' : 'pdf')
      a.click()
      URL.revokeObjectURL(url)
      toast.success(`${format === 'excel' ? 'Excel' : 'PDF'} report downloaded.`)
    } catch {
      toast.error('Failed to generate report.')
    } finally {
      setBusy(null)
    }
  }

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
    </div>
  )
}

import { useState } from 'react'
import { FileText, Download, Calendar, Loader2, FileBarChart2 } from 'lucide-react'
import { toast } from './Feedback/notify'
import { reportFileName } from '../utils/reportName'
import { openReportTab, downloadBlob } from '../utils/saveFile'
import { confirmPdfExport } from '../utils/confirmReport'
import './ReportExportBar.css'

// Reusable report controls: Date From / Date To (validated) + branded PDF/Excel
// download. `fetchBlob(format, params)` must return a Promise<Blob>, where
// format is 'pdf' | 'excel' and params carries date_from / date_to.
//
// `extraReports` hangs additional one-click PDFs off the same date range —
// a page that also wants a counts-only summary should not need a second date
// picker that can silently disagree with this one. Each entry is
// { key, label, fileBase?, fetch(params) -> Promise<Blob> }.
// `filters` carries the screen's own active filters into every export, so a
// report downloaded while the table is narrowed to Employees contains
// employees. Without it the export only ever carried the date range, and a
// filtered page still produced an unfiltered report - which reads as the
// report being wrong rather than as the filter not having been sent.
// `activeFilterSummary` names them on the bar, so it is clear before clicking
// what the file will contain.
// `onRangeChange({ from, to })` lets the screen apply this same date range to
// what it is showing. Without it these two boxes narrow the downloaded file
// only, so a table showing rows could hand back an empty report and read as
// the export being broken. Optional: a screen that does not pass it behaves
// exactly as before.
export default function ReportExportBar({ label = 'Report', fetchBlob, extraReports = [], filters = {}, activeFilterSummary = '', onRangeChange, recordCount }) {
  const [from, setFrom] = useState('')
  const [to, setTo]     = useState('')
  const [busy, setBusy] = useState(null) // 'pdf' | 'excel' | null
  const today = new Date().toISOString().slice(0, 10)

  const dateParams = () => {
    // Filters first, dates second: the date pickers belong to this bar and
    // must win if a screen ever passes a date of its own.
    const params = {}
    for (const [key, value] of Object.entries(filters)) {
      // 'all' is the screens' own "no filter" value and '' is an empty box;
      // neither should travel as a real query parameter.
      if (value !== undefined && value !== null && value !== '' && value !== 'all') {
        params[key] = value
      }
    }
    if (from) params.date_from = from
    if (to)   params.date_to   = to
    return params
  }

  // A PDF is opened for reading, an Excel file still downloads - there is
  // nothing to preview in a spreadsheet. `isPdf` is decided from the filename
  // so both callers of this helper (the two format buttons and each
  // extraReports entry) get it right.
  //
  // Order of the next three lines is the whole trick. openReportTab has to run
  // before the fetch below, while the click is still a user gesture, or the
  // popup is blocked - but the confirm has to run before THAT, or cancelling
  // leaves a blank tab open. Confirming is itself a click, so the tab still
  // opens inside the activation that click grants; if a browser disagrees,
  // show() returns false and the report downloads instead of opening, which
  // is the same fallback a blocked popup already took.
  const download = async (key, fetch, fileName, successLabel, reportName = label) => {
    const isPdf = fileName.toLowerCase().endsWith('.pdf')
    if (isPdf && !(await confirmPdfExport({
      label: reportName, summary: activeFilterSummary, from, to, count: recordCount,
    }))) return
    const tab = isPdf ? openReportTab() : null
    setBusy(key)
    try {
      const blob = await fetch(dateParams())
      if (tab && tab.show(blob)) {
        toast.success(`${successLabel} opened in a new tab.`)
      } else {
        downloadBlob(blob, fileName)
        toast.success(`${successLabel} downloaded.`)
      }
    } catch {
      tab?.close()
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
      <span className="report-bar-label">
        <FileBarChart2 size={14} /> {label}
        {activeFilterSummary && (
          <span className="report-bar-filters" title="These filters are applied to the exported report">
            {activeFilterSummary}
          </span>
        )}
      </span>
      <div className="report-bar-dates">
        <Calendar size={13} />
        <input
          type="date"
          className="report-bar-date"
          value={from}
          max={to || today}
          onChange={(e) => {
            const v = e.target.value
            const nextTo = (v && to && v > to) ? v : to
            if (nextTo !== to) setTo(nextTo)
            setFrom(v)
            onRangeChange?.({ from: v, to: nextTo })
          }}
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
          onChange={(e) => {
            const v = e.target.value
            const nextFrom = (v && from && v < from) ? v : from
            if (nextFrom !== from) setFrom(nextFrom)
            setTo(v)
            onRangeChange?.({ from: nextFrom, to: v })
          }}
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
          onClick={() => download(r.key, r.fetch, reportFileName(r.fileBase || r.label, 'pdf'), r.label, r.label)}
        >
          {busy === r.key ? <Loader2 size={14} className="report-bar-spin" /> : <FileBarChart2 size={14} />} {r.label}
        </button>
      ))}
    </div>
  )
}

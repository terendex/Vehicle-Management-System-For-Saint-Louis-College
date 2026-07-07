import { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { Bell, AlertTriangle, Car, CheckCheck, Trash2 } from 'lucide-react'
import { formatDistanceToNow } from 'date-fns'
import { toast } from 'sonner'
import { getNotifications, markNotificationsRead, clearNotifications } from '../api/notifications'
import { useLiveUpdates } from '../realtime/useLiveUpdates'
import './NotificationBell.css'

const TOAST_BY_SEVERITY = {
  critical: toast.error,
  warning:  toast.warning,
  info:     toast.info,
}

function timeAgo(ts) {
  try { return formatDistanceToNow(new Date(ts), { addSuffix: true }) } catch { return '' }
}

const CATEGORY_ICONS = {
  violation:    AlertTriangle,
  registration: Car,
}

export default function NotificationBell() {
  const [open, setOpen]               = useState(false)
  const [items, setItems]             = useState([])
  const [unreadCount, setUnreadCount] = useState(0)
  const wrapRef = useRef(null)
  const maxSeenIdRef = useRef(null) // highest notification id already shown — null until first load
  const navigate = useNavigate()

  const fetchNotifications = useCallback(async () => {
    try {
      const { data } = await getNotifications({ limit: 30 })
      const results = data.results || []
      setItems(results)
      setUnreadCount(data.unread_count || 0)

      // Mini pop-ups for notifications that arrived since the last fetch.
      // The first load only records the watermark — no toast storm on login.
      const maxId = results.reduce((m, n) => Math.max(m, n.id), 0)
      if (maxSeenIdRef.current !== null) {
        const fresh = results.filter(n => n.id > maxSeenIdRef.current && !n.is_read)
        if (fresh.length > 3) {
          toast.info(`${fresh.length} new notifications`, {
            description: 'Open the bell to review them.',
          })
        } else {
          // results are newest-first; toast oldest-first so they stack in order
          fresh.slice().reverse().forEach(n => {
            const show = TOAST_BY_SEVERITY[n.severity] || toast.info
            show(n.title, { description: n.message || undefined })
          })
        }
      }
      maxSeenIdRef.current = Math.max(maxSeenIdRef.current ?? 0, maxId)
    } catch {
      /* bell is non-critical — stay quiet on fetch errors */
    }
  }, [])

  useEffect(() => { fetchNotifications() }, [fetchNotifications])

  // Refetch whenever the backend signals a notification change (new violation,
  // registration event, or another admin marking items read).
  useLiveUpdates(fetchNotifications, 'notification')

  // Close on outside click / Escape
  useEffect(() => {
    if (!open) return
    const onClick = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false)
    }
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const handleMarkAll = async () => {
    try {
      await markNotificationsRead({ all: true })
      fetchNotifications()
    } catch { /* ignore */ }
  }

  const handleClearAll = async () => {
    // Optimistic clear so the panel empties instantly; watermark stays so we
    // don't re-toast anything that gets recreated server-side.
    setItems([])
    setUnreadCount(0)
    try {
      await clearNotifications()
      toast.success('Notifications cleared.')
    } catch {
      toast.error('Failed to clear notifications.')
    } finally {
      fetchNotifications()
    }
  }

  const handleItemClick = async (n) => {
    if (!n.is_read) {
      try { await markNotificationsRead({ ids: [n.id] }) } catch { /* ignore */ }
      fetchNotifications()
    }
    setOpen(false)
    if (n.link) navigate(n.link)
  }

  return (
    <div className="notif-bell" ref={wrapRef}>
      <button
        className="notif-bell-btn"
        title="Notifications"
        aria-label={`Notifications${unreadCount ? ` (${unreadCount} unread)` : ''}`}
        onClick={() => setOpen((v) => !v)}
      >
        <Bell size={18} />
        {unreadCount > 0 && (
          <span className="notif-badge">{unreadCount > 99 ? '99+' : unreadCount}</span>
        )}
      </button>

      {open && (
        <div className="notif-panel">
          <div className="notif-panel-head">
            <span className="notif-panel-title">Notifications</span>
            {unreadCount > 0 && (
              <button className="notif-mark-all" onClick={handleMarkAll}>
                <CheckCheck size={14} /> Mark all read
              </button>
            )}
          </div>

          <div className="notif-list">
            {items.length === 0 && (
              <div className="notif-empty">No notifications yet.</div>
            )}
            {items.map((n) => {
              const Icon = CATEGORY_ICONS[n.category] || Bell
              return (
                <button
                  key={n.id}
                  className={`notif-item ${n.is_read ? 'read' : 'unread'}`}
                  onClick={() => handleItemClick(n)}
                >
                  <span className={`notif-icon sev-${n.severity}`}>
                    <Icon size={15} />
                  </span>
                  <span className="notif-body">
                    <span className="notif-title">{n.title}</span>
                    {n.message && <span className="notif-message">{n.message}</span>}
                    <span className="notif-time">{timeAgo(n.created_at)}</span>
                  </span>
                  {!n.is_read && <span className="notif-dot" />}
                </button>
              )
            })}
          </div>

          {items.length > 0 && (
            <div className="notif-panel-foot">
              <button className="notif-clear-all" onClick={handleClearAll}>
                <Trash2 size={14} /> Clear all
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

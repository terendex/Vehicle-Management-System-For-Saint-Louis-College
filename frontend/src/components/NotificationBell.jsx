import { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { Bell, AlertTriangle, Car, CheckCheck } from 'lucide-react'
import { formatDistanceToNow } from 'date-fns'
import { getNotifications, markNotificationsRead } from '../api/notifications'
import { useLiveUpdates } from '../realtime/useLiveUpdates'
import './NotificationBell.css'

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
  const navigate = useNavigate()

  const fetchNotifications = useCallback(async () => {
    try {
      const { data } = await getNotifications({ limit: 30 })
      setItems(data.results || [])
      setUnreadCount(data.unread_count || 0)
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
        </div>
      )}
    </div>
  )
}

// The admin notification bell. Three calls, matching the three backend
// endpoints exactly — read, mark read, clear. No state of its own; the bell
// component owns that.
import api from './axios'

// Admin notification bell — violations & registration events
export const getNotifications = (params = {}) =>
  api.get('/accounts/notifications/', { params })

// `payload` is either { ids: [...] } or { all: true } — the server refuses
// anything else, so the shape is not validated here.
export const markNotificationsRead = (payload) =>
  api.post('/accounts/notifications/mark-read/', payload)

// Clear (delete) notifications. Pass { read_only: true } to keep unread ones.
// Defaults to {} — which the server reads as "clear everything", unread
// included. Passing nothing is therefore the destructive call, not the safe one.
export const clearNotifications = (payload = {}) =>
  api.post('/accounts/notifications/clear/', payload)

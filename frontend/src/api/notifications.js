import api from './axios'

// Admin notification bell — violations & registration events
export const getNotifications = (params = {}) =>
  api.get('/accounts/notifications/', { params })

export const markNotificationsRead = (payload) =>
  api.post('/accounts/notifications/mark-read/', payload)

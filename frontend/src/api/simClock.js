import api from './axios'

// The instructor demo's Test Clock (backend/sim_clock_views.py). The endpoint
// only exists on the local demo stack started with dev.ps1 -SimClock.
export const getTestClock = () => api.get('/system/test-clock/')
export const testClockAction = (action, extra = {}) => api.post('/system/test-clock/', { action, ...extra })

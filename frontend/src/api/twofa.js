import api from './axios'

/** Where the "remember this browser" token lives.
 *
 *  localStorage rather than a cookie: the campus and Railway halves are served
 *  from different origins, so a cookie set by one would not be sent by the
 *  other, and the user would be asked for a code every time they crossed over.
 *  The token is useless without the password, and the server binds it to the
 *  account's current password hash — changing the password kills it. */
const DEVICE_TOKEN_KEY = 'twofa_device_token'

export const deviceToken = {
  get: () => localStorage.getItem(DEVICE_TOKEN_KEY) || '',
  set: (token) => {
    if (token) localStorage.setItem(DEVICE_TOKEN_KEY, token)
  },
  clear: () => localStorage.removeItem(DEVICE_TOKEN_KEY),
}

export const twofaApi = {
  /** Begin (or re-do) enrollment. Pass `challenge` during a paused first login,
   *  or nothing at all when an already signed-in user is pairing a new phone. */
  setup: async (challenge) => {
    const { data } = await api.post('/accounts/2fa/setup/', challenge ? { challenge } : {})
    return data
  },

  /** Prove the app is paired. Completes a paused login when `challenge` is given. */
  confirm: async (code, challenge) => {
    const { data } = await api.post('/accounts/2fa/confirm/', { code, ...(challenge ? { challenge } : {}) })
    return data
  },

  /** Second half of a paused login. Supply either `code` or `backupCode`. */
  verify: async (challenge, { code, backupCode } = {}) => {
    const { data } = await api.post('/accounts/2fa/verify/', {
      challenge,
      ...(backupCode ? { backup_code: backupCode } : { code }),
    })
    return data
  },

  /** Trade a current code for ~10 minutes of authority over sensitive actions. */
  stepUp: async ({ code, backupCode } = {}) => {
    const { data } = await api.post('/accounts/2fa/step-up/',
      backupCode ? { backup_code: backupCode } : { code })
    return data
  },

  status: async () => {
    const { data } = await api.get('/accounts/2fa/status/')
    return data
  },

  regenerateBackupCodes: async () => {
    const { data } = await api.post('/accounts/2fa/backup-codes/', {})
    return data
  },

  /** CDSO clears a user's authenticator — the lost-phone path. */
  reset: async (userId) => {
    const { data } = await api.post(`/accounts/users/${userId}/2fa/reset/`, {})
    return data
  },
}

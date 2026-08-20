/**
 * The live step-up token, held in one place with no imports of its own.
 *
 * Both the axios request interceptor (which attaches it) and the 2FA store
 * (which sets it) need this value. The store imports axios, so axios cannot
 * import the store back — this leaf module breaks the cycle without resorting
 * to a global.
 *
 * In memory only, deliberately: persisting it would let a walk-up attacker
 * inherit the ten-minute window it exists to close.
 */

let token = ''
let expiresAt = 0

/** The token if it is still good, otherwise ''. The 5s of slack stops a token
 *  that expires in transit from causing a second prompt straight after the first. */
export function liveStepUpToken() {
  return token && Date.now() < expiresAt - 5000 ? token : ''
}

export function setStepUpToken(value, ttlSeconds) {
  token = value || ''
  expiresAt = value ? Date.now() + (ttlSeconds || 600) * 1000 : 0
  return expiresAt
}

export function clearStepUpToken() {
  token = ''
  expiresAt = 0
}

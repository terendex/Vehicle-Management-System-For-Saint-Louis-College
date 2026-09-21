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

// Module-scope state, which works because ES modules are singletons: every
// importer sees the same two variables. That is the whole mechanism — there is
// no store and no context here on purpose.
let token = ''
let expiresAt = 0                                // epoch ms; 0 means "no token"

/** The token if it is still good, otherwise ''. The 5s of slack stops a token
 *  that expires in transit from causing a second prompt straight after the first. */
export function liveStepUpToken() {
  // Returns '' rather than null, so the caller can use it directly in a
  // truthiness test without distinguishing "expired" from "never set".
  return token && Date.now() < expiresAt - 5000 ? token : ''
}

export function setStepUpToken(value, ttlSeconds) {
  token = value || ''                            // a falsy value clears rather than storing undefined
  // Computed from the CLIENT clock, so a badly skewed machine could believe a
  // token is still good after the server has expired it. The consequence is
  // one rejected request and a fresh prompt, not a security hole — the server
  // is what actually decides.
  expiresAt = value ? Date.now() + (ttlSeconds || 600) * 1000 : 0   // 600s default matches the server's window
  return expiresAt                               // returned so the caller can show a countdown
}

// Called on logout and when a step-up is explicitly abandoned. Both fields are
// reset, so a stale expiresAt cannot outlive the token it described.
export function clearStepUpToken() {
  token = ''
  expiresAt = 0
}

import crypto from 'node:crypto'

export const BASE = 'http://127.0.0.1:5199'
export const PASSWORD = 'Demo@2026!'
export const ACCOUNTS = {
  admin: { email: 'cdso.demo@slc-sflu.edu.ph', secret: 'JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP' },
  owner: { email: '20231001@slc-sflu.edu.ph', secret: 'KRSXG5CTMVRXEZLUKRSXG5CTMVRXEZLU' },
  guard: { email: 'guard.santos@slc-sflu.edu.ph' },
}

function base32(s) {
  const A = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'
  let bits = ''
  for (const c of s.replace(/=+$/, '')) bits += A.indexOf(c).toString(2).padStart(5, '0')
  const out = []
  for (let i = 0; i + 8 <= bits.length; i += 8) out.push(parseInt(bits.slice(i, i + 8), 2))
  return Buffer.from(out)
}

// The server rejects a replayed timestep, so each login uses a step not yet spent.
const spent = new Set()
export function totp(secret) {
  let step = Math.floor(Date.now() / 30000)
  while (spent.has(secret + step)) step += 1
  spent.add(secret + step)
  const buf = Buffer.alloc(8)
  buf.writeBigUInt64BE(BigInt(step))
  const h = crypto.createHmac('sha1', base32(secret)).update(buf).digest()
  const o = h[h.length - 1] & 15
  return String(((h.readUInt32BE(o) & 0x7fffffff) % 1e6)).padStart(6, '0')
}

export async function login(page, who, { beforeCode } = {}) {
  const acct = ACCOUNTS[who]
  if (who === 'guard') {
    await page.goto(`${BASE}/security/guard-login`)
    await page.click('.sqr-gate-item >> nth=0')
    await page.fill('#guard-email', acct.email)
    await page.fill('#guard-password', PASSWORD)
    await page.click('.sqr-cred-submit')
    await page.getByRole('button', { name: 'OK', exact: true }).click({ timeout: 20000 })
    await page.waitForURL((u) => !u.pathname.includes('guard-login'), { timeout: 30000 })
    return
  }
  await page.goto(`${BASE}/login`)
  await page.fill('#login-email', acct.email)
  await page.fill('#login-password', PASSWORD)
  await page.click('#login-submit')
  if (acct.secret) {
    await page.waitForSelector('.tfa-code-input', { timeout: 20000 })
    if (beforeCode) await beforeCode()
    await page.fill('.tfa-code-input', totp(acct.secret))
  }
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 30000 })
}

export async function settle(page, ms = 1500) {
  await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {})
  await page.waitForTimeout(ms)
}

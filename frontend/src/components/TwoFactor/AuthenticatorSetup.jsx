import { useState } from 'react'
import { Copy, Check, ExternalLink, QrCode } from 'lucide-react'

/**
 * The three ways to get a secret into an authenticator app.
 *
 * A QR code alone assumes two devices — one showing it, one scanning it. That
 * holds on a laptop and breaks completely on a phone, which is where most
 * people actually finish signing up: they cannot point the camera at the screen
 * displaying the camera. The enrollment endpoint has always returned the other
 * two forms of the same secret (`otpauth_uri` and `secret`); the screens simply
 * threw them away.
 *
 * So all three are offered:
 *
 *   1. The `otpauth://` link. Tapping it hands off to whichever authenticator is
 *      installed, account and secret already filled in — no scanning, no typing.
 *      Google Authenticator, Authy, Microsoft Authenticator and 1Password all
 *      register for the scheme.
 *   2. The QR, for the laptop-plus-phone case.
 *   3. The key itself, for when nothing claims the link and for the person whose
 *      authenticator lives on a different device entirely.
 *
 * Which one leads is decided by `pointer: coarse` in the stylesheet, not by
 * sniffing the user agent — a touch device puts the link first, everything else
 * puts the QR first. Both are always present, so guessing wrong costs a scroll
 * rather than a dead end.
 */
export default function AuthenticatorSetup({ enrollment }) {
  const [copied, setCopied] = useState(false)

  if (!enrollment) return null

  const { qr_code: qrCode, otpauth_uri: otpauthUri, secret } = enrollment

  const copySecret = () => {
    navigator.clipboard?.writeText(secret).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2200)
    })
  }

  // Base32 in groups of four. The key is only ever read off a screen and typed
  // into another app by hand, and an unbroken 32-character run is where that
  // goes wrong.
  const grouped = (secret || '').replace(/(.{4})/g, '$1 ').trim()

  return (
    <div className="tfa-enroll">

      {otpauthUri && (
        <div className="tfa-enroll-open">
          <a className="tfa-btn tfa-btn-primary tfa-open-app" href={otpauthUri}>
            <ExternalLink size={16} />Open in your authenticator app
          </a>
          <p className="tfa-hint tfa-enroll-note">
            Already on your phone? This opens the app and fills everything in.
          </p>
        </div>
      )}

      <div className="tfa-enroll-qr">
        <div className="tfa-enroll-label"><QrCode size={13} />Or scan this</div>
        <div className="tfa-qr-wrap">
          <img src={qrCode} alt="QR code for your authenticator app" />
        </div>
      </div>

      {secret && (
        <div className="tfa-enroll-key">
          <div className="tfa-enroll-label">Or type this key in by hand</div>
          <div className="tfa-secret-row">
            <code className="tfa-secret">{grouped}</code>
            <button
              type="button"
              className="tfa-btn tfa-btn-ghost tfa-secret-copy"
              onClick={copySecret}
            >
              {copied ? <Check size={15} /> : <Copy size={15} />}
              {copied ? 'Copied' : 'Copy'}
            </button>
          </div>
        </div>
      )}

    </div>
  )
}

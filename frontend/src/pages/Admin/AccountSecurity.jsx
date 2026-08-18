import SecurityPanel from '../../components/TwoFactor/SecurityPanel'
import { ShieldCheck } from 'lucide-react'
import './AccountSecurity.css'

/**
 * CDSO account security — the self-service half of two-factor.
 *
 * Deliberately separate from System Settings: everything on that screen is a
 * system-wide policy the CDSO sets for other people, while this is about the
 * signed-in account's own device and recovery codes.
 */
export default function AccountSecurity() {
  return (
    <div className="acs-page">
      <div className="acs-header">
        <div className="acs-header-icon"><ShieldCheck size={22} /></div>
        <div>
          <h1 className="acs-title">Account Security</h1>
          <p className="acs-subtitle">
            Manage the authenticator app and backup codes for your own account.
          </p>
        </div>
      </div>

      <div className="acs-card">
        <SecurityPanel />
      </div>
    </div>
  )
}

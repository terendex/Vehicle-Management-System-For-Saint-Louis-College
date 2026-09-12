import slcLogo from '../assets/slclogo.jpg'
import cdsoLogo from '../assets/cdsologo.jpg'

/**
 * The Saint Louis College seal and the CDSO emblem, side by side.
 *
 * The two are one lockup, not two decorations: the system is run by the CDSO
 * on behalf of the college, and every screen that identifies the system has to
 * show both. They used to be a bare `<img>` of the SLC seal copy-pasted into
 * nine pages and three layouts, which is why adding the CDSO emblem to one of
 * them would have left the other eleven showing half the mark.
 *
 *   size="header"  — the page-header lockup (login, register, policy, …)
 *   size="sidebar" — the compact sidebar brand (admin, security shells)
 *
 * Both logos are circular emblems drawn on white, so they are fitted (never
 * cropped) inside their ring — `cover` clipped the tips of the CDSO arrows.
 */
export default function BrandLogos({ size = 'header', className = '' }) {
  const cls = `brand-logos brand-logos--${size}${className ? ` ${className}` : ''}`
  return (
    <span className={cls}>
      <img src={slcLogo} alt="Saint Louis College logo" className="brand-logos-img" />
      <img
        src={cdsoLogo}
        alt="Campus Development and Sustainability Office logo"
        className="brand-logos-img brand-logos-img--cdso"
      />
    </span>
  )
}

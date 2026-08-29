/* Drawn scenes for the steps that get an applicant an account.

   Only the journey is illustrated — pay, file the receipt, wait for the
   approval mail that carries the portal credentials. The form itself is left
   alone: its fields are labelled and hinted already, and a picture beside every
   heading fought the density rather than helping it.

   The scenes are inline SVG on a shared 120x90 stage and a shared palette
   (RegArt.css) so a card on the OR upload page and a card on the confirmation
   screen look like the same set of drawings — which is the point: the applicant
   meets them on two screens several days apart.

   All of them are decorative. The instruction is always spelled out in the text
   beside the picture, so every svg is aria-hidden and nothing here is the only
   carrier of meaning. */

import './RegArt.css'

const PESO = '₱'

function Art({ children }) {
  return (
    <svg
      className="reg-art"
      viewBox="0 0 120 90"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
      focusable="false"
    >
      <rect width="120" height="90" rx="12" fill="var(--art-bg)" />
      {children}
    </svg>
  )
}

/* ===== Next steps after submitting ===== */

export function PayAtAccountingArt() {
  return (
    <Art>
      <rect x="20" y="10" width="80" height="43" rx="6" fill="var(--art-white)" stroke="var(--art-sky)" strokeWidth="2" />
      <rect x="20" y="10" width="80" height="10" rx="6" fill="var(--art-navy)" />
      <rect x="20" y="16" width="80" height="4" fill="var(--art-navy)" />
      <circle cx="45" cy="33" r="7.5" fill="var(--art-navy-2)" />
      <path d="M33 50c0-7 5.5-12 12-12s12 5 12 12z" fill="var(--art-navy-2)" />
      <rect x="66" y="40" width="30" height="13" rx="2" fill="var(--art-sky-2)" />
      <rect x="6" y="53" width="108" height="10" rx="3" fill="var(--art-navy)" />
      <rect x="14" y="63" width="92" height="18" rx="3" fill="var(--art-sky)" />
      <rect x="60" y="45" width="38" height="21" rx="3" fill="var(--art-gold)" stroke="var(--art-gold-ink)" strokeWidth="1.6" />
      <text
        x="79"
        y="61"
        textAnchor="middle"
        fontFamily="Inter, system-ui, sans-serif"
        fontSize="15"
        fontWeight="800"
        fill="var(--art-gold-ink)"
      >
        {PESO}
      </text>
    </Art>
  )
}

export function NoFeeArt() {
  return (
    <Art>
      <circle cx="52" cy="45" r="27" fill="var(--art-white)" stroke="var(--art-green-2)" strokeWidth="3" />
      <text
        x="52"
        y="54"
        textAnchor="middle"
        fontFamily="Inter, system-ui, sans-serif"
        fontSize="26"
        fontWeight="800"
        fill="var(--art-green)"
      >
        {`${PESO}0`}
      </text>
      <circle cx="88" cy="63" r="15" fill="var(--art-green)" />
      <path d="M81 63l5 5 10-11" fill="none" stroke="#fff" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round" />
    </Art>
  )
}

export function UploadOrArt() {
  return (
    <Art>
      <rect x="14" y="12" width="92" height="60" rx="8" fill="var(--art-white)" stroke="var(--art-sky)" strokeWidth="2" />
      <rect x="14" y="12" width="92" height="13" rx="8" fill="var(--art-sky-2)" />
      <rect x="14" y="20" width="92" height="5" fill="var(--art-sky-2)" />
      <circle cx="23" cy="18.5" r="2.2" fill="var(--art-sky)" />
      <circle cx="31" cy="18.5" r="2.2" fill="var(--art-sky)" />
      <circle cx="39" cy="18.5" r="2.2" fill="var(--art-sky)" />
      <rect x="34" y="32" width="34" height="36" rx="3" fill="var(--art-sky-2)" stroke="var(--art-sky)" strokeWidth="1.5" />
      <rect x="40" y="39" width="22" height="4" rx="2" fill="var(--art-navy)" />
      <rect x="40" y="47" width="16" height="3.5" rx="1.75" fill="var(--art-sky)" />
      <rect x="40" y="55" width="19" height="3.5" rx="1.75" fill="var(--art-sky)" />
      <circle cx="82" cy="55" r="16" fill="var(--art-navy)" />
      <path d="M82 63V47M75 54l7-7 7 7" fill="none" stroke="#fff" strokeWidth="3.4" strokeLinecap="round" strokeLinejoin="round" />
    </Art>
  )
}

export function ApprovalMailArt() {
  return (
    <Art>
      <rect x="16" y="22" width="80" height="50" rx="7" fill="var(--art-white)" stroke="var(--art-sky)" strokeWidth="2.5" />
      <rect x="30" y="14" width="52" height="20" rx="3" fill="var(--art-sky-2)" />
      <rect x="38" y="21" width="26" height="4" rx="2" fill="var(--art-navy)" />
      <rect x="38" y="29" width="18" height="3" rx="1.5" fill="var(--art-sky)" />
      <path d="M18 28l38 26 38-26" fill="none" stroke="var(--art-navy-2)" strokeWidth="2.5" strokeLinejoin="round" />
      <circle cx="94" cy="62" r="16" fill="var(--art-green)" />
      <path d="M87 62l5 5 10-11" fill="none" stroke="#fff" strokeWidth="3.6" strokeLinecap="round" strokeLinejoin="round" />
    </Art>
  )
}

export function CdsoOfficeArt() {
  return (
    <Art>
      <path d="M60 8l44 22H16z" fill="var(--art-navy)" />
      <rect x="22" y="30" width="76" height="42" rx="3" fill="var(--art-white)" stroke="var(--art-sky)" strokeWidth="2" />
      <rect x="31" y="40" width="9" height="32" rx="2" fill="var(--art-sky-2)" />
      <rect x="47" y="40" width="9" height="32" rx="2" fill="var(--art-sky-2)" />
      <rect x="63" y="40" width="9" height="32" rx="2" fill="var(--art-sky-2)" />
      <rect x="79" y="40" width="9" height="32" rx="2" fill="var(--art-sky-2)" />
      <rect x="14" y="72" width="92" height="8" rx="3" fill="var(--art-navy)" />
      <rect x="46" y="20" width="28" height="7" rx="3" fill="var(--art-gold)" />
    </Art>
  )
}

/* ===== OR upload page ===== */

export function OrNumberArt() {
  return (
    <Art>
      <rect x="32" y="8" width="56" height="70" rx="4" fill="var(--art-white)" stroke="var(--art-sky)" strokeWidth="2" />
      <rect x="40" y="17" width="40" height="5" rx="2.5" fill="var(--art-navy)" />
      <rect x="40" y="28" width="24" height="4" rx="2" fill="var(--art-sky)" />
      <rect x="40" y="36" width="30" height="4" rx="2" fill="var(--art-sky)" />
      <rect x="36" y="46" width="48" height="16" rx="3" fill="var(--art-gold)" stroke="var(--art-gold-ink)" strokeWidth="1.6" />
      <rect x="42" y="52" width="36" height="5" rx="2.5" fill="var(--art-gold-ink)" />
      <rect x="40" y="68" width="20" height="4" rx="2" fill="var(--art-sky)" />
    </Art>
  )
}

export function ReceiptPhotoArt() {
  return (
    <Art>
      <rect x="32" y="6" width="56" height="78" rx="9" fill="var(--art-navy)" />
      <rect x="37" y="14" width="46" height="60" rx="4" fill="var(--art-white)" />
      <circle cx="60" cy="10" r="1.8" fill="var(--art-sky)" />
      <rect x="52" y="77" width="16" height="3" rx="1.5" fill="var(--art-sky)" />
      <rect x="44" y="21" width="32" height="46" rx="3" fill="var(--art-sky-2)" />
      <rect x="49" y="28" width="22" height="4" rx="2" fill="var(--art-navy)" />
      <rect x="49" y="37" width="16" height="3.5" rx="1.75" fill="var(--art-sky)" />
      <rect x="49" y="45" width="20" height="3.5" rx="1.75" fill="var(--art-sky)" />
      <rect x="49" y="55" width="22" height="6" rx="2" fill="var(--art-gold)" />
      {/* Framing corners — the instruction is "a clear photo of the whole receipt" */}
      <path d="M20 26v-8h8M100 26v-8h-8M20 64v8h8M100 64v8h-8" fill="none" stroke="var(--art-gold)" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
    </Art>
  )
}

export function CdsoReviewArt() {
  return (
    <Art>
      <rect x="24" y="12" width="58" height="68" rx="6" fill="var(--art-white)" stroke="var(--art-sky)" strokeWidth="2" />
      <rect x="43" y="7" width="20" height="10" rx="4" fill="var(--art-navy)" />
      <rect x="33" y="28" width="30" height="4.5" rx="2.25" fill="var(--art-sky)" />
      <rect x="33" y="40" width="38" height="4.5" rx="2.25" fill="var(--art-sky)" />
      <rect x="33" y="52" width="24" height="4.5" rx="2.25" fill="var(--art-sky)" />
      <circle cx="88" cy="56" r="16" fill="var(--art-white)" stroke="var(--art-navy)" strokeWidth="4.5" />
      <path d="M99 68l9 9" stroke="var(--art-navy)" strokeWidth="5.5" strokeLinecap="round" />
      <path d="M81 56l5 5 10-11" fill="none" stroke="var(--art-gold)" strokeWidth="3.4" strokeLinecap="round" strokeLinejoin="round" />
    </Art>
  )
}

/* ===== Layout wrapper ===== */

/* A numbered instruction with its scene. `tone="ok"` is for a step that is good
   news rather than a task — an exempt applicant's waived fee. */
export function IllustratedStep({ art, step, title, children, tone }) {
  return (
    <div className={`reg-step-card${tone ? ` reg-step-card--${tone}` : ''}`}>
      <div className="reg-step-art">
        {art}
        {step != null && <span className="reg-step-num">{step}</span>}
      </div>
      <div className="reg-step-body">
        <p className="reg-step-title">{title}</p>
        <p className="reg-step-text">{children}</p>
      </div>
    </div>
  )
}

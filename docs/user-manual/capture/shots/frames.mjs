// Renders a stylised gate-camera frame (illustration, fictional plate) to JPEG.
import { chromium } from 'playwright'

const svg = `
<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
  <defs>
    <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#9fb8cc"/><stop offset="1" stop-color="#d9e2e8"/>
    </linearGradient>
    <linearGradient id="road" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#6b6f73"/><stop offset="1" stop-color="#44484c"/>
    </linearGradient>
    <linearGradient id="body" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#f4f5f6"/><stop offset="1" stop-color="#c9ced3"/>
    </linearGradient>
    <linearGradient id="glass" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#3b4b5c"/><stop offset="1" stop-color="#1c2631"/>
    </linearGradient>
  </defs>
  <rect width="1280" height="300" fill="url(#sky)"/>
  <!-- trees / buildings -->
  <rect x="0" y="150" width="1280" height="150" fill="#7d8f7a"/>
  <rect x="60" y="90" width="330" height="200" fill="#e8e1d2"/>
  <rect x="60" y="80" width="330" height="18" fill="#8a3b2e"/>
  ${Array.from({ length: 6 }, (_, i) => `<rect x="${85 + i * 52}" y="120" width="30" height="40" fill="#5d6f80"/><rect x="${85 + i * 52}" y="190" width="30" height="40" fill="#5d6f80"/>`).join('')}
  <rect x="900" y="110" width="320" height="180" fill="#ece6d8"/>
  ${Array.from({ length: 5 }, (_, i) => `<rect x="${925 + i * 58}" y="140" width="34" height="46" fill="#5d6f80"/>`).join('')}
  <!-- road -->
  <polygon points="380,300 900,300 1280,720 0,720" fill="url(#road)"/>
  <polygon points="0,300 380,300 0,560" fill="#8c9189"/>
  <polygon points="900,300 1280,300 1280,560" fill="#8c9189"/>
  ${Array.from({ length: 6 }, (_, i) => { const y = 320 + i * 70; const w = 6 + i * 3; return `<rect x="${640 - w / 2}" y="${y}" width="${w}" height="${30 + i * 6}" fill="#e9e3c8" opacity="0.8"/>` }).join('')}
  <!-- guard booth + boom barrier -->
  <rect x="960" y="210" width="120" height="170" fill="#123a63"/>
  <rect x="975" y="235" width="90" height="60" fill="#9fc3e0"/>
  <rect x="950" y="200" width="140" height="16" fill="#f2c200"/>
  <rect x="940" y="330" width="22" height="80" fill="#333"/>
  <rect x="330" y="338" width="620" height="16" fill="#d7261e"/>
  ${Array.from({ length: 8 }, (_, i) => `<rect x="${345 + i * 78}" y="338" width="38" height="16" fill="#fafafa"/>`).join('')}
  <!-- car (front view) -->
  <ellipse cx="640" cy="640" rx="300" ry="40" fill="#000" opacity="0.35"/>
  <path d="M390,520 Q400,430 470,400 L540,330 Q560,315 590,315 L690,315 Q720,315 740,330 L810,400 Q880,430 890,520 L895,600 Q895,630 865,632 L415,632 Q385,630 385,600 Z" fill="url(#body)" stroke="#8d949b" stroke-width="3"/>
  <path d="M500,405 L560,342 Q572,332 592,332 L688,332 Q708,332 720,342 L780,405 Z" fill="url(#glass)"/>
  <rect x="440" y="440" width="120" height="44" rx="18" fill="#fdfbe8" stroke="#9aa0a6" stroke-width="3"/>
  <rect x="720" y="440" width="120" height="44" rx="18" fill="#fdfbe8" stroke="#9aa0a6" stroke-width="3"/>
  <rect x="560" y="450" width="160" height="40" rx="8" fill="#2a2f35"/>
  ${Array.from({ length: 7 }, (_, i) => `<rect x="572" y="${456 + i * 5}" width="136" height="2" fill="#555"/>`).join('')}
  <rect x="405" y="590" width="80" height="44" rx="10" fill="#1d1f22"/>
  <rect x="795" y="590" width="80" height="44" rx="10" fill="#1d1f22"/>
  <!-- plate -->
  <rect x="548" y="535" width="184" height="52" rx="4" fill="#ffffff" stroke="#1b1b1b" stroke-width="3"/>
  <rect x="548" y="535" width="184" height="10" fill="#1d6fb8"/>
  <text x="640" y="580" font-family="Arial Black, Arial, sans-serif" font-size="34" font-weight="900" text-anchor="middle" fill="#111">NBC 1234</text>
  <text x="40" y="690" font-family="Consolas, monospace" font-size="22" fill="#ffffff" opacity="0.85">GATE 1 ENTRY CAM</text>
</svg>`

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1280, height: 720 } })
await page.setContent(`<body style="margin:0">${svg}</body>`)
await page.screenshot({ path: 'assets/gate_frame.jpg', type: 'jpeg', quality: 90 })
await browser.close()
console.log('frames ok')

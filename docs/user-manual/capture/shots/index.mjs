// Writes README.md next to the figures: every image with its caption, the text
// of its numbered callouts, and any prose notes from notes.mjs, grouped by
// manual section.
//
// README.md is GENERATED. It is the source the Word document is built from
// (docs/user-manual/build_manual.py reads it), but it is not the source of its
// own text: the callout wording lives in shots.mjs, beside the element each
// numbered box points at, and the notes live in notes.mjs. Editing README.md
// by hand is lost on the next run.
import fs from 'node:fs'
import path from 'node:path'
import { NOTES } from './notes.mjs'

const OUT_ROOT = process.argv[2]
const metas = fs.readdirSync('raw').filter((f) => f.endsWith('.json'))
  .map((f) => JSON.parse(fs.readFileSync(path.join('raw', f), 'utf8')))
  // "-mobile" captures are the phone layout of a figure, built for the in-app
  // help page to swap in on a narrow screen. They are not separate sections of
  // the printed manual, and composing them into the images folder by accident
  // would otherwise add a duplicate chapter entry for each one.
  .filter((m) => !m.id.endsWith('-mobile'))
  .filter((m) => fs.existsSync(path.join(OUT_ROOT, m.out)))
  .sort((a, b) => a.out.localeCompare(b.out))

const SECTION_NOTES = {
  '01-getting-started': 'Signing in, password reset, applying for a vehicle pass, and guard sign-in at a gate.',
  '02-cdso-admin': 'Every screen available to the CDSO (administrator) account.',
  '03-security-guard': 'The gate terminal used by security guards.',
  '04-vehicle-owner': 'The portal registered vehicle owners see.',
  '05-installer': 'Installing the campus system with SLC-Smart-Parking-Campus-Setup.exe.',
  '06-campus-launcher': 'The launcher window that runs the campus server.',
}

// Wrap a note so the markdown stays readable in a diff; the Word build joins
// the lines back into one paragraph.
const quote = (text, width = 96) => {
  const out = []
  let line = ''
  for (const word of text.split(' ')) {
    if (line && (line + ' ' + word).length > width) { out.push(line); line = word } else {
      line = line ? line + ' ' + word : word
    }
  }
  if (line) out.push(line)
  return out.map((l) => `> ${l}`).join('\n')
}

let md = `# User manual images

**This file is generated.** \`shots/index.mjs\` writes it from the figure definitions in
\`shots/shots.mjs\` and the prose in \`shots/notes.mjs\`; the Word document is then built from
it. Edit those two files and re-run the capture, not this one — a hand edit here is lost on
the next run.

Annotated screenshots for the user manual. Each image has numbered callouts, and the same
numbers are explained in the list below it.

The web screenshots were taken against a separate demo database filled with
fictional people, plates and records. Camera pictures in them are sample
illustrations, not real camera footage. Installer screens come from the real
setup wizard, and the launcher screen from the installed launcher, with its
activity log pixelated.

`
let current = ''
for (const m of metas) {
  const dir = m.out.split('/')[0]
  if (dir !== current) {
    current = dir
    md += `\n## ${m.section}\n\n${SECTION_NOTES[dir] || ''}\n\n`
  }
  md += `### ${m.title}\n\n![${m.title}](${m.out})\n\n`
  if (m.caption) md += `${m.caption}\n\n`
  for (const mk of m.marks) md += `${mk.n}. **${mk.label}**: ${mk.desc}\n`
  for (const note of NOTES[m.id] || []) md += `\n${quote(note)}\n`
  md += '\n'
}
fs.writeFileSync(path.join(OUT_ROOT, 'README.md'), md)

const orphans = Object.keys(NOTES).filter((id) => !metas.some((m) => m.id === id))
if (orphans.length) console.log('! notes with no figure:', orphans.join(', '))
console.log('index', metas.length, 'figures,', Object.keys(NOTES).length, 'noted sections')

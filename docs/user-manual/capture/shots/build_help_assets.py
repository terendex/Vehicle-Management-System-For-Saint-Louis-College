"""Copies the clean help screenshots into the frontend as WebP and writes
src/pages/Help/helpFigures.js, which maps a figure key to its image and callouts."""
import json
import re
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
CLEAN = HERE.parent / 'figures-clean'
# docs/user-manual/capture/shots -> repository root. Derived rather than
# hard-coded so the pipeline works from any checkout.
REPO = HERE.parents[3]
ASSETS = REPO / 'frontend' / 'src' / 'assets' / 'help'
MODULE = REPO / 'frontend' / 'src' / 'pages' / 'Help' / 'helpFigures.js'

PREFIX = {
    '01-getting-started': 'start',
    '02-cdso-admin': 'cdso',
    '03-security-guard': 'guard',
    '04-vehicle-owner': 'owner',
    '05-installer': 'installer',
    '06-campus-launcher': 'launcher',
}
MAX_W = 1600
# A picture of the Help page inside the Help page explains nothing.
SKIP = {'cdso-help'}

ASSETS.mkdir(parents=True, exist_ok=True)
entries = []
total = 0
for meta_path in sorted(CLEAN.rglob('*.json')):
    meta = json.loads(meta_path.read_text(encoding='utf-8'))
    section = meta_path.parent.name
    name = re.sub(r'^\d+b?-', '', meta_path.stem)
    key = f'{PREFIX[section]}-{name}'
    if key in SKIP:
        continue
    with Image.open(meta_path.with_suffix('.png')) as im:
        im = im.convert('RGB')
        if im.width > MAX_W:
            im = im.resize((MAX_W, round(im.height * MAX_W / im.width)), Image.LANCZOS)
        out = ASSETS / f'{key}.webp'
        im.save(out, 'WEBP', quality=84, method=6)
        w, h = im.size
    total += out.stat().st_size
    entries.append((key, meta, w, h))


def js(v):
    return json.dumps(v, ensure_ascii=False)


lines = [
    '// Generated from the user-manual screenshots. Each figure is a clean',
    '// screenshot plus numbered callouts; the boxes and badges are drawn by',
    '// HelpFigure at render time, as percentages of the image, so they stay',
    '// sharp at any size and can be highlighted from the legend.',
    '//',
    '// The people, plates and records in these pictures are sample data, and',
    '// camera panels show an illustration rather than real footage.',
    '',
]
for i, (key, _, _, _) in enumerate(entries):
    lines.append(f"import img{i} from '../../assets/help/{key}.webp'")
def callouts_of(meta):
    return [
        {'n': c['n'], 'label': c['label'], 'desc': c['desc'], 'box': c['box'], 'badge': c['badge']}
        for c in meta['callouts']
    ]


# A "-mobile" screenshot is the phone layout of the figure with the same key.
index = {key: i for i, (key, *_) in enumerate(entries)}
lines += [
    '',
    '// `mobile`, where present, is the same screen photographed on a phone.',
    '// HelpFigure shows it to readers on a phone-sized screen.',
    'export const HELP_FIGURES = {',
]
for i, (key, meta, w, h) in enumerate(entries):
    if key.endswith('-mobile'):
        continue
    lines.append(f'  {js(key)}: {{')
    lines.append(f'    src: img{i}, width: {w}, height: {h},')
    lines.append(f'    title: {js(meta["title"])},')
    lines.append(f'    caption: {js(meta.get("caption") or "")},')
    lines.append(f'    callouts: {js(callouts_of(meta))},')
    m = index.get(f'{key}-mobile')
    if m is not None:
        _, mmeta, mw, mh = entries[m]
        lines.append(f'    mobile: {{ src: img{m}, width: {mw}, height: {mh}, callouts: {js(callouts_of(mmeta))} }},')
    lines.append('  },')
lines.append('}')
MODULE.write_text('\n'.join(lines) + '\n', encoding='utf-8')
phones = sum(1 for k, *_ in entries if k.endswith('-mobile'))
print(len(entries) - phones, 'figures +', phones, 'phone versions,', f'{total / 1e6:.1f} MB of WebP')

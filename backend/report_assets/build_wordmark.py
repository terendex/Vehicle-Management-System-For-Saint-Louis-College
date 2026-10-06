"""Rebuild slc_wordmark.json: "Saint Louis College" in Old English Text MT,
as outlines.

The letterhead sets the college name in Old English Text MT, the face of the
official letterhead. Windows has the font (OLDENGL.TTF) and report_utils uses
it directly there. Railway runs Linux and has no Windows fonts, and the font
is Monotype's and may not be copied into this repository. So the one phrase
is shipped as artwork instead, the way a logo is: its glyph outlines, which
report_utils draws as a vector path when the font is not installed.

Run on a Windows machine (needs fontTools, already a dependency) only if the
name ever changes:

    python report_assets/build_wordmark.py
"""
import json
import os

from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont

TEXT = 'Saint Louis College'      # report_utils.LH_INSTITUTION
FONT = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts', 'OLDENGL.TTF')
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'slc_wordmark.json')


class _OpsPen(BasePen):
    """Records a glyph as M/L/C/Z ops, quadratic segments raised to cubic
    (reportlab paths are cubic), offset to the glyph's place in the line."""

    def __init__(self, glyph_set, dx):
        super().__init__(glyph_set)
        self.dx, self.ops = dx, []

    def _pt(self, p):
        return [round(p[0] + self.dx, 1), round(p[1], 1)]

    def _moveTo(self, p):
        self.ops.append(['M', *self._pt(p)])

    def _lineTo(self, p):
        self.ops.append(['L', *self._pt(p)])

    def _curveToOne(self, c1, c2, p):
        self.ops.append(['C', *self._pt(c1), *self._pt(c2), *self._pt(p)])

    def _qCurveToOne(self, q, p):
        p0 = self._getCurrentPoint()
        c1 = (p0[0] + 2 / 3 * (q[0] - p0[0]), p0[1] + 2 / 3 * (q[1] - p0[1]))
        c2 = (p[0] + 2 / 3 * (q[0] - p[0]), p[1] + 2 / 3 * (q[1] - p[1]))
        self._curveToOne(c1, c2, p)

    def _closePath(self):
        self.ops.append(['Z'])

    _endPath = _closePath


def build():
    font = TTFont(FONT)
    cmap, glyphs, hmtx = font.getBestCmap(), font.getGlyphSet(), font['hmtx']
    # Advance widths only, no kerning: the same layout reportlab gives the
    # real font, so a PDF made on Windows and one made on Railway match.
    x, ops = 0, []
    for ch in TEXT:
        name = cmap[ord(ch)]
        pen = _OpsPen(glyphs, x)
        glyphs[name].draw(pen)
        ops += pen.ops
        x += hmtx[name][0]
    data = {
        'text': TEXT,
        'source': 'Old English Text MT (OLDENGL.TTF), glyph outlines',
        'units_per_em': font['head'].unitsPerEm,
        'advance': x,
        'ops': ops,
    }
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(data, f, separators=(',', ':'))
    print(f'{OUT}: {len(ops)} ops, advance {x} / {data["units_per_em"]} em')


if __name__ == '__main__':
    build()

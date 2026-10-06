"""The letterhead's "Saint Louis College" is Old English Text MT everywhere.

Windows has the font; Railway (Linux) does not, and draws the same name from
its outlines (report_assets/slc_wordmark.json). Neither may fall back to a
substitute face.
"""
import io
import os
from unittest import mock, skipUnless

from django.test import SimpleTestCase
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas as rl_canvas

import report_utils

_STATE = ('_fonts_ready', '_WORDMARK', '_PDF_INSTITUTION_FONT', '_PDF_LOCATION_FONT',
          '_PDF_TAGLINE_FONT', '_PDF_ACCRED_FONT')


class LetterheadWordmarkTests(SimpleTestCase):

    def setUp(self):
        saved = {k: getattr(report_utils, k) for k in _STATE}
        self.addCleanup(lambda: [setattr(report_utils, k, v) for k, v in saved.items()])
        report_utils._fonts_ready, report_utils._WORDMARK = False, None

    def pdf(self):
        buf = io.BytesIO()
        w, h = landscape(A4)
        c = rl_canvas.Canvas(buf, pagesize=(w, h))
        report_utils.draw_letterhead(c, w, h, title='TEST')
        c.save()
        return buf.getvalue()

    def test_without_the_font_the_name_is_drawn_from_its_outlines(self):
        real = report_utils._font_path
        hidden = lambda f: os.path.join('/nonexistent', f) if f == 'OLDENGL.TTF' else real(f)   # noqa: E731
        with mock.patch.object(report_utils, '_font_path', hidden):
            data = self.pdf()
        self.assertEqual(report_utils._WORDMARK['text'], report_utils.LH_INSTITUTION)
        self.assertNotIn(b'Times-Bold', data)      # no substitute face for the name

    @skipUnless(os.path.exists(report_utils._font_path('OLDENGL.TTF')), 'needs Windows fonts')
    def test_the_outlines_are_as_wide_as_the_real_font(self):
        from reportlab.pdfbase.pdfmetrics import stringWidth
        self.pdf()
        self.assertIsNone(report_utils._WORDMARK)  # the real font is used where it exists
        import json
        with open(report_utils._WORDMARK_PATH, encoding='utf-8') as f:
            mark = json.load(f)
        self.assertAlmostEqual(mark['advance'] * 22 / mark['units_per_em'],
                               stringWidth(report_utils.LH_INSTITUTION, 'SLC-OldEnglish', 22), places=1)

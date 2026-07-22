"""Shared branded report builders (Excel + PDF) for the admin/CDSO reports.

Every report carries a text reconstruction of the Saint Louis College
letterhead — seal, "Saint Louis College", "of San Fernando, La Union", the
"Beacon of Wisdom" tagline, and the accreditation line — followed by the
brand-coloured table and a footer with generation stamp + page numbers.
"""
import os
from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone as tz

REPORT_BRAND_HEX = '2A2B61'
REPORT_NAVY_HEX  = '1B2A63'
REPORT_LOGO_PATH = os.path.join(settings.BASE_DIR, 'report_assets', 'slclogo.jpg')

# Letterhead text (reconstruction of the official SLC letterhead).
LH_INSTITUTION = 'Saint Louis College'
LH_LOCATION    = 'of San Fernando, La Union'
LH_TAGLINE     = 'The Beacon of Wisdom in the North'
LH_ACCRED      = ('•  Center of Excellence in Teacher Education        '
                  '•  ISO 9001: 2015 Quality Management System Certified        '
                  '•  CHED Deregulated Status')


def report_filename(report_name, ext):
    """Filesystem-safe, human-readable report filename with date + time.

    e.g. report_filename('Vehicle Log Report', 'pdf')
         -> 'Vehicle Log Report - 2026-07-22 09-30 PM.pdf'
    """
    stamp = tz.localtime().strftime('%Y-%m-%d %I-%M %p')
    return f"{report_name} - {stamp}.{ext}"


def branded_excel_response(*, filename, sheet_title, report_title, subtitle, headers, rows, col_widths):
    """Build a styled .xlsx with the SLC letterhead band, brand header row, footer."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill
    from openpyxl.drawing.image import Image as XLImage

    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title
    n = len(headers)
    last_col = chr(ord('A') + n - 1)
    TNR = 'Times New Roman'

    # ── Letterhead band ──────────────────────────────────────────────
    if os.path.exists(REPORT_LOGO_PATH):
        try:
            logo = XLImage(REPORT_LOGO_PATH)
            logo.width, logo.height = 132, 50
            ws.add_image(logo, 'A1')
        except Exception:
            pass
    for rh in (1, 2, 3, 4):
        ws.row_dimensions[rh].height = 15

    center = Alignment(horizontal='center')
    ws.merge_cells(f'A1:{last_col}1')
    ws['A1'] = LH_INSTITUTION
    ws['A1'].font = Font(name=TNR, bold=True, size=18, color=REPORT_NAVY_HEX)
    ws['A1'].alignment = center
    ws.merge_cells(f'A2:{last_col}2')
    ws['A2'] = LH_LOCATION
    ws['A2'].font = Font(name=TNR, size=11, color='333333')
    ws['A2'].alignment = center
    ws.merge_cells(f'A3:{last_col}3')
    ws['A3'] = LH_TAGLINE
    ws['A3'].font = Font(name=TNR, italic=True, size=10, color='555555')
    ws['A3'].alignment = center

    ws.merge_cells(f'A5:{last_col}5')
    ws['A5'] = LH_ACCRED
    ws['A5'].font = Font(name=TNR, size=8, color='555555')
    ws['A5'].alignment = center

    # ── Report title + subtitle (centred) ────────────────────────────
    ws.merge_cells(f'A6:{last_col}6')
    ws['A6'] = report_title
    ws['A6'].font = Font(bold=True, size=13, color=REPORT_BRAND_HEX)
    ws['A6'].alignment = center
    ws.merge_cells(f'A7:{last_col}7')
    ws['A7'] = subtitle
    ws['A7'].font = Font(size=10, color='666666')
    ws['A7'].alignment = center

    # ── Column headers (row 9) ───────────────────────────────────────
    header_row = 9
    header_fill = PatternFill('solid', fgColor=REPORT_BRAND_HEX)
    for col, title in enumerate(headers, start=1):
        cell = ws.cell(row=header_row, column=col, value=title)
        cell.font = Font(bold=True, color='FFFFFF', size=11)
        cell.fill = header_fill
        cell.alignment = Alignment(vertical='center')

    for i, width in enumerate(col_widths):
        ws.column_dimensions[chr(ord('A') + i)].width = width
    ws.freeze_panes = f'A{header_row + 1}'

    wrap = Alignment(wrap_text=True, vertical='top')
    r = header_row
    for row in rows:
        r += 1
        for col, val in enumerate(row, start=1):
            cell = ws.cell(row=r, column=col, value=val)
            if col == n:
                cell.alignment = wrap

    foot = r + 2
    ws.merge_cells(start_row=foot, start_column=1, end_row=foot, end_column=n)
    fcell = ws.cell(row=foot, column=1, value='— End of report · Saint Louis College CDSO · Confidential —')
    fcell.font = Font(size=9, italic=True, color='999999')
    fcell.alignment = Alignment(horizontal='center')

    resp = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    wb.save(resp)
    return resp


def branded_pdf_response(*, filename, report_title, subtitle, generated_by, headers, rows, col_widths_mm):
    """Build a landscape A4 PDF with the SLC letterhead, brand table and footer."""
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

    brand = colors.HexColor(f'#{REPORT_BRAND_HEX}')
    navy  = colors.HexColor(f'#{REPORT_NAVY_HEX}')
    generated_at = tz.localtime().strftime('%B %d, %Y %I:%M %p')

    def esc(s):
        return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    def draw_frame(canvas, doc):
        canvas.saveState()
        w, h = landscape(A4)
        left = 15 * mm
        # Seal
        if os.path.exists(REPORT_LOGO_PATH):
            try:
                canvas.drawImage(REPORT_LOGO_PATH, left, h - 27 * mm,
                                 width=20 * mm, height=20 * mm,
                                 preserveAspectRatio=True, mask='auto')
            except Exception:
                pass
        cx = w / 2
        canvas.setFillColor(navy)
        canvas.setFont('Times-Bold', 20)
        canvas.drawCentredString(cx, h - 15 * mm, LH_INSTITUTION)
        canvas.setFillColor(colors.HexColor('#333333'))
        canvas.setFont('Times-Roman', 10.5)
        canvas.drawCentredString(cx, h - 19.5 * mm, LH_LOCATION)
        canvas.setFillColor(colors.HexColor('#555555'))
        canvas.setFont('Times-Italic', 10)
        canvas.drawCentredString(cx, h - 23.5 * mm, LH_TAGLINE)
        # Accreditation line (centred)
        canvas.setFont('Times-Roman', 7.5)
        canvas.setFillColor(colors.HexColor('#555555'))
        canvas.drawCentredString(cx, h - 30 * mm, LH_ACCRED)
        # Rule
        canvas.setStrokeColor(navy)
        canvas.setLineWidth(1.2)
        canvas.line(left, h - 33 * mm, w - 15 * mm, h - 33 * mm)
        # Report title (centred)
        canvas.setFillColor(brand)
        canvas.setFont('Helvetica-Bold', 11)
        canvas.drawCentredString(cx, h - 39 * mm, report_title)
        # Footer
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(colors.HexColor('#999999'))
        canvas.drawString(left, 10 * mm,
                          f'Generated {generated_at} by {generated_by} · Saint Louis College CDSO · Confidential')
        canvas.drawRightString(w - 15 * mm, 10 * mm, f'Page {doc.page}')
        canvas.restoreState()

    resp = HttpResponse(content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'

    doc = SimpleDocTemplate(
        resp, pagesize=landscape(A4),
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=44 * mm, bottomMargin=16 * mm, title=report_title,
    )
    cell_style = ParagraphStyle('cell', fontName='Helvetica', fontSize=8, leading=10)
    head_style = ParagraphStyle('head', fontName='Helvetica-Bold', fontSize=9,
                                textColor=colors.white, leading=11)
    sub_style = ParagraphStyle('sub', fontName='Helvetica', fontSize=9,
                               textColor=colors.HexColor('#666666'))

    story = [Paragraph(esc(subtitle), sub_style), Spacer(1, 4 * mm)]
    data = [[Paragraph(esc(h), head_style) for h in headers]]
    for row in rows:
        data.append([Paragraph(esc(c), cell_style) for c in row])
    if len(data) == 1:
        data.append([Paragraph('No records match the selected filters.', cell_style)]
                    + [Paragraph('', cell_style) for _ in range(len(headers) - 1)])

    table = Table(data, colWidths=[w * mm for w in col_widths_mm], repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), brand),
        ('TOPPADDING', (0, 0), (-1, 0), 6),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
        ('TOPPADDING', (0, 1), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F4F5FA')]),
        ('LINEBELOW', (0, 0), (-1, -1), 0.4, colors.HexColor('#E5E8F0')),
    ]))
    story.append(table)
    doc.build(story, onFirstPage=draw_frame, onLaterPages=draw_frame)
    return resp

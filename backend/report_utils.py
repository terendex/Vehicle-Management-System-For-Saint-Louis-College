"""Shared branded report builders (Excel + PDF) for the admin/CDSO reports.

Keeps the Saint Louis College branding — logo header, brand-coloured table
header, footer with generation stamp + page numbers — consistent across every
report (Vehicle Log, Violations, Registrations).
"""
import os
from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone as tz

REPORT_BRAND_HEX = '2A2B61'
REPORT_LOGO_PATH = os.path.join(settings.BASE_DIR, 'report_assets', 'slclogo.jpg')


def branded_excel_response(*, filename, sheet_title, report_title, subtitle, headers, rows, col_widths):
    """Build a styled .xlsx: logo band, title/subtitle, brand header row, footer."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill
    from openpyxl.drawing.image import Image as XLImage

    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title
    n = len(headers)
    last_col = chr(ord('A') + n - 1)

    if os.path.exists(REPORT_LOGO_PATH):
        try:
            logo = XLImage(REPORT_LOGO_PATH)
            logo.width, logo.height = 150, 55
            ws.add_image(logo, 'A1')
        except Exception:
            pass
    for rh in (1, 2, 3):
        ws.row_dimensions[rh].height = 18

    ws.merge_cells(f'C1:{last_col}1')
    ws['C1'] = report_title
    ws['C1'].font = Font(bold=True, size=13, color=REPORT_BRAND_HEX)
    ws.merge_cells(f'C2:{last_col}2')
    ws['C2'] = subtitle
    ws['C2'].font = Font(size=10, color='666666')

    header_fill = PatternFill('solid', fgColor=REPORT_BRAND_HEX)
    for col, title in enumerate(headers, start=1):
        cell = ws.cell(row=4, column=col, value=title)
        cell.font = Font(bold=True, color='FFFFFF', size=11)
        cell.fill = header_fill
        cell.alignment = Alignment(vertical='center')

    for i, width in enumerate(col_widths):
        ws.column_dimensions[chr(ord('A') + i)].width = width
    ws.freeze_panes = 'A5'

    wrap = Alignment(wrap_text=True, vertical='top')
    r = 4
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
    """Build a landscape A4 PDF with logo header, brand table and footer."""
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

    brand = colors.HexColor(f'#{REPORT_BRAND_HEX}')
    generated_at = tz.localtime().strftime('%B %d, %Y %I:%M %p')

    def esc(s):
        return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    def draw_frame(canvas, doc):
        canvas.saveState()
        w, h = landscape(A4)
        if os.path.exists(REPORT_LOGO_PATH):
            try:
                canvas.drawImage(REPORT_LOGO_PATH, 15 * mm, h - 26 * mm,
                                 width=30 * mm, height=16 * mm,
                                 preserveAspectRatio=True, mask='auto')
            except Exception:
                pass
        canvas.setFillColor(brand)
        canvas.setFont('Helvetica-Bold', 13)
        canvas.drawString(50 * mm, h - 16 * mm, 'Saint Louis College — Vehicle Management System')
        canvas.setFont('Helvetica', 10)
        canvas.setFillColor(colors.HexColor('#666666'))
        canvas.drawString(50 * mm, h - 21 * mm, report_title)
        canvas.setStrokeColor(brand)
        canvas.setLineWidth(1)
        canvas.line(15 * mm, h - 28 * mm, w - 15 * mm, h - 28 * mm)
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(colors.HexColor('#999999'))
        canvas.drawString(15 * mm, 10 * mm,
                          f'Generated {generated_at} by {generated_by} · Saint Louis College CDSO · Confidential')
        canvas.drawRightString(w - 15 * mm, 10 * mm, f'Page {doc.page}')
        canvas.restoreState()

    resp = HttpResponse(content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'

    doc = SimpleDocTemplate(
        resp, pagesize=landscape(A4),
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=32 * mm, bottomMargin=16 * mm, title=report_title,
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

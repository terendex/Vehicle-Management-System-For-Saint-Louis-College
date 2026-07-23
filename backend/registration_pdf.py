"""Vehicle registration confirmation PDF — attached to the approval email.

Lives beside report_utils.py and reuses its letterhead (draw_letterhead) so the
document the owner receives matches the CDSO's own reports.
"""

from io import BytesIO

from django.utils import timezone as tz

from report_utils import REPORT_BRAND_HEX, draw_letterhead

DAY_ORDER = ['Monday', 'Tuesday', 'Wednesday', 'Thursday',
             'Friday', 'Saturday', 'Sunday']


def _campus_days(registration):
    """Campus days rendered in week order, however they were stored."""
    days = registration.campus_days or []
    if not isinstance(days, (list, tuple)):
        return ''
    known = [d for d in DAY_ORDER if d in days]
    extra = [str(d) for d in days if d not in DAY_ORDER]
    return ', '.join(known + extra)


def registration_pdf_filename(registration):
    """e.g. 'SLC Vehicle Registration - ABC1234.pdf'"""
    plate = (registration.plate_number or 'registration').replace('/', '-')
    return f'SLC Vehicle Registration - {plate}.pdf'


def registration_confirmation_pdf(registration):
    """Build the approved-registration confirmation and return PDF bytes.

    Bytes rather than an HttpResponse: this is attached to an email, not
    served over HTTP. Portrait A4 with the same SLC letterhead as the reports.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                    Paragraph, Spacer, KeepTogether)

    brand = colors.HexColor(f'#{REPORT_BRAND_HEX}')
    r = registration
    generated_at = tz.localtime().strftime('%B %d, %Y %I:%M %p')

    def esc(s):
        return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    label_style = ParagraphStyle('lbl', fontName='Helvetica', fontSize=9,
                                 textColor=colors.HexColor('#5A5F72'), leading=12)
    value_style = ParagraphStyle('val', fontName='Helvetica-Bold', fontSize=9.5,
                                 textColor=colors.HexColor('#1A1D2B'), leading=12)
    head_style = ParagraphStyle('sec', fontName='Helvetica-Bold', fontSize=10,
                                textColor=brand, leading=13)
    note_style = ParagraphStyle('note', fontName='Helvetica', fontSize=8.5,
                                textColor=colors.HexColor('#5A5F72'), leading=12)

    def section(title, pairs):
        """Titled two-column block. Rows with no value are dropped, and the
        whole section disappears if nothing is left — so a student's PDF never
        shows blank employee fields."""
        rows = [[Paragraph(esc(k), label_style), Paragraph(esc(v), value_style)]
                for k, v in pairs if v not in (None, '', [])]
        if not rows:
            return []
        tbl = Table(rows, colWidths=[52 * mm, 118 * mm])
        tbl.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('ROWBACKGROUNDS', (0, 0), (-1, -1),
             [colors.white, colors.HexColor('#F7F8FC')]),
            ('LINEBELOW', (0, 0), (-1, -2), 0.25, colors.HexColor('#EDEFF5')),
        ]))
        return [
            KeepTogether([Paragraph(esc(title), head_style), Spacer(1, 1.5 * mm), tbl]),
            Spacer(1, 5 * mm),
        ]

    def when(dt, fmt='%B %d, %Y'):
        return tz.localtime(dt).strftime(fmt) if dt else ''

    # ── Identity fields vary by registrant type ──────────────────────────────
    kind = r.registrant_type
    if kind == 'student':
        specific = [
            ('Student ID', r.student_id),
            ('System Student ID', r.system_student_id),
            ('Level', r.get_student_level_display() if r.student_level else ''),
            ('Program', r.program.name if r.program else ''),
            ('Program & Year', r.program_year),
        ]
    elif kind == 'employee':
        specific = [
            ('Employee ID', r.employee_id),
            ('System Employee ID', r.system_employee_id),
            ('Department', r.department.name if r.department else ''),
            ('Department Type',
             r.get_department_type_display() if r.department_type else ''),
        ]
    else:  # fetcher
        specific = [
            ('Fetcher Type', r.get_fetcher_type_display() if r.fetcher_type else ''),
        ]
        for i, s in enumerate(r.fetcher_students or [], start=1):
            if not isinstance(s, dict):
                continue
            bits = [s.get('full_name') or '']
            if s.get('student_id'):
                bits.append(f"ID {s['student_id']}")
            if s.get('program_year'):
                bits.append(s['program_year'])
            specific.append((f'Student Fetched #{i}',
                             ' · '.join(b for b in bits if b)))

    story = [
        Paragraph(
            'This confirms that the vehicle registration below has been '
            '<b>approved</b>. Present this document when requested at the '
            'campus gates.',
            note_style),
        Spacer(1, 5 * mm),
    ]

    story += section('Registration Summary', [
        ('Status', r.get_status_display()),
        ('Reference No.', f'REG-{r.id:06d}'),
        ('Date Submitted', when(r.created_at)),
        ('Date Approved', when(r.reviewed_at) or generated_at),
        ('OR Number', r.or_number),
    ])

    story += section('Registrant Details', [
        ('Full Name', r.full_name),
        ('Registrant Type', r.get_registrant_type_display()),
        ('Email', r.email),
        ('Contact Number', r.contact_number),
        ('Address', r.address),
        ('Age', str(r.age) if r.age else ''),
        ("Driver's License", r.drivers_license),
    ] + specific)

    # Only present when the registrant is not the one driving (minors, SpEd).
    story += section('Authorized Driver', [
        ('Name', r.driver_name),
        ('Relationship',
         r.get_driver_relationship_display() if r.driver_relationship else ''),
        ('Contact Number', r.driver_contact),
    ])

    story += section('Vehicle Details', [
        ('Plate Number', r.plate_number),
        ('Vehicle Type', r.vehicle_type),
        ('Colour', r.vehicle_color),
        ('Conduction Number', r.conduction_number),
        ('Body Number', r.body_number),
    ])

    story += section('Campus Access', [
        ('Schedule', r.get_schedule_display() if r.schedule else ''),
        ('Campus Days', _campus_days(r)),
        ('Special Case', 'Yes' if r.is_special_case else ''),
        ('Special Case Reason', r.special_case_reason),
    ])

    story.append(Paragraph(
        'This document is system-generated and valid without a signature. '
        'Report any change of vehicle or contact information to the CDSO office.',
        note_style))

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=48 * mm, bottomMargin=18 * mm,
        title=f'Vehicle Registration Confirmation - {r.plate_number}',
    )

    def draw_frame(canvas, doc_):
        w, h = A4
        draw_letterhead(
            canvas, w, h,
            title='VEHICLE REGISTRATION CONFIRMATION',
            footer_left=f'Generated {generated_at} · Saint Louis College CDSO',
            footer_right=f'Page {doc_.page}',
        )

    doc.build(story, onFirstPage=draw_frame, onLaterPages=draw_frame)
    return buf.getvalue()

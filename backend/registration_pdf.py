"""Vehicle registration confirmation PDF — attached to the approval email.

Lives beside report_utils.py and reuses its letterhead (draw_letterhead) so the
document the owner receives matches the CDSO's own reports.
"""

from io import BytesIO

from django.utils import timezone as tz

from report_utils import REPORT_BRAND_HEX, draw_letterhead

DAY_ORDER = ['Monday', 'Tuesday', 'Wednesday', 'Thursday',
             'Friday', 'Saturday', 'Sunday']

# The uploads a registration can carry, in the order the CDSO reviews them.
DOCUMENT_FIELDS = (
    ("Driver's License Photo", 'drivers_license_image'),
    ('Assessment Form',        'assessment_form'),
    ('Official Receipt',       'or_receipt_image'),
)

# A scan off a phone is routinely several megabytes, and three of them decoded
# at once is what turns a print into a timeout. Past this the page says the
# document exists rather than trying to draw it.
MAX_EMBED_BYTES = 12 * 1024 * 1024

# Longest edge the embedded copy is scaled down to. The pictures are drawn into
# a box about 62mm across, so 1600px is still far more detail than a printer can
# put on the paper — while a 12-megapixel original would carry its full sensor
# resolution into a PDF that then has to survive an email attachment limit.
MAX_EMBED_PIXELS = 1600


def _shrink(data):
    """A copy scaled down to MAX_EMBED_PIXELS, or the original if it is smaller.

    Returns the original bytes unchanged if Pillow cannot open it — the caller
    still has reportlab's own decoder to try, and a picture that draws at full
    size beats no picture at all.
    """
    try:
        from PIL import Image

        img = Image.open(BytesIO(data))
        if max(img.size) <= MAX_EMBED_PIXELS:
            return data
        img.thumbnail((MAX_EMBED_PIXELS, MAX_EMBED_PIXELS), Image.LANCZOS)
        # Flattened to RGB: a PNG scan with an alpha channel cannot be saved as
        # JPEG, and transparency means nothing on a printed page anyway.
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        out = BytesIO()
        img.save(out, format='JPEG', quality=82, optimize=True)
        return out.getvalue()
    except Exception:
        return data


def _read_document(file_field):
    """Bytes of an uploaded document, or None with the reason it cannot be drawn.

    Returns (data, note). Exactly one of the two is ever set. A document that
    cannot be embedded is still worth naming on the page — "no receipt" and
    "receipt attached as a PDF" are different facts to whoever holds the
    printout.
    """
    if not file_field:
        return None, 'Not provided'

    name = (file_field.name or '').lower()
    if name.endswith('.pdf'):
        return None, 'Submitted as a PDF (not printed here)'
    if name.endswith(('.heic', '.heif')):
        # iPhone's default format. Pillow cannot decode it without a plugin, so
        # rendering would fail deeper in reportlab with a far less clear error.
        return None, 'Submitted as a HEIC photo (not printed here)'

    try:
        if file_field.size > MAX_EMBED_BYTES:
            return None, 'File too large to print'
        with file_field.open('rb') as fh:
            return _shrink(fh.read()), None
    except Exception:
        # Storage being unreachable must not cost the CDSO the whole document.
        return None, 'Could not be read from storage'


def _campus_days(registration):
    """Campus days rendered in week order, however they were stored."""
    days = registration.campus_days or []
    if not isinstance(days, (list, tuple)):
        return ''
    known = [d for d in DAY_ORDER if d in days]
    extra = [str(d) for d in days if d not in DAY_ORDER]
    return ', '.join(known + extra)


def registration_pdf_filename(registration, pending=False):
    """e.g. 'SLC Vehicle Registration - ABC1234.pdf'.

    The pending copy is named differently on purpose: an applicant who keeps
    both ends up with two files in one folder, and the one that is only an
    acknowledgement must not be the one they present at a gate.
    """
    plate = (registration.plate_number or 'registration').replace('/', '-')
    kind = 'Registration Acknowledgement' if pending else 'Vehicle Registration'
    return f'SLC {kind} - {plate}.pdf'


def _documents_story(registration, content_width, head_style, note_style):
    """The 'Submitted Documents' block: every upload drawn at a readable size.

    Two per row, each scaled to fit its cell without cropping — a licence
    printed to the edge of a box is a licence number nobody can read off the
    filed copy, which is the only reason the picture is on the page. Slots that
    hold nothing still get a caption, so the printout distinguishes "nothing was
    submitted" from "this page forgot to include it".
    """
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import Image, Paragraph, Spacer, Table, TableStyle

    caption_style = ParagraphStyle(
        'doccap', fontName='Helvetica-Bold', fontSize=8.5,
        textColor=colors.HexColor('#5A5F72'), leading=11, spaceAfter=2,
    )
    missing_style = ParagraphStyle(
        'docmiss', fontName='Helvetica-Oblique', fontSize=8.5,
        textColor=colors.HexColor('#8A8FA3'), leading=11,
    )

    gutter  = 8 * mm
    cell_w  = (content_width - gutter) / 2
    frame_w = cell_w - 4 * mm      # padding inside the cell
    frame_h = 62 * mm

    def cell(title, file_field):
        data, note = _read_document(file_field)
        body = None
        if data:
            try:
                iw, ih = ImageReader(BytesIO(data)).getSize()
                scale = min(frame_w / iw, frame_h / ih)
                body = Image(BytesIO(data), width=iw * scale, height=ih * scale)
            except Exception:
                # Decodable by neither Pillow nor reportlab — say so rather
                # than aborting the whole print.
                note = 'Could not be printed'
        if body is None:
            body = Paragraph(note or 'Not provided', missing_style)
        return [Paragraph(title, caption_style), body]

    cells = [cell(title, getattr(registration, field_name, None))
             for title, field_name in DOCUMENT_FIELDS]

    # A fetcher's own assessment slot is always empty — they are not enrolled.
    # What proves the trip is each listed student's own form, so those follow
    # the shared uploads, captioned with the student they belong to.
    if registration.registrant_type == 'fetcher':
        by_index = {a.student_index: a.assessment_form
                    for a in registration.fetcher_assessments.all()}
        for i, student in enumerate(registration.fetcher_students or []):
            name = (student.get('full_name') if isinstance(student, dict) else '') or f'Student #{i + 1}'
            cells.append(cell(f'Assessment Form — {name}', by_index.get(i)))

    rows = [cells[i:i + 2] for i in range(0, len(cells), 2)]
    if rows and len(rows[-1]) == 1:
        rows[-1].append('')          # an odd count leaves the last cell empty

    tbl = Table(rows, colWidths=[cell_w, cell_w], hAlign='LEFT')
    tbl.setStyle(TableStyle([
        ('VALIGN',        (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING',   (0, 0), (-1, -1), 2 * mm),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 2 * mm),
        ('TOPPADDING',    (0, 0), (-1, -1), 3 * mm),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3 * mm),
        ('BOX',           (0, 0), (-1, -1), 0.25, colors.HexColor('#EDEFF5')),
        ('INNERGRID',     (0, 0), (-1, -1), 0.25, colors.HexColor('#EDEFF5')),
    ]))

    return [
        Paragraph('Submitted Documents', head_style),
        Spacer(1, 1.5 * mm),
        tbl,
        Spacer(1, 3 * mm),
        Paragraph(
            'Scans as uploaded by the applicant. Verify against the original '
            'documents when presented at the counter.',
            note_style),
        Spacer(1, 5 * mm),
    ]


def registration_confirmation_pdf(registration, include_documents=False, pending=False):
    """Build the approved-registration confirmation and return PDF bytes.

    Bytes rather than an HttpResponse: this is attached to an email, not
    served over HTTP. Portrait A4 with the same SLC letterhead as the reports.

    `include_documents` appends the scans the applicant uploaded. Off by
    default, because the copy that goes out by email is going to the person who
    uploaded them — it would be mailing someone their own licence back, on a
    document that is already the largest thing the approval email carries. The
    CDSO's own printout turns it on: that is the copy that gets filed, and the
    file is meant to hold the evidence.

    `pending` builds the acknowledgement instead: the same record of what was
    submitted, but saying plainly that the application is still under review and
    is not a pass. It rides along with the registration-received email, so an
    applicant has something to show that they did register — which, until the
    approval PDF exists, was only ever an email in their inbox. Every claim this
    document makes about an approval is dropped rather than left blank, so it
    can never be mistaken for the one that grants entry.
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

    if pending:
        intro = ('This acknowledges that the vehicle registration below was '
                 '<b>received</b> and is awaiting CDSO review. It is proof of '
                 'application only — it is <b>not</b> a vehicle pass and does '
                 'not grant entry to the campus.')
        summary = [
            ('Status', r.get_status_display()),
            ('Reference No.', f'REG-{r.id:06d}'),
            ('Date Submitted', when(r.created_at)),
            ('OR Number', r.or_number),
        ]
    else:
        intro = ('This confirms that the vehicle registration below has been '
                 '<b>approved</b>. Present this document when requested at the '
                 'campus gates.')
        summary = [
            ('Status', r.get_status_display()),
            ('Reference No.', f'REG-{r.id:06d}'),
            ('Date Submitted', when(r.created_at)),
            ('Date Approved', when(r.reviewed_at) or generated_at),
            ('OR Number', r.or_number),
        ]

    story = [
        Paragraph(intro, note_style),
        Spacer(1, 5 * mm),
    ]

    story += section('Registration Summary', summary)

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

    if include_documents:
        story += _documents_story(r, 170 * mm, head_style, note_style)

    closing = ('This document is system-generated and valid without a signature. '
               'Report any change of vehicle or contact information to the CDSO office.')
    if pending:
        closing = ('This document is system-generated and valid without a signature. '
                   'It records a submitted application, not an approved pass — you '
                   'will receive a separate email once CDSO has reviewed it.')
    story.append(Paragraph(closing, note_style))

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=48 * mm, bottomMargin=18 * mm,
        title=(f'Vehicle Registration Acknowledgement - {r.plate_number}' if pending
               else f'Vehicle Registration Confirmation - {r.plate_number}'),
    )

    def draw_frame(canvas, doc_):
        w, h = A4
        draw_letterhead(
            canvas, w, h,
            title=('VEHICLE REGISTRATION ACKNOWLEDGEMENT' if pending
                   else 'VEHICLE REGISTRATION CONFIRMATION'),
            footer_left=f'Generated {generated_at} · Saint Louis College CDSO',
            footer_right=f'Page {doc_.page}',
        )

    doc.build(story, onFirstPage=draw_frame, onLaterPages=draw_frame)
    return buf.getvalue()

from django.core.mail import send_mail, EmailMultiAlternatives
from django.conf import settings
from email.mime.image import MIMEImage

# These templates are f-strings, not Django templates, so nothing auto-escapes.
# Owner names and CDSO-typed notes both reach the HTML body verbatim otherwise.
from vehicles.email_utils import esc

VIOLATION_TYPE_LABELS = {
    'unauthorized_entry':   'Unauthorized Entry',
    'double_parking':       'Double Parking',
    'time_exceed':          'Time Exceed',
    'no_sticker':           'No Sticker',
    'expired_registration': 'Expired Registration',
    'unauthorized':         'Unauthorized (Legacy)',
    'other':                'Other',
}

OFFENSE_LABELS = {1: '1st', 2: '2nd', 3: '3rd'}

_BASE_STYLE = """
  font-family: Arial, sans-serif;
  color: #1A1D2E;
  background: #F0F2F7;
  padding: 20px;
  margin: 0;
"""

_FOOTER = """
  <div style="background:#F8FAFC;border-top:1px solid #E2E6EE;padding:14px 32px;text-align:center;">
    <p style="font-size:12px;color:#7C80A3;margin:0;">Saint Louis College Vehicle Management System</p>
    <p style="font-size:11px;color:#B0B4C7;margin:4px 0 0;">This is an automated message. Please do not reply.</p>
  </div>
"""


def _evidence_url(violation):
    """Absolute URL of the evidence photo, or None to fall back to a cid attachment.

    With USE_R2 on — which production requires — the photo already lives at a
    public https URL, so the email can simply link it. That matters because the
    Railway half sends over Brevo's HTTP API, which has no Content-ID field: a
    `cid:` reference there renders as a broken image. A plain URL renders on both
    transports, and keeps the message small enough not to trip size limits.

    Local storage yields a relative path (`/media/...`) that means nothing in a
    mail client, so those still go out as an inline attachment.
    """
    evidence = getattr(violation, 'evidence', None)
    if not evidence:
        return None
    try:
        url = evidence.url
    except Exception:
        return None
    return url if url and url.startswith(('http://', 'https://')) else None


def _evidence_html(violation):
    """Evidence-photo block — empty when there is no photo."""
    if not getattr(violation, 'evidence', None):
        return ''
    src = _evidence_url(violation) or 'cid:evidence'
    return (
        '<div style="margin:0 0 20px;">'
        '<p style="font-size:13px;color:#5A5F72;margin:0 0 8px;font-weight:600;">Evidence Photo</p>'
        f'<img src="{src}" alt="violation evidence" '
        'style="max-width:100%;border-radius:10px;border:1px solid #E2E6EE;display:block;" />'
        '</div>'
    )


def _send_violation_email(subject, text, html, recipient, violation=None):
    """Send a violation email, inlining the evidence photo when one exists."""
    try:
        msg = EmailMultiAlternatives(
            subject=subject, body=text,
            from_email=settings.DEFAULT_FROM_EMAIL, to=[recipient],
        )
        msg.attach_alternative(html, 'text/html')
        evidence = getattr(violation, 'evidence', None)
        # Only when the photo has no public URL — otherwise the HTML above links
        # it directly and attaching it would duplicate the payload.
        if evidence and not _evidence_url(violation):
            try:
                evidence.open('rb')
                data = evidence.read()
                evidence.close()
                subtype = 'png' if (evidence.name or '').lower().endswith('.png') else 'jpeg'
                img = MIMEImage(data, _subtype=subtype)
                img.add_header('Content-ID', '<evidence>')
                img.add_header('Content-Disposition', 'inline', filename='evidence.jpg')
                msg.attach(img)
            except Exception:
                pass
        msg.send(fail_silently=True)
    except Exception:
        pass


def send_confiscation_email(violation, penalty):
    """Tell the owner an offence was recorded and what it cost their account.

    One email covers all three rungs of the ladder. The pair it replaced
    (a warning mail plus a separate "fee imposed" mail) both quoted the P150
    fine, which no longer exists.
    """
    vehicle = violation.vehicle
    owner = violation.owner or (vehicle.user if vehicle else None)
    if owner is None or not owner.email:
        return

    level  = (penalty or {}).get('level') or violation.offense_number or 1
    reason = (penalty or {}).get('reason') or ''
    until  = (penalty or {}).get('until')

    vtype_label   = VIOLATION_TYPE_LABELS.get(violation.violation_type, violation.violation_type)
    offense_label = OFFENSE_LABELS.get(level, f'{level}th')
    issued_str    = violation.issued_at.strftime('%B %d, %Y') if violation.issued_at else '-'
    plate         = violation.identifier or (vehicle.plate_number if vehicle else '')
    until_str     = until.strftime('%B %d, %Y') if until else 'lifted by the CDSO'

    notes_row = (
        f'<tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;width:130px;">Notes</td>'
        f'<td style="padding:8px 0;font-weight:600;">{esc(violation.notes)}</td></tr>'
    ) if violation.notes else ''

    is_final = level >= 3
    accent   = '#B91C1C' if is_final else '#D97706'
    tint     = '#FEF2F2' if is_final else '#FFFBEB'

    if is_final:
        consequence = (
            'This is your <strong>third and final offence</strong>. Your account is '
            f'confiscated until <strong>{esc(until_str)}</strong>, and you may not '
            'register a vehicle again unless the CDSO approves it.'
        )
    else:
        remaining = 3 - level
        consequence = (
            f'Your account is confiscated until <strong>{esc(until_str)}</strong>. '
            f'You have <strong>{remaining} offence(s)</strong> remaining before your '
            'account is confiscated for the rest of the registration period.'
        )

    html_message = f"""
    <html>
      <body style="{_BASE_STYLE}">
        <div style="max-width:580px;margin:0 auto;background:#fff;border-radius:12px;border-top:4px solid {accent};box-shadow:0 4px 20px rgba(0,0,0,.08);overflow:hidden;">
          <div style="padding:28px 32px 24px;">
            <h2 style="color:{accent};margin:0 0 6px;">&#9888; Offence {offense_label} of 3 - account confiscated</h2>
            <p style="color:#5A5F72;font-size:13px;margin:0 0 20px;">A violation has been recorded against your vehicle.</p>
            <p style="margin:0 0 4px;">Dear <strong>{esc(owner.full_name)}</strong>,</p>
            <p style="color:#5A5F72;font-size:14px;margin:0 0 24px;">
              The following violation for your vehicle (<strong>{esc(plate)}</strong>) has been recorded.
            </p>
            <div style="background:{tint};border-radius:10px;padding:16px 20px;margin-bottom:16px;">
              <table style="width:100%;border-collapse:collapse;">
                <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;width:130px;">Plate Number</td>
                    <td style="padding:8px 0;font-weight:700;font-family:monospace;">{esc(plate)}</td></tr>
                <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;">Violation</td>
                    <td style="padding:8px 0;font-weight:600;">{esc(vtype_label)}</td></tr>
                <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;">Offence</td>
                    <td style="padding:8px 0;font-weight:600;">{offense_label} of 3</td></tr>
                <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;">Date</td>
                    <td style="padding:8px 0;font-weight:600;">{issued_str}</td></tr>
                <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;">Access until</td>
                    <td style="padding:8px 0;font-weight:700;color:{accent};">{esc(until_str)}</td></tr>
                {notes_row}
              </table>
            </div>
            {_evidence_html(violation)}
            <div style="background:{tint};border-left:4px solid {accent};border-radius:6px;padding:12px 16px;margin-bottom:20px;">
              <p style="margin:0;font-size:14px;color:{accent};">{consequence}</p>
            </div>
            <p style="color:#5A5F72;font-size:14px;margin:0;">
              While your account is confiscated you may not enter or park on campus.
              Being detected at a gate or in a parking area during this period counts
              as a further offence. This violation is visible on your vehicle owner
              portal. If you believe this is a mistake, contact the CDSO office.
            </p>
          </div>
          {_FOOTER}
        </div>
      </body>
    </html>
    """

    _send_violation_email(
        subject=f"SLC Vehicle - Offence {offense_label} of 3: {vtype_label} (account confiscated)",
        text=(
            f"Dear {owner.full_name},\n\n"
            f"A {offense_label} offence ({vtype_label}) has been recorded for your vehicle "
            f"{plate}.\n\n"
            f"{reason}\n\n"
            "While your account is confiscated you may not enter or park on campus. "
            "Being detected during this period counts as a further offence.\n\n"
            + ("You may not register a vehicle again unless the CDSO approves it.\n\n"
               if is_final else "")
            + "Contact the CDSO office for any concerns."
        ),
        html=html_message,
        recipient=owner.email,
        violation=violation,
    )


def send_violation_notified_email(violation):
    """Send a notification email when CDSO manually releases a legacy violation to the owner."""
    vehicle = violation.vehicle
    if not vehicle or not vehicle.user:
        return
    owner = vehicle.user
    if not owner.email:
        return

    vtype_label = VIOLATION_TYPE_LABELS.get(violation.violation_type, violation.violation_type)
    issued_str  = violation.issued_at.strftime('%B %d, %Y') if violation.issued_at else '—'
    fine        = float(violation.fine_amount or 0)
    fine_row = (
        f'<tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;width:130px;">Fine</td>'
        f'<td style="padding:8px 0;font-weight:700;color:#DC2626;">₱{fine:.2f}</td></tr>'
    ) if fine > 0 else ''
    notes_row = (
        f'<tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;width:130px;">Notes</td>'
        f'<td style="padding:8px 0;font-weight:600;">{esc(violation.notes)}</td></tr>'
    ) if violation.notes else ''

    html_message = f"""
    <html>
      <body style="{_BASE_STYLE}">
        <div style="max-width:580px;margin:0 auto;background:#fff;border-radius:12px;border-top:4px solid #D97706;box-shadow:0 4px 20px rgba(0,0,0,.08);overflow:hidden;">
          <div style="padding:28px 32px 24px;">
            <h2 style="color:#D97706;margin:0 0 6px;">&#9888; Violation Notice</h2>
            <p style="color:#5A5F72;font-size:13px;margin:0 0 20px;">A violation has been officially recorded against your vehicle.</p>
            <p style="margin:0 0 4px;">Dear <strong>{esc(owner.full_name)}</strong>,</p>
            <p style="color:#5A5F72;font-size:14px;margin:0 0 24px;">
              The CDSO office has issued a violation notice for your vehicle (<strong>{esc(vehicle.plate_number)}</strong>).
              Please review the details below and settle any outstanding amount at the CDSO office.
            </p>
            <div style="background:#FFFBEB;border-radius:10px;padding:16px 20px;margin-bottom:24px;">
              <table style="width:100%;border-collapse:collapse;">
                <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;width:130px;">Plate Number</td>
                    <td style="padding:8px 0;font-weight:700;font-family:monospace;">{esc(vehicle.plate_number)}</td></tr>
                <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;">Violation</td>
                    <td style="padding:8px 0;font-weight:600;">{esc(vtype_label)}</td></tr>
                <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;">Date</td>
                    <td style="padding:8px 0;font-weight:600;">{issued_str}</td></tr>
                {fine_row}
                {notes_row}
              </table>
            </div>
            {_evidence_html(violation)}
            <p style="color:#5A5F72;font-size:14px;margin:0;">
              Please contact or visit the CDSO office to address this violation. This record is now visible on your vehicle owner portal.
            </p>
          </div>
          {_FOOTER}
        </div>
      </body>
    </html>
    """

    _send_violation_email(
        subject=f"SLC Vehicle — Violation Notice: {vtype_label}",
        text=(
            f"Dear {owner.full_name},\n\n"
            f"The CDSO office has issued a violation notice ({vtype_label}) "
            f"for your vehicle {vehicle.plate_number}.\n\n"
            f"{'Fine: ₱' + f'{fine:.2f}' + chr(10) if fine > 0 else ''}"
            f"Please visit the CDSO office to address this violation.\n\n"
            f"This record is now visible on your vehicle owner portal."
        ),
        html=html_message,
        recipient=owner.email,
        violation=violation,
    )


def send_violation_resolved_email(violation):
    """Send an email to the vehicle owner when a violation is marked resolved/cleared."""
    vehicle = violation.vehicle
    if not vehicle or not vehicle.user:
        return

    owner = vehicle.user
    recipient = owner.email
    if not recipient:
        return

    vtype_label  = VIOLATION_TYPE_LABELS.get(violation.violation_type, violation.violation_type)
    notes_section = (
        f'<tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;width:130px;">Notes</td>'
        f'<td style="padding:8px 0;font-weight:600;">{esc(violation.notes)}</td></tr>'
    ) if violation.notes else ''

    issued_str = violation.issued_at.strftime('%B %d, %Y') if violation.issued_at else '—'

    html_message = f"""
    <html>
      <body style="{_BASE_STYLE}">
        <div style="max-width:580px;margin:0 auto;background:#fff;border-radius:12px;border-top:4px solid #059669;box-shadow:0 4px 20px rgba(0,0,0,.08);overflow:hidden;">
          <div style="padding:28px 32px 24px;">
            <h2 style="color:#059669;margin:0 0 6px;">Violation Cleared &#10003;</h2>
            <p style="color:#5A5F72;font-size:13px;margin:0 0 20px;">Your violation has been reviewed and cleared by the CDSO office.</p>
            <p style="margin:0 0 4px;">Dear <strong>{esc(owner.full_name)}</strong>,</p>
            <p style="color:#5A5F72;font-size:14px;margin:0 0 24px;">
              The following violation for your vehicle (<strong>{esc(vehicle.plate_number)}</strong>) has been cleared.
              Campus entry access has been restored.
            </p>
            <div style="background:#F0FDF4;border-radius:10px;padding:16px 20px;margin-bottom:24px;">
              <table style="width:100%;border-collapse:collapse;">
                <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;width:130px;">Plate Number</td>
                    <td style="padding:8px 0;font-weight:700;font-family:monospace;">{esc(vehicle.plate_number)}</td></tr>
                <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;">Violation Type</td>
                    <td style="padding:8px 0;font-weight:600;">{esc(vtype_label)}</td></tr>
                <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;">Date Issued</td>
                    <td style="padding:8px 0;font-weight:600;">{issued_str}</td></tr>
                {notes_section}
                <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;">Status</td>
                    <td style="padding:8px 0;"><span style="background:#D1FAE5;color:#065F46;font-weight:700;padding:3px 10px;border-radius:20px;font-size:12px;">Cleared</span></td></tr>
              </table>
            </div>
            <p style="color:#5A5F72;font-size:14px;margin:0;">
              Your warning cycle has been reset. Please note that your next school year vehicle registration
              will require additional review. Contact the CDSO office for any concerns.
            </p>
          </div>
          {_FOOTER}
        </div>
      </body>
    </html>
    """

    send_mail(
        subject=f"SLC Vehicle — Violation Cleared: {vtype_label}",
        message=(
            f"Dear {owner.full_name},\n\n"
            f"The violation ({vtype_label}) on your vehicle {vehicle.plate_number} "
            f"has been cleared. Campus entry access has been restored.\n\n"
            f"Your warning cycle has been reset.\n\n"
            f"Note: Next school year registration will require additional CDSO review.\n\n"
            f"Contact the CDSO office for any concerns."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[recipient],
        html_message=html_message,
        fail_silently=True,
    )

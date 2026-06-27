from django.core.mail import send_mail
from django.conf import settings

VIOLATION_TYPE_LABELS = {
    'no_sticker':           'No Sticker',
    'expired_registration': 'Expired Registration',
    'unauthorized':         'Unauthorized Entry',
    'other':                'Other',
}


def send_violation_resolved_email(violation):
    """Send an email to the vehicle owner when a violation is marked resolved."""
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
        f'<td style="padding:8px 0;font-weight:600;">{violation.notes}</td></tr>'
    ) if violation.notes else ''

    issued_str = violation.issued_at.strftime('%B %d, %Y') if violation.issued_at else '—'

    html_message = f"""
    <html>
      <body style="font-family:Arial,sans-serif;color:#1A1D2E;background:#F0F2F7;padding:20px;margin:0;">
        <div style="max-width:580px;margin:0 auto;background:#fff;border-radius:12px;border-top:4px solid #059669;box-shadow:0 4px 20px rgba(0,0,0,.08);overflow:hidden;">
          <div style="padding:28px 32px 24px;">
            <h2 style="color:#059669;margin:0 0 6px;">Violation Resolved &#10003;</h2>
            <p style="color:#5A5F72;font-size:13px;margin:0 0 20px;">A violation on your vehicle has been reviewed and cleared by administration.</p>
            <p style="margin:0 0 4px;">Dear <strong>{owner.full_name}</strong>,</p>
            <p style="color:#5A5F72;font-size:14px;margin:0 0 24px;">
              The following violation for your vehicle (<strong>{vehicle.plate_number}</strong>) has been resolved.
            </p>
            <div style="background:#F0FDF4;border-radius:10px;padding:16px 20px;margin-bottom:24px;">
              <table style="width:100%;border-collapse:collapse;">
                <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;width:130px;">Plate Number</td>
                    <td style="padding:8px 0;font-weight:700;font-family:monospace;">{vehicle.plate_number}</td></tr>
                <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;">Violation Type</td>
                    <td style="padding:8px 0;font-weight:600;">{vtype_label}</td></tr>
                <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;">Date Issued</td>
                    <td style="padding:8px 0;font-weight:600;">{issued_str}</td></tr>
                {notes_section}
                <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;">Status</td>
                    <td style="padding:8px 0;"><span style="background:#D1FAE5;color:#065F46;font-weight:700;padding:3px 10px;border-radius:20px;font-size:12px;">Resolved</span></td></tr>
              </table>
            </div>
            <p style="color:#5A5F72;font-size:14px;margin:0;">
              This violation has been cleared from your active record. If you have any concerns, please contact the CDSO office.
            </p>
          </div>
          <div style="background:#F8FAFC;border-top:1px solid #E2E6EE;padding:14px 32px;text-align:center;">
            <p style="font-size:12px;color:#7C80A3;margin:0;">Saint Louis College Vehicle Management System</p>
            <p style="font-size:11px;color:#B0B4C7;margin:4px 0 0;">This is an automated message. Please do not reply.</p>
          </div>
        </div>
      </body>
    </html>
    """

    send_mail(
        subject=f"SLC Vehicle — Violation Resolved: {vtype_label}",
        message=(
            f"Dear {owner.full_name},\n\n"
            f"The violation ({vtype_label}) on your vehicle {vehicle.plate_number} "
            f"has been resolved and cleared from your active record.\n\n"
            f"If you have any concerns, please contact the CDSO office."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[recipient],
        html_message=html_message,
        fail_silently=True,
    )

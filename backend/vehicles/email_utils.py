from django.core.mail import send_mail
from django.conf import settings
import qrcode
from io import BytesIO
import base64


def _generate_qr_base64(data):
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buffer = BytesIO()
    img.save(buffer)
    return base64.b64encode(buffer.getvalue()).decode()


def send_acceptance_email(registration, temp_password, user_code=None):
    # Generate QR code
    qr_data = f"VEHICLE:{registration.plate_number}|ID:{registration.id}"
    qr_base64 = _generate_qr_base64(qr_data)

    # Determine system-assigned registration ID
    system_id = registration.system_student_id or registration.system_employee_id or '\u2014'

    # Build identity rows based on registrant type (no nested f-strings)
    if registration.registrant_type == 'student':
        id_label  = 'Student ID'
        id_value  = registration.student_id or '\u2014'
        id2_label = 'Program &amp; Year'
        id2_value = registration.program_year or '\u2014'
    else:
        id_label  = 'Employee ID'
        id_value  = registration.employee_id or '\u2014'
        id2_label = 'Department'
        id2_value = registration.department or '\u2014'

    identity_rows = (
        f'<tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;width:140px;">{id_label}</td>'
        f'<td style="padding:8px 0;font-weight:600;">{id_value}</td></tr>'
        f'<tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;">{id2_label}</td>'
        f'<td style="padding:8px 0;font-weight:600;">{id2_value}</td></tr>'
    )

    # Campus days row (only for students) — built separately to avoid nested f-string
    if registration.registrant_type == 'student':
        campus_days_str = ', '.join(registration.campus_days) if registration.campus_days else '\u2014'
        campus_days_row = (
            f'<tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;">Campus Days</td>'
            f'<td style="padding:8px 0;font-weight:600;">{campus_days_str}</td></tr>'
        )
    else:
        campus_days_row = ''

    # Portal account ID (use table layout — flex not supported in many email clients)
    portal_id_display   = user_code or '\u2014'
    contact_val         = registration.contact_number or '\u2014'
    address_val         = registration.address or '\u2014'
    license_val         = registration.drivers_license or '\u2014'
    color_val           = registration.vehicle_color or '\u2014'
    conduction_val      = registration.conduction_number or '\u2014'

    html_message = f"""
    <html>
        <body style="font-family: Arial, sans-serif; color: #1A1D2E; background-color: #F0F2F7; padding: 20px; margin: 0;">
            <div style="max-width: 620px; margin: 0 auto; background: #FFFFFF; border-radius: 12px; border-top: 4px solid #2A2B61; box-shadow: 0 4px 20px rgba(0,0,0,0.08); overflow: hidden;">

                <!-- Header -->
                <div style="padding: 28px 32px 0;">
                    <h2 style="color: #2A2B61; margin: 0 0 6px;">Vehicle Registration Approved &#10003;</h2>
                    <p style="color: #5A5F72; margin: 0 0 16px; font-size: 14px;">Your registration has been reviewed and accepted by the administration.</p>
                    <p style="margin: 0 0 4px;">Dear <strong>{registration.full_name}</strong>,</p>
                    <p style="color: #5A5F72; font-size: 14px; margin: 0 0 24px;">
                        Your vehicle registration for plate number <strong style="color:#2A2B61;">{registration.plate_number}</strong> has been approved.
                        Below are your account details and vehicle access QR code.
                    </p>
                </div>

                <!-- System IDs Banner (table-based for email client compatibility) -->
                <div style="margin: 0 32px 24px; background: #EEF0FF; border-radius: 10px; padding: 16px 20px;">
                    <table style="width:100%; border-collapse:collapse;">
                        <tr>
                            <td style="padding: 0; vertical-align: top; width: 50%;">
                                <div style="font-size: 11px; color: #6366F1; text-transform: uppercase; letter-spacing: 0.08em; font-weight: 700; margin-bottom: 4px;">Portal Account ID</div>
                                <div style="font-family: monospace; font-size: 16px; font-weight: 700; color: #2A2B61; letter-spacing: 0.5px;">{portal_id_display}</div>
                            </td>
                            <td style="padding: 0; vertical-align: top; border-left: 1px solid #C7C9E8; padding-left: 20px; width: 50%;">
                                <div style="font-size: 11px; color: #6366F1; text-transform: uppercase; letter-spacing: 0.08em; font-weight: 700; margin-bottom: 4px;">System Registration ID</div>
                                <div style="font-family: monospace; font-size: 16px; font-weight: 700; color: #2A2B61; letter-spacing: 0.5px;">{system_id}</div>
                            </td>
                        </tr>
                    </table>
                </div>

                <!-- Personal Info -->
                <div style="margin: 0 32px 20px;">
                    <h4 style="color: #2A2B61; font-size: 13px; text-transform: uppercase; letter-spacing: 0.06em; margin: 0 0 12px; border-bottom: 1px solid #E2E6EE; padding-bottom: 8px;">Personal Information</h4>
                    <table style="width:100%; border-collapse:collapse;">
                        <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;width:140px;">Full Name</td><td style="padding:8px 0;font-weight:600;">{registration.full_name}</td></tr>
                        <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;">Email</td><td style="padding:8px 0;font-weight:600;">{registration.email}</td></tr>
                        <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;">Type</td><td style="padding:8px 0;font-weight:600;text-transform:capitalize;">{registration.registrant_type}</td></tr>
                        {identity_rows}
                        <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;">Contact</td><td style="padding:8px 0;font-weight:600;">{contact_val}</td></tr>
                        <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;">Address</td><td style="padding:8px 0;font-weight:600;">{address_val}</td></tr>
                        <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;">Driver&#39;s License</td><td style="padding:8px 0;font-weight:600;">{license_val}</td></tr>
                        {campus_days_row}
                    </table>
                </div>

                <!-- Vehicle Info -->
                <div style="margin: 0 32px 20px;">
                    <h4 style="color: #2A2B61; font-size: 13px; text-transform: uppercase; letter-spacing: 0.06em; margin: 0 0 12px; border-bottom: 1px solid #E2E6EE; padding-bottom: 8px;">Vehicle Information</h4>
                    <table style="width:100%; border-collapse:collapse;">
                        <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;width:140px;">Plate Number</td><td style="padding:8px 0;font-weight:700;font-family:monospace;color:#2A2B61;">{registration.plate_number}</td></tr>
                        <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;">Vehicle Type</td><td style="padding:8px 0;font-weight:600;text-transform:capitalize;">{registration.vehicle_type}</td></tr>
                        <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;">Color</td><td style="padding:8px 0;font-weight:600;">{color_val}</td></tr>
                        <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;">Conduction No.</td><td style="padding:8px 0;font-weight:600;">{conduction_val}</td></tr>
                    </table>
                </div>

                <!-- QR Code -->
                <div style="text-align: center; margin: 0 32px 24px; background: #F8FAFC; border-radius: 10px; padding: 24px;">
                    <p style="margin: 0 0 12px; color: #5A5F72; font-size: 13px;">Present this QR code to security personnel upon entry:</p>
                    <img src="data:image/png;base64,{qr_base64}" alt="Vehicle QR Code" style="border: 2px solid #E2E6EE; border-radius: 8px; padding: 8px; background: white; max-width: 200px;" />
                </div>

                <!-- Login Credentials -->
                <div style="margin: 0 32px 24px; background: #F0F2F7; border-radius: 10px; padding: 20px; border-left: 4px solid #2A2B61;">
                    <h4 style="color: #2A2B61; margin: 0 0 12px; font-size: 14px;">Portal Login Credentials</h4>
                    <table style="width:100%; border-collapse:collapse;">
                        <tr><td style="padding:6px 0;color:#5A5F72;font-size:13px;width:80px;">Email</td><td style="padding:6px 0;font-weight:700;">{registration.email}</td></tr>
                        <tr><td style="padding:6px 0;color:#5A5F72;font-size:13px;">Password</td><td style="padding:6px 0;font-weight:700;font-family:monospace;font-size:15px;letter-spacing:1px;">{temp_password}</td></tr>
                    </table>
                    <p style="color: #DC2626; font-size: 12px; margin: 12px 0 0;"><strong>Important:</strong> You will be prompted to change this password on your first login.</p>
                </div>

                <!-- Footer -->
                <div style="background: #F8FAFC; border-top: 1px solid #E2E6EE; padding: 16px 32px; text-align: center;">
                    <p style="font-size: 12px; color: #7C80A3; margin: 0;">Saint Louis College Vehicle Management System</p>
                    <p style="font-size: 11px; color: #B0B4C7; margin: 4px 0 0;">This is an automated message. Please do not reply.</p>
                </div>

            </div>
        </body>
    </html>
    """

    send_mail(
        subject="SLC Vehicle Registration Approved \u2014 Your Account & Credentials",
        message=(
            f"Your vehicle registration has been approved.\n\n"
            f"Portal Account ID: {portal_id_display}\n"
            f"System Registration ID: {system_id}\n\n"
            f"Email: {registration.email}\n"
            f"Temporary Password: {temp_password}\n\n"
            f"You will be prompted to change your password on first login."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[registration.email],
        html_message=html_message,
        fail_silently=True,
    )


def send_rejection_email(registration, reason):
    rejection_reason = reason or 'No specific reason provided.'

    html_message = f"""
    <html>
        <body style="font-family: Arial, sans-serif; color: #1A1D2E; background-color: #F0F2F7; padding: 20px; margin: 0;">
            <div style="max-width: 600px; margin: 0 auto; background: #FFFFFF; padding: 30px; border-radius: 12px; border-top: 4px solid #DC2626; box-shadow: 0 4px 10px rgba(0,0,0,0.05);">
                <h2 style="color: #DC2626; margin-top: 0;">Vehicle Registration Declined</h2>
                <p>Dear {registration.full_name},</p>
                <p>We regret to inform you that your vehicle registration for plate number <strong>{registration.plate_number}</strong> has been declined.</p>
                <div style="background: #FEF2F2; border-left: 4px solid #DC2626; padding: 15px; margin: 20px 0; border-radius: 4px;">
                    <h4 style="margin: 0 0 10px 0; color: #991B1B;">Reason for Rejection:</h4>
                    <p style="margin: 0; color: #7F1D1D;">{rejection_reason}</p>
                </div>
                <p>If you have any questions or would like to submit a new application, please contact the administration office.</p>
                <hr style="border: 0; border-top: 1px solid #E2E6EE; margin: 20px 0;" />
                <p style="font-size: 12px; color: #7C80A3; text-align: center;">Saint Louis College Vehicle Management System</p>
            </div>
        </body>
    </html>
    """

    send_mail(
        subject="SLC Vehicle Registration Status Update",
        message=f"Your vehicle registration has been declined.\n\nReason: {rejection_reason}",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[registration.email],
        html_message=html_message,
        fail_silently=True,
    )

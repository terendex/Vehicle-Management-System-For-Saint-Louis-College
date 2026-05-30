from django.core.mail import send_mail
from django.conf import settings
from django.template.loader import render_to_string
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
    img.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()

def send_acceptance_email(registration):
    # Generate the payload for the QR code.
    # In a real app this might be a unique vehicle ID or token.
    # Here we'll use a JSON string or just the plate number.
    qr_data = f"VEHICLE:{registration.plate_number}|ID:{registration.id}"
    qr_base64 = _generate_qr_base64(qr_data)
    
    html_message = f"""
    <html>
        <body style="font-family: Arial, sans-serif; color: #1A1D2E; background-color: #F0F2F7; padding: 20px;">
            <div style="max-width: 600px; margin: 0 auto; background: #FFFFFF; padding: 30px; border-radius: 10px; border-top: 4px solid #2A2B61; box-shadow: 0 4px 10px rgba(0,0,0,0.05);">
                <h2 style="color: #2A2B61; margin-top: 0;">Vehicle Registration Approved</h2>
                <p>Dear {registration.full_name},</p>
                <p>Your vehicle registration for plate number <strong>{registration.plate_number}</strong> has been approved.</p>
                <p>Please find your access QR code below. You will need to present this code to security personnel upon entry.</p>
                <div style="text-align: center; margin: 30px 0;">
                    <img src="data:image/png;base64,{qr_base64}" alt="Vehicle QR Code" style="border: 2px solid #E2E6EE; border-radius: 8px; padding: 10px; background: white;" />
                </div>
                <p>You can also log in to your account at any time to view your details.</p>
                <hr style="border: 0; border-top: 1px solid #E2E6EE; margin: 20px 0;" />
                <p style="font-size: 12px; color: #7C80A3; text-align: center;">Saint Louis College Vehicle Management System</p>
            </div>
        </body>
    </html>
    """
    
    send_mail(
        subject="SLC Vehicle Registration Approved",
        message="Your vehicle registration has been approved.",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[registration.email],
        html_message=html_message,
        fail_silently=True,
    )

def send_rejection_email(registration, reason):
    html_message = f"""
    <html>
        <body style="font-family: Arial, sans-serif; color: #1A1D2E; background-color: #F0F2F7; padding: 20px;">
            <div style="max-width: 600px; margin: 0 auto; background: #FFFFFF; padding: 30px; border-radius: 10px; border-top: 4px solid #DC2626; box-shadow: 0 4px 10px rgba(0,0,0,0.05);">
                <h2 style="color: #DC2626; margin-top: 0;">Vehicle Registration Declined</h2>
                <p>Dear {registration.full_name},</p>
                <p>We regret to inform you that your vehicle registration for plate number <strong>{registration.plate_number}</strong> has been declined.</p>
                <div style="background: #FEF2F2; border-left: 4px solid #DC2626; padding: 15px; margin: 20px 0; border-radius: 4px;">
                    <h4 style="margin: 0 0 10px 0; color: #991B1B;">Reason for Rejection:</h4>
                    <p style="margin: 0; color: #7F1D1D;">{reason}</p>
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
        message="Your vehicle registration has been declined.",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[registration.email],
        html_message=html_message,
        fail_silently=True,
    )

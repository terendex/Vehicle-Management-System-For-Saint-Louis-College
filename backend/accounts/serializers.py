import re
from django.db import transaction
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from .models import User, AuditLog


def validate_password_strength(password):
    """Enforce strong password policy."""
    errors = []
    if len(password) < 8:
        errors.append('Password must be at least 8 characters long.')
    if not re.search(r'[A-Z]', password):
        errors.append('Password must contain at least one uppercase letter.')
    if not re.search(r'[a-z]', password):
        errors.append('Password must contain at least one lowercase letter.')
    if not re.search(r'[0-9]', password):
        errors.append('Password must contain at least one number.')
    if not re.search(r'[!@#$%^&*()_+\-=\[\]{};\':\"\\|,.<>\/?]', password):
        errors.append('Password must contain at least one special character.')
    if errors:
        raise serializers.ValidationError(errors)


class UserSerializer(serializers.ModelSerializer):
    photo_url       = serializers.SerializerMethodField()
    registrant_type = serializers.SerializerMethodField()

    class Meta:
        model  = User
        fields = ['id', 'user_code', 'full_name', 'email', 'role', 'is_active', 'date_joined', 'must_change_password', 'photo_url', 'gate_assignment', 'agency', 'qr_token', 'registrant_type']

    def get_photo_url(self, obj):
        if not obj.photo:
            return None
        request = self.context.get('request')
        if request:
            return request.build_absolute_uri(obj.photo.url)
        return obj.photo.url

    def get_registrant_type(self, obj):
        reg = obj.registrations.first()
        return reg.registrant_type if reg else None


class UserUpdateSerializer(serializers.ModelSerializer):
    """For editing user details (no password change). Accepts optional photo upload."""
    photo = serializers.ImageField(required=False, allow_null=True)

    class Meta:
        model  = User
        fields = ['full_name', 'email', 'role', 'photo', 'gate_assignment']

    def validate_email(self, value):
        value = value.strip().lower()
        user = self.instance
        if User.objects.exclude(pk=user.pk).filter(email__iexact=value).exists():
            raise serializers.ValidationError('A user with this email already exists.')
        return value


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)
    confirm_password = serializers.CharField(write_only=True)

    class Meta:
        model  = User
        fields = ['full_name', 'email', 'password', 'confirm_password', 'role', 'gate_assignment']
        extra_kwargs = {'gate_assignment': {'required': False, 'allow_null': True, 'allow_blank': True}}

    def validate_email(self, value):
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('A user with this email already exists.')
        return value

    def validate(self, attrs):
        if attrs['password'] != attrs.pop('confirm_password'):
            raise serializers.ValidationError({'confirm_password': 'Passwords do not match.'})
        validate_password_strength(attrs['password'])
        return attrs

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)


def _generate_secure_password():
    """Return a 12-char random password that satisfies the strength policy."""
    import secrets as _s
    import string as _st
    specials = '!@#$%^&*'
    pool = _st.ascii_letters + _st.digits + specials
    while True:
        pwd = ''.join(_s.choice(pool) for _ in range(12))
        if (any(c.isupper() for c in pwd)
                and any(c.islower() for c in pwd)
                and any(c.isdigit() for c in pwd)
                and any(c in specials for c in pwd)):
            return pwd


def _send_account_created_email(full_name, email, password, extra_rows=None):
    """Email a newly-created account's login info. Temp password must be
    changed on first login (must_change_password is set by the caller)."""
    from django.core.mail import send_mail
    from django.conf import settings as _cfg

    frontend_url = getattr(_cfg, 'FRONTEND_URL', 'http://localhost:5173')
    extra_rows = extra_rows or []
    extra_html = ''.join(
        f'<tr><td style="padding:8px 12px;background:#F0F2F7;font-weight:600;">{label}</td>'
        f'<td style="padding:8px 12px;">{value}</td></tr>'
        for label, value in extra_rows
    )
    extra_text = ''.join(f"{label:<10}: {value}\n" for label, value in extra_rows)

    html = f"""
    <html><body style="font-family:Arial,sans-serif;color:#1A1D2E;background:#F0F2F7;padding:20px;">
      <div style="max-width:540px;margin:0 auto;background:#fff;border-radius:12px;
                  border-top:4px solid #2A2B61;box-shadow:0 4px 20px rgba(0,0,0,.08);overflow:hidden;">
        <div style="padding:28px 32px 24px;">
          <h2 style="color:#2A2B61;margin:0 0 8px;">Your SLC Account is Ready</h2>
          <p style="color:#5A5F72;font-size:14px;margin:0 0 20px;">
            Hello <strong>{full_name}</strong>, the administrator has created your account.
          </p>
          <table style="width:100%;border-collapse:collapse;margin-bottom:20px;">
            <tr><td style="padding:8px 12px;background:#F0F2F7;font-weight:600;width:40%;">Login URL</td>
                <td style="padding:8px 12px;">{frontend_url}/login</td></tr>
            <tr><td style="padding:8px 12px;background:#F0F2F7;font-weight:600;">Email</td>
                <td style="padding:8px 12px;">{email}</td></tr>
            {extra_html}
            <tr><td style="padding:8px 12px;background:#F0F2F7;font-weight:600;">Temp Password</td>
                <td style="padding:8px 12px;font-family:monospace;font-size:15px;">{password}</td></tr>
          </table>
          <p style="color:#DC2626;font-size:13px;background:#FEF2F2;border:1px solid #FECACA;
                     border-radius:8px;padding:10px 14px;margin:0 0 20px;">
            Please log in and <strong>change your password immediately</strong>.
          </p>
          <p style="color:#9CA3B0;font-size:12px;margin:0;">
            This is an automated message from the Saint Louis College Vehicle Management System.
            Do not reply to this email.
          </p>
        </div>
      </div>
    </body></html>
    """
    try:
        send_mail(
            subject='SLC Vehicle Management — Your Account Has Been Created',
            message=(
                f"Hello {full_name},\n\n"
                f"Your account has been created by the administrator.\n\n"
                f"Login URL : {frontend_url}/login\n"
                f"Email     : {email}\n"
                f"{extra_text}"
                f"Password  : {password}\n\n"
                f"Please log in and change your password immediately.\n\n"
                f"Saint Louis College Vehicle Management System"
            ),
            from_email=_cfg.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            html_message=html,
            fail_silently=True,
        )
    except Exception:
        pass


class GuardCreateSerializer(serializers.Serializer):
    """Admin creates a security-guard account. A temporary password is
    auto-generated and emailed to the guard, who must change it on first login.
    Guards log in at the dedicated guard gate login page (credentials or QR badge).
    Gate is assigned when the guard selects a gate at the kiosk login screen,
    not at creation time."""
    full_name = serializers.CharField(max_length=150)
    email     = serializers.EmailField()
    agency    = serializers.CharField(max_length=150)

    def validate_full_name(self, value):
        if not value.strip():
            raise serializers.ValidationError('Full name is required.')
        return value.strip()

    def validate_email(self, value):
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('A user with this email already exists.')
        return value

    def validate_agency(self, value):
        if not value.strip():
            raise serializers.ValidationError('Agency is required.')
        return value.strip()

    def create(self, validated_data):
        password = _generate_secure_password()
        user = User.objects.create_user(
            email=validated_data['email'],
            full_name=validated_data['full_name'],
            password=password,
            role='security',
            agency=validated_data['agency'],
            must_change_password=True,
        )
        _send_account_created_email(
            full_name=validated_data['full_name'],
            email=validated_data['email'],
            password=password,
            extra_rows=[('Agency', validated_data['agency'])],
        )
        return user


class AdminOwnerCreateSerializer(serializers.Serializer):
    """Admin creates a vehicle-owner account directly.  A temporary password is
    auto-generated and emailed to the owner."""
    # Personal
    last_name       = serializers.CharField(max_length=100)
    first_name      = serializers.CharField(max_length=100)
    middle_name     = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    email           = serializers.EmailField()
    contact_number  = serializers.CharField(max_length=50,  required=False, allow_blank=True, default='')
    age             = serializers.IntegerField(required=False, allow_null=True, default=None)
    drivers_license = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    address         = serializers.CharField(required=False, allow_blank=True, default='')

    # Registration type
    registrant_type = serializers.ChoiceField(choices=['student', 'employee', 'fetcher'])

    # Student-specific
    student_id   = serializers.CharField(max_length=50,  required=False, allow_blank=True, default='')
    program_year = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    campus_days  = serializers.ListField(
        child=serializers.CharField(), required=False, default=list,
    )

    # Employee-specific
    employee_id     = serializers.CharField(max_length=50, required=False, allow_blank=True, default='')
    department_type = serializers.CharField(max_length=20, required=False, allow_blank=True, default='')

    # Vehicle
    plate_number      = serializers.CharField(max_length=20)
    conduction_number = serializers.CharField(max_length=50, required=False, allow_blank=True, default='')
    vehicle_type      = serializers.CharField(max_length=50)
    vehicle_color     = serializers.CharField(max_length=50, required=False, allow_blank=True, default='')
    body_number       = serializers.CharField(max_length=50, required=False, allow_blank=True, default='')

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('A user with this email already exists.')
        return value.lower()

    def validate_plate_number(self, value):
        from vehicles.models import Vehicle
        normalized = value.strip().upper().replace(' ', '')
        if Vehicle.objects.filter(plate_number__iexact=normalized).exists():
            raise serializers.ValidationError('A vehicle with this plate number is already registered.')
        return normalized

    def validate(self, attrs):
        # Guard against collisions with an existing active registration so a
        # duplicate is reported as a clean per-field 400 rather than hitting a DB
        # constraint mid-create (which the atomic create below would roll back).
        from vehicles.models import VehicleRegistration
        from vehicles.views import _plate_conflict, _email_conflict, _license_conflict, _id_conflict
        active = [VehicleRegistration.Status.PENDING, VehicleRegistration.Status.ACCEPTED]
        qs = VehicleRegistration.objects.filter(status__in=active)

        errors = {}
        c = _plate_conflict(attrs['plate_number'], qs)
        if c:
            errors['plate_number'] = c
        c = _email_conflict(attrs['email'], qs)
        if c:
            errors['email'] = c
        c = _license_conflict(attrs.get('drivers_license', ''), qs)
        if c:
            errors['drivers_license'] = c
        c = _id_conflict(attrs['registrant_type'], attrs.get('student_id', ''), attrs.get('employee_id', ''), qs)
        if c:
            errors['student_id' if attrs['registrant_type'] == 'student' else 'employee_id'] = c
        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        from vehicles.models import Vehicle, VehicleRegistration

        reg_type    = validated_data['registrant_type']
        last        = validated_data['last_name'].strip()
        first       = validated_data['first_name'].strip()
        middle      = validated_data.get('middle_name', '').strip()
        full_name   = ', '.join(filter(None, [last, first, middle]))
        email       = validated_data['email']
        plate       = validated_data['plate_number']
        campus_days = validated_data.get('campus_days') or []

        password = _generate_secure_password()

        user = User.objects.create_user(
            email=email,
            full_name=full_name,
            password=password,
            role='vehicle_owner',
            must_change_password=True,
            contact=validated_data.get('contact_number', ''),
            address=validated_data.get('address', ''),
        )

        vtype_raw = validated_data.get('vehicle_type', '')
        vtype_map = {
            'sedan': 'car', 'suv': 'car', 'car': 'car',
            'motorcycle': 'motorcycle', 'tricycle': 'motorcycle',
            'van': 'van', 'truck': 'truck', 'bus': 'bus',
        }
        vtype_norm = vtype_map.get(vtype_raw.lower(), 'car')

        vehicle = Vehicle.objects.create(
            plate_number=plate,
            vehicle_type=vtype_norm,
            color=validated_data.get('vehicle_color', ''),
            is_authorized=True,
            user=user,
        )

        days_set  = set(campus_days)
        mwf_days  = {'Monday', 'Wednesday', 'Friday'}
        tths_days = {'Tuesday', 'Thursday', 'Saturday'}
        if days_set == mwf_days:
            schedule = 'MWF'
        elif days_set == tths_days:
            schedule = 'TTHS'
        elif days_set:
            schedule = 'MIXED'
        else:
            schedule = 'ANY'

        VehicleRegistration.objects.create(
            user=user,
            vehicle=vehicle,
            registrant_type=reg_type,
            full_name=full_name,
            email=email,
            address=validated_data.get('address', ''),
            contact_number=validated_data.get('contact_number', ''),
            age=validated_data.get('age'),
            drivers_license=validated_data.get('drivers_license', ''),
            campus_days=campus_days,
            schedule=schedule,
            student_id=validated_data.get('student_id', ''),
            program_year=validated_data.get('program_year', ''),
            employee_id=validated_data.get('employee_id', ''),
            department_type=validated_data.get('department_type', '') or None,
            plate_number=plate,
            conduction_number=validated_data.get('conduction_number', ''),
            vehicle_type=vtype_raw,
            vehicle_color=validated_data.get('vehicle_color', ''),
            body_number=validated_data.get('body_number', ''),
            status=VehicleRegistration.Status.ACCEPTED,
            source=VehicleRegistration.Source.DIRECT,
        )

        _send_account_created_email(full_name=full_name, email=email, password=password)

        return user


class AdminReplaceSerializer(serializers.Serializer):
    """Create a new admin and delete the requesting admin. A temporary password
    is auto-generated and emailed to the new admin, who must change it on first login."""
    full_name = serializers.CharField(max_length=150)
    email = serializers.EmailField()

    def validate_full_name(self, value):
        if not value.strip():
            raise serializers.ValidationError('Full name is required.')
        return value.strip()

    def validate_email(self, value):
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('A user with this email already exists.')
        return value

    def create(self, validated_data):
        password = _generate_secure_password()
        user = User.objects.create_user(
            full_name=validated_data['full_name'],
            email=validated_data['email'],
            password=password,
            role='admin',
            is_staff=True,
            is_superuser=True,
            must_change_password=True,
        )
        _send_account_created_email(
            full_name=validated_data['full_name'],
            email=validated_data['email'],
            password=password,
        )
        return user


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Custom JWT serializer that adds role to the token and response."""

    # Override the default 'username' field — we log in with email
    username_field = User.USERNAME_FIELD

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token['role'] = user.role
        token['full_name'] = user.full_name
        token['email'] = user.email
        token['must_change_password'] = user.must_change_password
        return token

    def validate(self, attrs):
        from rest_framework.exceptions import AuthenticationFailed

        email = attrs.get(self.username_field, '')
        password = attrs.get('password', '')

        # Check for disabled account or security role before SimpleJWT swallows it
        try:
            user = User.objects.get(**{self.username_field: email})
            if user.check_password(password):
                if not user.is_active:
                    raise AuthenticationFailed(
                        'Your account has been disabled. Please contact the administrator.'
                    )
                if user.role == 'security':
                    raise AuthenticationFailed(
                        'Security personnel must log in at the guard gate login page.'
                    )
        except User.DoesNotExist:
            pass

        data = super().validate(attrs)
        data['role'] = self.user.role
        data['user_code'] = self.user.user_code
        data['must_change_password'] = self.user.must_change_password
        request = self.context.get('request')
        photo_url = (
            request.build_absolute_uri(self.user.photo.url)
            if request and self.user.photo else None
        )
        data['user'] = {
            'id': self.user.id,
            'user_code': self.user.user_code,
            'full_name': self.user.full_name,
            'email': self.user.email,
            'role': self.user.role,
            'must_change_password': self.user.must_change_password,
            'photo_url': photo_url,
            'gate_assignment': self.user.gate_assignment,
        }
        return data


class AuditLogSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source='actor.full_name', read_only=True)
    target_name = serializers.CharField(source='target_user.full_name', read_only=True)
    action_label = serializers.CharField(source='get_action_display', read_only=True)

    class Meta:
        model  = AuditLog
        fields = ['id', 'actor', 'actor_name', 'action', 'action_label', 'target_user', 'target_name', 'details', 'ip_address', 'created_at']
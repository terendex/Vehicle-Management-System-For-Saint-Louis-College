import re
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
    photo_url = serializers.SerializerMethodField()

    class Meta:
        model  = User
        fields = ['id', 'user_code', 'full_name', 'email', 'role', 'is_active', 'date_joined', 'must_change_password', 'photo_url', 'guard_qr_secret']

    def get_photo_url(self, obj):
        if not obj.photo:
            return None
        request = self.context.get('request')
        if request:
            return request.build_absolute_uri(obj.photo.url)
        return obj.photo.url


class UserUpdateSerializer(serializers.ModelSerializer):
    """For editing user details (no password change). Accepts optional photo upload."""
    photo = serializers.ImageField(required=False, allow_null=True)

    class Meta:
        model  = User
        fields = ['full_name', 'email', 'role', 'photo']

    def validate_email(self, value):
        user = self.instance
        if User.objects.exclude(pk=user.pk).filter(email=value).exists():
            raise serializers.ValidationError('A user with this email already exists.')
        return value


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)
    confirm_password = serializers.CharField(write_only=True)

    class Meta:
        model  = User
        fields = ['full_name', 'email', 'password', 'confirm_password', 'role']

    def validate(self, attrs):
        if attrs['password'] != attrs.pop('confirm_password'):
            raise serializers.ValidationError({'confirm_password': 'Passwords do not match.'})
        validate_password_strength(attrs['password'])
        return attrs

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)


class AdminReplaceSerializer(serializers.Serializer):
    """Create a new admin and delete the requesting admin."""
    full_name = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    confirm_password = serializers.CharField(write_only=True)

    def validate_email(self, value):
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError('A user with this email already exists.')
        return value

    def validate(self, attrs):
        if attrs['password'] != attrs.pop('confirm_password'):
            raise serializers.ValidationError({'confirm_password': 'Passwords do not match.'})
        validate_password_strength(attrs['password'])
        return attrs

    def create(self, validated_data):
        return User.objects.create_user(
            full_name=validated_data['full_name'],
            email=validated_data['email'],
            password=validated_data['password'],
            role='admin',
            is_staff=True,
            is_superuser=True,
        )


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

        # Check for disabled account before SimpleJWT swallows it into a generic error
        try:
            user = User.objects.get(**{self.username_field: email})
            if user.check_password(password) and not user.is_active:
                raise AuthenticationFailed(
                    'Your account has been disabled. Please contact the administrator.'
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
        }
        return data


class AuditLogSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source='actor.full_name', read_only=True)
    target_name = serializers.CharField(source='target_user.full_name', read_only=True)
    action_label = serializers.CharField(source='get_action_display', read_only=True)

    class Meta:
        model  = AuditLog
        fields = ['id', 'actor', 'actor_name', 'action', 'action_label', 'target_user', 'target_name', 'details', 'ip_address', 'created_at']
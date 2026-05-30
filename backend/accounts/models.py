from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models


class UserManager(BaseUserManager):
    """Custom manager that uses email instead of username."""

    def create_user(self, email, full_name, password=None, **extra_fields):
        if not email:
            raise ValueError('Email is required')
        if not full_name:
            raise ValueError('Full name is required')
        email = self.normalize_email(email)
        user = self.model(email=email, full_name=full_name, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, full_name, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('role', 'admin')
        return self.create_user(email, full_name, password, **extra_fields)


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN          = 'admin',          'Admin'
        SECURITY       = 'security',       'Security Personnel'
        VEHICLE_OWNER  = 'vehicle_owner',  'Registered Vehicle Owner'

    full_name = models.CharField(max_length=150)
    email = models.EmailField(unique=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.VEHICLE_OWNER)

    # Override username to be nullable/blank, email is used for login
    username = models.CharField(max_length=150, blank=True, null=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['full_name']  # email is already required via USERNAME_FIELD

    objects = UserManager()

    def __str__(self):
        return f"{self.full_name} ({self.role})"
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models


class UserManager(BaseUserManager):
    """Custom manager that uses full_name instead of username."""

    def create_user(self, full_name, password=None, **extra_fields):
        if not full_name:
            raise ValueError('Full name is required')
        extra_fields.setdefault('username', full_name)  # keep username in sync
        user = self.model(full_name=full_name, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, full_name, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('role', 'admin')
        return self.create_user(full_name, password, **extra_fields)


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN          = 'admin',          'Admin'
        SECURITY       = 'security',       'Security Personnel'
        VEHICLE_OWNER  = 'vehicle_owner',  'Registered Vehicle Owner'

    full_name = models.CharField(max_length=150, unique=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.VEHICLE_OWNER)

    # Override username to remove its unique constraint — full_name is now the login field
    username = models.CharField(max_length=150, blank=True, default='')

    USERNAME_FIELD = 'full_name'
    REQUIRED_FIELDS = []  # full_name is already required via USERNAME_FIELD

    objects = UserManager()

    def save(self, *args, **kwargs):
        # Keep username in sync with full_name for compatibility
        if not self.username:
            self.username = self.full_name
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.full_name} ({self.role})"
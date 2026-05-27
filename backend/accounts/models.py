from django.contrib.auth.models import AbstractUser
from django.db import models

class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN          = 'admin',          'Admin'
        SECURITY       = 'security',       'Security Personnel'
        VEHICLE_OWNER  = 'vehicle_owner',  'Registered Vehicle Owner'

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.VEHICLE_OWNER)

    def __str__(self):
        return f"{self.username} ({self.role})"
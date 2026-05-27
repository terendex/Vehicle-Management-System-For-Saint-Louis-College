from django.contrib.auth.models import AbstractUser
from django.db import models

class User(AbstractUser):
    class Role(models.TextChoices):
        GUARD      = 'guard',      'Guard'
        SUPERVISOR = 'supervisor', 'Supervisor'
        ADMIN      = 'admin',      'Admin'

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.GUARD)

    def __str__(self):
        return f"{self.username} ({self.role})"
from django.db import models

class Owner(models.Model):
    class OwnerType(models.TextChoices):
        STUDENT  = 'student',  'Student'
        FETCHER  = 'fetcher',  'Fetcher/Dropper'
        EMPLOYEE = 'employee', 'Employee'
        VISITOR  = 'visitor',  'Visitor'

    class Schedule(models.TextChoices):
        MWF  = 'MWF',  'Monday-Wednesday-Friday'
        TTHS = 'TTHS', 'Tuesday-Thursday-Saturday'
        ANY  = 'ANY',  'Any Day'               # for employees

    full_name   = models.CharField(max_length=255)
    contact     = models.CharField(max_length=50, blank=True)
    address     = models.TextField(blank=True)
    photo       = models.ImageField(upload_to='owners/', blank=True)
    owner_type  = models.CharField(max_length=20, choices=OwnerType.choices, default=OwnerType.STUDENT)
    schedule    = models.CharField(max_length=10, choices=Schedule.choices, default=Schedule.MWF)
    created_at  = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.full_name} ({self.owner_type})"


class Vehicle(models.Model):
    class Type(models.TextChoices):
        CAR        = 'car',        'Car'
        MOTORCYCLE = 'motorcycle', 'Motorcycle'
        TRUCK      = 'truck',      'Truck'
        VAN        = 'van',        'Van'
        BUS        = 'bus',        'Bus'

    plate_number  = models.CharField(max_length=20, unique=True, db_index=True)
    vehicle_type  = models.CharField(max_length=20, choices=Type.choices, default=Type.CAR)
    model         = models.CharField(max_length=100, blank=True)
    color         = models.CharField(max_length=50, blank=True)
    is_authorized = models.BooleanField(default=False)
    owner         = models.ForeignKey(Owner, on_delete=models.SET_NULL, null=True, related_name='vehicles')
    created_at    = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.plate_number
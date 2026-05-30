from django.contrib import admin
from .models import Owner, Vehicle, RegistrationToken, VehicleRegistration

admin.site.register(Owner)
admin.site.register(Vehicle)

@admin.register(RegistrationToken)
class RegistrationTokenAdmin(admin.ModelAdmin):
    list_display = ('token', 'registrant_type', 'is_active', 'is_used', 'expires_at')
    list_filter = ('registrant_type', 'is_active', 'is_used')

@admin.register(VehicleRegistration)
class VehicleRegistrationAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'email', 'registrant_type', 'plate_number', 'status', 'created_at')
    list_filter = ('status', 'registrant_type')
    search_fields = ('full_name', 'email', 'plate_number')
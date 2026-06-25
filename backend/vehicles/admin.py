from django.contrib import admin
from .models import Vehicle, VehicleRegistration, ReferenceItem

admin.site.register(Vehicle)


@admin.register(VehicleRegistration)
class VehicleRegistrationAdmin(admin.ModelAdmin):
    list_display   = ('full_name', 'email', 'registrant_type', 'plate_number', 'status', 'source', 'created_at')
    list_filter    = ('status', 'registrant_type', 'source')
    search_fields  = ('full_name', 'email', 'plate_number')


@admin.register(ReferenceItem)
class ReferenceItemAdmin(admin.ModelAdmin):
    list_display   = ('name', 'category', 'is_active', 'order')
    list_filter    = ('category', 'is_active')
    list_editable  = ('is_active', 'order')
    search_fields  = ('name',)

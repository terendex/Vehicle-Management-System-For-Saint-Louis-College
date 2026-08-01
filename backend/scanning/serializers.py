from rest_framework import serializers
from .models import VisitorPass, Office, AccessLog, MLTrainingSample, GuardShift

class OfficeSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Office
        fields = '__all__'

class VisitorPassSerializer(serializers.ModelSerializer):
    office_name    = serializers.CharField(source='office.name',         read_only=True, default=None)
    issued_by_name = serializers.CharField(source='issued_by.full_name', read_only=True, default=None)
    qr_payload     = serializers.CharField(read_only=True)

    class Meta:
        model  = VisitorPass
        fields = '__all__'

class AccessLogSerializer(serializers.ModelSerializer):
    scanned_by_name    = serializers.CharField(source='scanned_by.full_name',     read_only=True, default=None)
    on_duty_guard_name = serializers.CharField(source='on_duty_guard.full_name',  read_only=True, default=None)
    vehicle_owner_name = serializers.CharField(source='vehicle.user.full_name',   read_only=True, default=None)
    vehicle_type_info  = serializers.CharField(source='vehicle.vehicle_type',     read_only=True, default=None)
    # Entrant classification for Entry Management filtering: the owner's type
    # (student/employee/fetcher/visitor), 'supplier' for a registered supplier
    # plate, else 'unknown' (walk-in visitor / unregistered).
    classification     = serializers.SerializerMethodField()

    def _supplier_plates(self):
        # DRF reuses this child serializer across the whole list, so cache the
        # active supplier plates on the instance — one query, not one per row.
        if not hasattr(self, '_supplier_plate_cache'):
            from vehicles.models import SupplierPlate
            self._supplier_plate_cache = {
                (p or '').strip().upper()
                for p in SupplierPlate.objects.filter(supplier__is_active=True)
                                              .values_list('plate_number', flat=True)
            }
        return self._supplier_plate_cache

    def get_classification(self, obj):
        vehicle = getattr(obj, 'vehicle', None)
        owner = getattr(vehicle, 'user', None) if vehicle else None
        if owner and owner.owner_type:
            return owner.owner_type
        plate = getattr(vehicle, 'plate_number', '') if vehicle else ''
        if plate and (plate or '').strip().upper() in self._supplier_plates():
            return 'supplier'
        return 'unknown'

    class Meta:
        model  = AccessLog
        fields = '__all__'

class GuardShiftSerializer(serializers.ModelSerializer):
    guard_name       = serializers.CharField(source='guard.full_name',         read_only=True)
    guard_code       = serializers.CharField(source='guard.user_code',         read_only=True)
    clocked_out_by_name = serializers.CharField(source='clocked_out_by.full_name', read_only=True, default=None)
    is_active = serializers.SerializerMethodField()

    def get_is_active(self, obj):
        return obj.clocked_out_at is None

    class Meta:
        model  = GuardShift
        fields = '__all__'


class MLTrainingSampleSerializer(serializers.ModelSerializer):
    class Meta:
        model  = MLTrainingSample
        fields = '__all__'
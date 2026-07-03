from rest_framework import serializers
from .models import Vehicle, VehicleRegistration, RuleConstraint, ParkingSpace, ParkingZone, ReferenceItem, Camera, ParkingNotice, Supplier, SupplierPlate
from accounts.models import User


class UserProfileSerializer(serializers.ModelSerializer):
    """Exposes owner-profile fields from User — used as embedded object in VehicleSerializer."""
    class Meta:
        model  = User
        fields = ['id', 'full_name', 'email', 'user_code', 'owner_type', 'schedule', 'contact', 'address', 'photo']


class VehicleSerializer(serializers.ModelSerializer):
    user    = UserProfileSerializer(read_only=True)
    user_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), source='user', write_only=True, required=False, allow_null=True
    )

    class Meta:
        model  = Vehicle
        fields = '__all__'


class VehicleRegistrationSerializer(serializers.ModelSerializer):
    registration_block_count = serializers.SerializerMethodField()

    class Meta:
        model = VehicleRegistration
        fields = '__all__'

    def get_registration_block_count(self, instance):
        """Number of prior violations that flag this plate for additional review."""
        from violations.models import Violation
        return Violation.registration_block_for_plate(instance.plate_number).count()

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['department_name'] = instance.department.name if instance.department else ''
        return data


class RuleConstraintSerializer(serializers.ModelSerializer):
    class Meta:
        model = RuleConstraint
        fields = '__all__'


class ReferenceItemSerializer(serializers.ModelSerializer):
    class Meta:
        model  = ReferenceItem
        fields = '__all__'


class ParkingSpaceSerializer(serializers.ModelSerializer):
    vehicle_category = serializers.SerializerMethodField()

    class Meta:
        model  = ParkingSpace
        fields = ['id', 'zone', 'space_number', 'vehicle_category',
                  'x1', 'y1', 'x2', 'y2', 'is_occupied', 'occupied_by', 'updated_at']

    def get_vehicle_category(self, obj):
        return obj.zone.vehicle_category if obj.zone else None


class ParkingNoticeSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source='created_by.full_name', read_only=True)

    class Meta:
        model  = ParkingNotice
        fields = ['id', 'title', 'body', 'is_active', 'created_by', 'created_by_name', 'created_at']
        read_only_fields = ['created_by', 'created_at']


class ParkingZoneSerializer(serializers.ModelSerializer):
    spaces              = ParkingSpaceSerializer(many=True, read_only=True)
    reference_image_url = serializers.SerializerMethodField()
    space_count         = serializers.SerializerMethodField()
    total_capacity      = serializers.SerializerMethodField()
    occupied_count      = serializers.SerializerMethodField()
    is_full             = serializers.SerializerMethodField()

    class Meta:
        model  = ParkingZone
        fields = ['id', 'name', 'vehicle_category', 'rtsp_url', 'reference_image',
                  'reference_image_url', 'capacity_override', 'space_count',
                  'total_capacity', 'occupied_count', 'is_full', 'created_at', 'spaces']

    def get_reference_image_url(self, obj):
        if not obj.reference_image:
            return None
        request = self.context.get('request')
        if request:
            return request.build_absolute_uri(obj.reference_image.url)
        return obj.reference_image.url

    def get_space_count(self, obj):
        return obj.spaces.count()

    def get_total_capacity(self, obj):
        if obj.capacity_override is not None:
            return obj.capacity_override
        return obj.spaces.count()

    def get_occupied_count(self, obj):
        return obj.spaces.filter(is_occupied=True).count()

    def get_is_full(self, obj):
        cap = obj.capacity_override if obj.capacity_override is not None else obj.spaces.count()
        occ = obj.spaces.filter(is_occupied=True).count()
        return cap > 0 and occ >= cap


class CameraSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Camera
        fields = ['id', 'cam_number', 'name', 'ip', 'device_id', 'password',
                  'rtsp_url', 'assignment', 'gate_id', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'cam_number', 'name', 'created_at', 'updated_at']

    def validate(self, attrs):
        ip        = attrs.get('ip', getattr(self.instance, 'ip', None))
        device_id = attrs.get('device_id', getattr(self.instance, 'device_id', None))
        qs        = Camera.objects.all()
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if ip and qs.filter(ip=ip).exists():
            raise serializers.ValidationError({'ip': 'A camera with this IP address is already registered.'})
        if device_id and qs.filter(device_id=device_id).exists():
            raise serializers.ValidationError({'device_id': 'A camera with this Device ID is already registered.'})
        return attrs


class SupplierPlateSerializer(serializers.ModelSerializer):
    class Meta:
        model  = SupplierPlate
        fields = ['id', 'plate_number', 'created_at']
        read_only_fields = ['id', 'created_at']


class SupplierSerializer(serializers.ModelSerializer):
    plates      = SupplierPlateSerializer(many=True, read_only=True)
    plate_count = serializers.IntegerField(source='plates.count', read_only=True)

    class Meta:
        model  = Supplier
        fields = ['id', 'company_name', 'is_active', 'plate_count', 'plates', 'created_at', 'updated_at']
        read_only_fields = ['id', 'plate_count', 'created_at', 'updated_at']

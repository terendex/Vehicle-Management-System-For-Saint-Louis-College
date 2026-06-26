from rest_framework import serializers
from .models import Vehicle, VehicleRegistration, RuleConstraint, ParkingSpace, ParkingZone, ReferenceItem, Camera
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
    class Meta:
        model = VehicleRegistration
        fields = '__all__'

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


class ParkingZoneSerializer(serializers.ModelSerializer):
    spaces              = ParkingSpaceSerializer(many=True, read_only=True)
    reference_image_url = serializers.SerializerMethodField()

    class Meta:
        model  = ParkingZone
        fields = ['id', 'name', 'vehicle_category', 'rtsp_url', 'reference_image',
                  'reference_image_url', 'created_at', 'spaces']

    def get_reference_image_url(self, obj):
        if not obj.reference_image:
            return None
        request = self.context.get('request')
        if request:
            return request.build_absolute_uri(obj.reference_image.url)
        return obj.reference_image.url


class CameraSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Camera
        fields = ['id', 'cam_number', 'name', 'ip', 'device_id', 'password',
                  'rtsp_url', 'assignment', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'cam_number', 'name', 'created_at', 'updated_at']

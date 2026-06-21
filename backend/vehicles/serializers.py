from rest_framework import serializers
from .models import Owner, Vehicle, RegistrationToken, VehicleRegistration, RuleConstraint, VehicleTypeAccess, ParkingSpace, ParkingZone

class OwnerSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Owner
        fields = '__all__'

class VehicleSerializer(serializers.ModelSerializer):
    owner = OwnerSerializer(read_only=True)
    owner_id = serializers.PrimaryKeyRelatedField(
        queryset=Owner.objects.all(), source='owner', write_only=True
    )

    class Meta:
        model  = Vehicle
        fields = '__all__'

class RegistrationTokenSerializer(serializers.ModelSerializer):
    is_valid = serializers.ReadOnlyField()
    class Meta:
        model = RegistrationToken
        fields = '__all__'

class VehicleRegistrationSerializer(serializers.ModelSerializer):
    class Meta:
        model = VehicleRegistration
        fields = '__all__'

class RuleConstraintSerializer(serializers.ModelSerializer):
    class Meta:
        model = RuleConstraint
        fields = '__all__'

class VehicleTypeAccessSerializer(serializers.ModelSerializer):
    hours_display = serializers.SerializerMethodField()

    class Meta:
        model = VehicleTypeAccess
        fields = '__all__'

    def get_hours_display(self, obj):
        if obj.is_all_hours:
            return 'All hours'
        return f"{obj.hours_start}–{obj.hours_end}"


class ParkingSpaceSerializer(serializers.ModelSerializer):
    class Meta:
        model  = ParkingSpace
        fields = '__all__'


class ParkingZoneSerializer(serializers.ModelSerializer):
    spaces              = ParkingSpaceSerializer(many=True, read_only=True)
    reference_image_url = serializers.SerializerMethodField()

    class Meta:
        model  = ParkingZone
        fields = ['id', 'name', 'vehicle_category', 'reference_image', 'reference_image_url', 'created_at', 'spaces']

    def get_reference_image_url(self, obj):
        if not obj.reference_image:
            return None
        request = self.context.get('request')
        if request:
            return request.build_absolute_uri(obj.reference_image.url)
        return obj.reference_image.url
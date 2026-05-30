from rest_framework import serializers
from .models import Owner, Vehicle, RegistrationToken, VehicleRegistration

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
    class Meta:
        model = RegistrationToken
        fields = '__all__'

class VehicleRegistrationSerializer(serializers.ModelSerializer):
    class Meta:
        model = VehicleRegistration
        fields = '__all__'
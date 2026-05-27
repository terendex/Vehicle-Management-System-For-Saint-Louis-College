from rest_framework import serializers
from .models import Owner, Vehicle

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
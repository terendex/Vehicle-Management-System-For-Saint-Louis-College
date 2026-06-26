from rest_framework import serializers
from .models import Violation

class ViolationSerializer(serializers.ModelSerializer):
    plate_number = serializers.CharField(source='vehicle.plate_number', read_only=True)
    owner_name   = serializers.CharField(source='vehicle.user.full_name', read_only=True, default='')

    class Meta:
        model  = Violation
        fields = '__all__'
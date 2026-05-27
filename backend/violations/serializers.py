from rest_framework import serializers
from .models import Violation

class ViolationSerializer(serializers.ModelSerializer):
    plate_number = serializers.CharField(source='vehicle.plate_number', read_only=True)
    class Meta:
        model  = Violation
        fields = '__all__'
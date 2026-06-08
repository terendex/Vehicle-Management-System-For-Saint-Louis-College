from rest_framework import serializers
from .models import VisitorPass, Office, AccessLog

class OfficeSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Office
        fields = '__all__'

class VisitorPassSerializer(serializers.ModelSerializer):
    office_name  = serializers.CharField(source='office.name', read_only=True)
    plate_number = serializers.CharField(source='vehicle.plate_number', read_only=True)
    class Meta:
        model  = VisitorPass
        fields = '__all__'

class AccessLogSerializer(serializers.ModelSerializer):
    scanned_by_name = serializers.CharField(source='scanned_by.full_name', read_only=True)
    class Meta:
        model  = AccessLog
        fields = '__all__'
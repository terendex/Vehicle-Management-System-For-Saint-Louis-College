from rest_framework import serializers
from .models import VisitorPass, Office, AccessLog, MLTrainingSample

class OfficeSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Office
        fields = '__all__'

class VisitorPassSerializer(serializers.ModelSerializer):
    office_name    = serializers.CharField(source='office.name',         read_only=True, default=None)
    issued_by_name = serializers.CharField(source='issued_by.full_name', read_only=True, default=None)

    class Meta:
        model  = VisitorPass
        fields = '__all__'

class AccessLogSerializer(serializers.ModelSerializer):
    scanned_by_name    = serializers.CharField(source='scanned_by.full_name',     read_only=True, default=None)
    vehicle_owner_name = serializers.CharField(source='vehicle.user.full_name',   read_only=True, default=None)
    vehicle_type_info  = serializers.CharField(source='vehicle.vehicle_type',     read_only=True, default=None)

    class Meta:
        model  = AccessLog
        fields = '__all__'

class MLTrainingSampleSerializer(serializers.ModelSerializer):
    class Meta:
        model  = MLTrainingSample
        fields = '__all__'
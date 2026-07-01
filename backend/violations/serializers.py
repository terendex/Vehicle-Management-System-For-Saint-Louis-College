from rest_framework import serializers
from .models import Violation

class ViolationSerializer(serializers.ModelSerializer):
    plate_number    = serializers.CharField(source='vehicle.plate_number', read_only=True)
    owner_name      = serializers.CharField(source='vehicle.user.full_name', read_only=True, default='')
    owner_email     = serializers.CharField(source='vehicle.user.email', read_only=True, default='')
    issued_by_name  = serializers.CharField(source='issued_by.full_name', read_only=True, default='')
    issued_by_code  = serializers.CharField(source='issued_by.user_code', read_only=True, default='')
    evidence_url    = serializers.SerializerMethodField()

    class Meta:
        model  = Violation
        fields = '__all__'

    def get_evidence_url(self, obj):
        if not obj.evidence:
            return None
        request = self.context.get('request')
        if request:
            return request.build_absolute_uri(obj.evidence.url)
        return obj.evidence.url

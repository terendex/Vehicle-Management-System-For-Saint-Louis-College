from rest_framework import serializers
from .models import Violation

class ViolationSerializer(serializers.ModelSerializer):
    # Read the identity snapshot taken when the violation was issued, falling
    # back to the live vehicle only for rows that predate it.
    #
    # These used to resolve through `vehicle.user.full_name`, a live FK chain.
    # Archiving an owner clears vehicle.user, so a perfectly valid violation
    # lost its owner name; deleting one removed the vehicle and the violation
    # with it. Stored values cannot be undone by either.
    plate_number    = serializers.SerializerMethodField()
    owner_name      = serializers.SerializerMethodField()
    owner_email     = serializers.SerializerMethodField()
    issued_by_name  = serializers.CharField(source='issued_by.full_name', read_only=True, default='')
    issued_by_code  = serializers.CharField(source='issued_by.user_code', read_only=True, default='')
    on_duty_guard_name = serializers.CharField(source='on_duty_guard.full_name', read_only=True, default='')
    evidence_url    = serializers.SerializerMethodField()

    class Meta:
        model  = Violation
        fields = '__all__'
        # The view resolves the vehicle from plate_number when no id is given.
        #
        # The identity snapshot is server-set at issue time and never accepted
        # from a request: `fields = '__all__'` would otherwise let a client POST
        # any owner_name it liked onto a disciplinary record.
        extra_kwargs = {
            'vehicle':           {'required': False},
            'conduction_number': {'read_only': True},
            'owner_name':        {'read_only': True},
            'owner_email':       {'read_only': True},
        }

    def get_plate_number(self, obj):
        return obj.identifier

    def get_owner_name(self, obj):
        if obj.owner_name:
            return obj.owner_name
        owner = obj.vehicle.user if obj.vehicle_id else None
        return owner.full_name if owner else ''

    def get_owner_email(self, obj):
        if obj.owner_email:
            return obj.owner_email
        owner = obj.vehicle.user if obj.vehicle_id else None
        return owner.email if owner else ''

    def get_evidence_url(self, obj):
        if not obj.evidence:
            return None
        request = self.context.get('request')
        if request:
            return request.build_absolute_uri(obj.evidence.url)
        return obj.evidence.url

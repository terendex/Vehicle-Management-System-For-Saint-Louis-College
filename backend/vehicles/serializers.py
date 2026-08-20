from rest_framework import serializers
from .models import Vehicle, VehicleRegistration, RuleConstraint, ParkingSpace, ParkingZone, ReferenceItem, Camera, ParkingNotice, Supplier, SupplierPlate, ScheduledVisit
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
    registration_block_count = serializers.SerializerMethodField()

    class Meta:
        model = VehicleRegistration
        fields = '__all__'

    @staticmethod
    def build_block_counts(registrations):
        """Count blocking violations for a whole page of registrations at once.

        Serialising a list otherwise costs one COUNT query per row, so the round
        trips grow with the result size. Pass the returned map in as the
        'block_counts' serializer context and the whole page costs one query
        regardless of how many rows it holds.
        """
        from django.db.models import Count
        from violations.models import Violation

        plates = {(r.plate_number or '').strip().upper() for r in registrations}
        plates.discard('')
        if not plates:
            return {}

        rows = (Violation.objects
                .filter(vehicle__plate_number__in=plates, registration_blocked=True)
                .values('vehicle__plate_number')
                .annotate(n=Count('id')))
        return {row['vehicle__plate_number']: row['n'] for row in rows}

    def get_registration_block_count(self, instance):
        """Number of prior violations that flag this plate for additional review."""
        plate = (instance.plate_number or '').strip().upper()

        # Prefer the batch-built map when the caller supplied one. A plate that
        # is absent from the map simply has no blocking violations.
        block_counts = self.context.get('block_counts')
        if block_counts is not None:
            return block_counts.get(plate, 0)

        # Single-object callers keep the direct query.
        from violations.models import Violation
        return Violation.registration_block_for_plate(instance.plate_number).count()

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # instance.department is a FK: without select_related('department') on
        # the queryset this line is a separate SELECT for every row.
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
                  'x1', 'y1', 'x2', 'y2', 'points', 'is_occupied', 'occupied_by', 'updated_at']

    def get_vehicle_category(self, obj):
        return obj.zone.vehicle_category if obj.zone else None


class ParkingNoticeSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source='created_by.full_name', read_only=True)

    class Meta:
        model  = ParkingNotice
        fields = ['id', 'title', 'body', 'is_active', 'created_by', 'created_by_name', 'created_at']
        read_only_fields = ['created_by', 'created_at']


class ParkingZoneSerializer(serializers.ModelSerializer):
    spaces              = ParkingSpaceSerializer(many=True, read_only=True)
    reference_image_url = serializers.SerializerMethodField()
    space_count         = serializers.SerializerMethodField()
    total_capacity      = serializers.SerializerMethodField()
    occupied_count      = serializers.SerializerMethodField()
    is_full             = serializers.SerializerMethodField()
    camera_name         = serializers.SerializerMethodField()

    class Meta:
        model  = ParkingZone
        fields = ['id', 'name', 'vehicle_category', 'camera', 'camera_name', 'reference_image',
                  'reference_image_url', 'capacity_override', 'space_count',
                  'total_capacity', 'occupied_count', 'is_full', 'created_at', 'spaces']

    def validate_camera(self, camera):
        if camera is not None and camera.assignment != Camera.Assignment.PARKING:
            raise serializers.ValidationError(
                'Only cameras assigned to Parking in Device Management can be linked to a zone.')
        return camera

    def get_camera_name(self, obj):
        return obj.camera.name if obj.camera else None

    def get_reference_image_url(self, obj):
        if not obj.reference_image:
            return None
        request = self.context.get('request')
        if request:
            return request.build_absolute_uri(obj.reference_image.url)
        return obj.reference_image.url

    def get_space_count(self, obj):
        return obj.spaces.count()

    def get_total_capacity(self, obj):
        if obj.capacity_override is not None:
            return obj.capacity_override
        return obj.spaces.count()

    def get_occupied_count(self, obj):
        return obj.spaces.filter(is_occupied=True).count()

    def get_is_full(self, obj):
        cap = obj.capacity_override if obj.capacity_override is not None else obj.spaces.count()
        occ = obj.spaces.filter(is_occupied=True).count()
        return cap > 0 and occ >= cap


class CameraSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Camera
        fields = ['id', 'cam_number', 'name', 'ip', 'device_id', 'password',
                  'rtsp_url', 'assignment', 'gate_id', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'cam_number', 'name', 'created_at', 'updated_at']

    def validate(self, attrs):
        # Normalize connection fields
        for f in ('ip', 'device_id', 'rtsp_url'):
            if f in attrs and attrs[f]:
                attrs[f] = attrs[f].strip()

        ip         = attrs.get('ip',        getattr(self.instance, 'ip', '')) or ''
        device_id  = attrs.get('device_id', getattr(self.instance, 'device_id', '')) or ''
        rtsp_url   = attrs.get('rtsp_url',  getattr(self.instance, 'rtsp_url', '')) or ''
        assignment = attrs.get('assignment', getattr(self.instance, 'assignment', '')) or ''
        gate_id    = attrs.get('gate_id',    getattr(self.instance, 'gate_id', None))

        # One row per *stream*, not per IP. A single address can host several
        # cameras — an NVR, or a multi-lens unit — distinguished only by the
        # channel number inside the RTSP path. Rejecting a repeated IP made
        # those impossible to register at all.
        #
        # The stream URL is what actually identifies a camera, and it still has
        # to be unique: that is what stops the same view being added twice.
        others = Camera.objects.all()
        if self.instance:
            others = others.exclude(pk=self.instance.pk)
        if rtsp_url and others.filter(rtsp_url__iexact=rtsp_url).exists():
            raise serializers.ValidationError(
                {'rtsp_url': 'A camera with this stream URL already exists. '
                             'For a second camera on the same device, use its '
                             'channel number.'})

        # Entry cameras must know which gate they cover (drives log tagging)
        if assignment == 'entry' and not gate_id:
            raise serializers.ValidationError(
                {'gate_id': 'Entry cameras must be assigned to a gate.'})
        return attrs


class SupplierPlateSerializer(serializers.ModelSerializer):
    class Meta:
        model  = SupplierPlate
        fields = ['id', 'plate_number', 'created_at']
        read_only_fields = ['id', 'created_at']


class SupplierSerializer(serializers.ModelSerializer):
    plates      = SupplierPlateSerializer(many=True, read_only=True)
    plate_count = serializers.IntegerField(source='plates.count', read_only=True)

    class Meta:
        model  = Supplier
        fields = ['id', 'company_name', 'category', 'is_active', 'plate_count', 'plates', 'created_at', 'updated_at']
        read_only_fields = ['id', 'plate_count', 'created_at', 'updated_at']


class ScheduledVisitSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source='supplier.company_name', read_only=True, default=None)

    class Meta:
        model  = ScheduledVisit
        fields = [
            'id', 'visitor_name', 'category', 'supplier', 'supplier_name',
            'plate_number', 'purpose', 'expected_date', 'notes', 'is_arrived', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']

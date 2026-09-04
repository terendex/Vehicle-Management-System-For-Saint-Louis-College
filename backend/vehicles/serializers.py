from rest_framework import serializers
from .models import Vehicle, VehicleRegistration, RuleConstraint, ParkingSpace, ParkingZone, ReferenceItem, Camera, ParkingNotice, Supplier, SupplierPlate, ScheduledVisit
from .document_urls import signed_document_url
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

    # The applicant's uploads. What is stored is an object key; what a browser
    # needs is a URL it can fetch, and for the reasons in document_urls that is
    # not the bucket's public path. Swapped on the way out in to_representation
    # rather than declared as fields here, so read_only_fields below stays the
    # single list of what the review process owns.
    DOCUMENT_FIELDS = ('drivers_license_image', 'assessment_form', 'or_receipt_image')

    # TEMPORARY — Data Privacy Office trial. The columns still exist (the schema
    # is shared with the other branches and must not move), and rows filed before
    # the trial still hold values in them, but nothing this API serves may carry
    # them: the reviewer screens, the owner portal and the vehicle profile a
    # guard can open all read this serializer. Dropped on the way out rather than
    # excluded from `fields`, so `fields = '__all__'` stays the one list and a
    # revert is one block of code.
    WITHHELD_FIELDS = ('address', 'contact_number', 'age',
                       'student_id', 'employee_id', 'driver_contact')

    class Meta:
        model = VehicleRegistration
        fields = '__all__'
        # Everything the *review process* owns, not the applicant.
        #
        # This serializer is fed straight from request.data by the public,
        # AllowAny registration endpoint, and `fields = '__all__'` made every
        # one of these writable from that payload. A submission could therefore
        # carry `status: "accepted"` and approve itself without CDSO ever seeing
        # it; set `user`/`vehicle` to point at somebody else's account; grant
        # itself `is_special_case`; invent an `or_number`; or claim a
        # `system_student_id` (a unique column) that the next genuine approval
        # then collides with. The views supply all of these themselves via
        # serializer.save(**kwargs), which bypasses read_only_fields, so
        # nothing legitimate changes here.
        read_only_fields = (
            'user', 'vehicle',
            'registrant_type', 'status', 'source',
            'or_number', 'reviewed_at', 'rejection_reason',
            'system_student_id', 'system_employee_id',
            'is_special_case', 'special_case_reason',
            'drivers_license_image',   # set only by UploadRegistrationDocumentsView
            'assessment_form',         # set only by UploadRegistrationDocumentsView
            # Payment is recorded by the applicant's receipt upload and by the
            # accept flow — never by the submission payload, which would let an
            # application mark itself paid and skip the Accounting Office.
            'payment_status', 'or_receipt_image', 'amount_paid', 'paid_at',
            'payment_token', 'unpaid_accept_reason',
            'created_at',
        )

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

        # Matches the plate snapshot on the violation, not the FK to Vehicle:
        # a blocking violation has to keep blocking after the owner's account
        # (and with it their vehicle row) is gone, which is the whole point of
        # a registration hold.
        rows = (Violation.objects
                .filter(plate_number__in=plates, registration_blocked=True)
                .values('plate_number')
                .annotate(n=Count('id')))
        return {row['plate_number']: row['n'] for row in rows}

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
        # TEMPORARY (DPO trial): what is stored is an object key, and what the
        # reviewer's browser used to get back was a signed URL for it (see
        # document_urls). Nothing is uploaded any more, so every slot is reported
        # empty instead — a legacy row's file is not handed out either.
        for name in self.DOCUMENT_FIELDS:
            data[name] = None
        for name in self.WITHHELD_FIELDS:
            data.pop(name, None)
        # A fetcher's students used to each carry their own enrolment proof,
        # folded in here from FetcherStudentAssessment so the reviewer read it
        # against the right child.
        # TEMPORARY (DPO trial): a fetched student is identified by name and
        # level only — no ID number, and no enrolment proof to pair with them.
        students = data.get('fetcher_students')
        if isinstance(students, list) and students:
            data['fetcher_students'] = [
                {k: v for k, v in entry.items() if k != 'student_id'}
                if isinstance(entry, dict) else entry
                for entry in students
            ]
        # payment_token is the secret in the applicant's receipt-upload link.
        # This serializer feeds the CDSO queue and the vehicle profile a guard
        # can open, so anyone with either view could otherwise read a stranger's
        # token and file a receipt against their registration. The pending email
        # is the only place the token is ever meant to appear.
        data.pop('payment_token', None)
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
                  'x1', 'y1', 'x2', 'y2', 'points', 'lens_index',
                  'is_occupied', 'occupied_by', 'updated_at']

    def get_vehicle_category(self, obj):
        return obj.zone.vehicle_category if obj.zone else None


class ParkingNoticeSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source='created_by.full_name', read_only=True)

    class Meta:
        model  = ParkingNotice
        fields = ['id', 'title', 'body', 'is_active', 'created_by', 'created_by_name', 'created_at']
        read_only_fields = ['created_by', 'created_at']


class ParkingZoneSerializer(serializers.ModelSerializer):
    """A zone's map plus the capacity picture its category sits in.

    Two granularities on purpose, because they come from two different sources:

      * **Zone/bay fields** (`space_count`, `bays_occupied`, `total_capacity`)
        describe this zone's drawn map and what the camera sees in it.
      * **Category fields** (`category_*`) describe capacity and live occupancy
        for every zone of this vehicle category, counted from the gate ledger.
        The ledger knows how many cars are on campus, not which car zone each
        one chose, so a per-zone occupancy would be a fabricated number.

    `is_full` deliberately reports the *category* answer — it is the one that
    decides whether another car can be let in.
    """
    spaces              = ParkingSpaceSerializer(many=True, read_only=True)
    reference_image_url = serializers.SerializerMethodField()
    space_count         = serializers.SerializerMethodField()
    total_capacity      = serializers.SerializerMethodField()
    occupied_count      = serializers.SerializerMethodField()
    bays_occupied       = serializers.SerializerMethodField()
    is_full             = serializers.SerializerMethodField()
    camera_name         = serializers.SerializerMethodField()
    category_capacity   = serializers.SerializerMethodField()
    category_occupied   = serializers.SerializerMethodField()
    category_available  = serializers.SerializerMethodField()
    category_is_full    = serializers.SerializerMethodField()
    category_fill_pct   = serializers.SerializerMethodField()
    occupancy_source    = serializers.SerializerMethodField()
    baseline_image_url  = serializers.SerializerMethodField()
    has_baseline        = serializers.SerializerMethodField()

    class Meta:
        model  = ParkingZone
        fields = ['id', 'name', 'vehicle_category', 'lens_index', 'camera', 'camera_name', 'reference_image',
                  'reference_image_url', 'capacity_override', 'space_count',
                  'total_capacity', 'occupied_count', 'bays_occupied', 'is_full',
                  'category_capacity', 'category_occupied', 'category_available',
                  'category_is_full', 'category_fill_pct', 'occupancy_source',
                  'occupancy_method', 'detection_enabled',
                  'baseline_image_url', 'baseline_captured_at',
                  'has_baseline', 'created_at', 'spaces']
        read_only_fields = ['baseline_captured_at']

    # ── Category state (gate ledger) ──────────────────────────────────────────

    def _state(self):
        """Category capacity/occupancy, fetched at most once per serialization.

        Prefers the map the view built for the whole page (`category_state` in
        context), matching the `build_block_counts` convention used above: a
        list of N zones then costs the same two queries as a single one. Falls
        back to computing it once and caching on the serializer instance, so
        even a caller that forgets the context cannot turn this into N+1.
        """
        state = self.context.get('category_state')
        if state is None:
            state = getattr(self, '_state_cache', None)
            if state is None:
                from .capacity import category_state
                state = category_state()
                self._state_cache = state
        return state

    def _category(self, obj):
        return self._state().get(obj.vehicle_category) or {}

    def get_category_capacity(self, obj):
        return self._category(obj).get('capacity', 0)

    def get_category_occupied(self, obj):
        return self._category(obj).get('occupied', 0)

    def get_category_available(self, obj):
        return self._category(obj).get('available', 0)

    def get_category_is_full(self, obj):
        return self._category(obj).get('is_full', False)

    def get_category_fill_pct(self, obj):
        return self._category(obj).get('fill_pct', 0)

    def get_is_full(self, obj):
        # The question a guard is really asking: can another one of these come
        # in? That is a category answer, from the gate ledger.
        return self._category(obj).get('is_full', False)

    def get_occupancy_source(self, obj):
        return 'gate_ledger'

    # ── Classic-scorer baseline ───────────────────────────────────────────────

    def get_has_baseline(self, obj):
        """Whether this zone can actually be scored classically. A zone set to
        'classic' without one keeps running on the detector, so the UI needs to
        say which is really in effect rather than which was selected."""
        return bool(obj.baseline_image)

    def get_baseline_image_url(self, obj):
        if not obj.baseline_image:
            return None
        request = self.context.get('request')
        if request:
            return request.build_absolute_uri(obj.baseline_image.url)
        return obj.baseline_image.url

    def validate_camera(self, camera):
        if camera is not None and camera.assignment != Camera.Assignment.PARKING:
            raise serializers.ValidationError(
                'Only cameras assigned to Parking in Device Management can be linked to a zone.')
        return camera

    def get_camera_name(self, obj):
        return obj.camera.name if obj.camera else None

    def get_reference_image_url(self, obj):
        """Signed, for the same reason the registration documents are.

        The bucket's public host only works while public access is on, and R2
        ships with the development URL turned off — measured here: the object
        reads fine over the S3 API (78 KB) while the public host does not
        answer at all, which on screen is the zone editor's broken thumbnail
        with nothing to draw bays on. `signed_document_url` already handles
        this, including the local-storage case, so use it rather than a second
        way of doing the same thing.
        """
        return signed_document_url(obj.reference_image, self.context.get('request'))

    # ── Zone/bay facts (drawn map + camera) ───────────────────────────────────
    #
    # All of these read the prefetched `spaces` cache. `obj.spaces.count()` and
    # `obj.spaces.filter(...)` issue fresh SQL and ignore the prefetch, so the
    # old versions cost three queries per zone — nine round trips to serialize
    # three zones, on top of the prefetch that had already loaded the rows.
    # Counting the cached list in Python costs none.

    def get_space_count(self, obj):
        return len(obj.spaces.all())

    def get_total_capacity(self, obj):
        """This zone's declared capacity — the admin's number, or the number of
        bays drawn when they have not set one."""
        if obj.capacity_override is not None:
            return obj.capacity_override
        return len(obj.spaces.all())

    def get_bays_occupied(self, obj):
        """Bays the camera currently reads as taken. A map fact, not a count of
        vehicles on campus — an unmapped zone reports 0 here while its category
        may be completely full."""
        return sum(1 for s in obj.spaces.all() if s.is_occupied)

    def get_occupied_count(self, obj):
        # Kept as the bay reading it has always been; category occupancy is
        # exposed separately as `category_occupied`.
        return self.get_bays_occupied(obj)


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

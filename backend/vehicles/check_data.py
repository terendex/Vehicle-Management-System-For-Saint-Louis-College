from django.core.management.base import BaseCommand
from vehicles.models import Vehicle, VehicleRegistration
from accounts.models import User


class Command(BaseCommand):
    def handle(self, *args, **options):
        print('=== Users ===')
        for u in User.objects.all():
            print(f'  {u.email}: full_name="{u.full_name}", user_code={u.user_code}, role={u.role}, owner_type={u.owner_type}')

        print('\n=== Vehicles ===')
        for v in Vehicle.objects.select_related('user').all():
            owner_name = v.user.full_name if v.user else 'None'
            print(f'  {v.plate_number} ({v.vehicle_type}): owner={owner_name}, authorized={v.is_authorized}')

        print('\n=== Accepted VehicleRegistrations ===')
        for reg in VehicleRegistration.objects.filter(status='accepted'):
            print(f'  {reg.full_name}: registrant_type={reg.registrant_type}, student_id={reg.student_id}, system_student_id={reg.system_student_id}, system_employee_id={reg.system_employee_id}')

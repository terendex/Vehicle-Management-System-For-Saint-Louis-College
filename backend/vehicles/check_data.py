from django.core.management.base import BaseCommand
from vehicles.models import Owner, VehicleRegistration
from accounts.models import User

class Command(BaseCommand):
    def handle(self, *args, **options):
        print('=== Users ===')
        for u in User.objects.all():
            print(f'  {u.email}: full_name="{u.full_name}", user_code={u.user_code}, role={u.role}')

        print('\n=== Owners ===')
        for o in Owner.objects.all():
            print(f'  {o.full_name} ({o.owner_type}): user_code={o.user_code}')

        print('\n=== Accepted VehicleRegistrations ===')
        for reg in VehicleRegistration.objects.filter(status='accepted'):
            print(f'  {reg.full_name}: registrant_type={reg.registrant_type}, student_id={reg.student_id}, system_student_id={reg.system_student_id}, system_employee_id={reg.system_employee_id}')
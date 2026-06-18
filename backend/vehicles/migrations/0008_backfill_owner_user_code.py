from django.db import migrations
from django.db.models import Q


def backfill_owner_user_code(apps, schema_editor):
    """
    Backfill Owner.user_code using identifier-based matching only.
    
    IDENTIFIER PATH (no silent full_name fallback):
    For student/employee Owners created via VehicleRegistration flow:
    - Owner.full_name → VehicleRegistration.full_name (with matching registrant_type)
    - → User.full_name (exact match required)
    
    This is reliable because:
    1. VehicleRegistration is the authoritative source for registrations
    2. The registrant_type must match owner_type
    3. We require EXACT full_name match on User (not case-insensitive)
    
    For visitor/fetcher Owners: NO identifier path exists → left unmatched
    
    SAME-NAME COLLISIONS: NEVER silently linked. Reported as unmatched.
    """
    Owner = apps.get_model('vehicles', 'Owner')
    User = apps.get_model('accounts', 'User')
    VehicleRegistration = apps.get_model('vehicles', 'VehicleRegistration')
    
    total = Owner.objects.count()
    already_set = Owner.objects.filter(user_code__gt='').count()
    to_process = total - already_set
    
    matched_via_registration = 0
    matched_via_user_name = 0
    unmatched = []
    collisions = []
    
    for owner in Owner.objects.filter(user_code=''):
        user = None
        
        # Tier 1: Try VehicleRegistration-based matching for student/employee
        if owner.owner_type in ['student', 'employee']:
            reg_type = 'student' if owner.owner_type == 'student' else 'employee'
            regs = VehicleRegistration.objects.filter(
                full_name__iexact=owner.full_name,
                registrant_type=reg_type,
                status='accepted'
            )
            if regs.exists():
                # Found matching registration - now find User by full_name
                # Use exact match to avoid collisions
                users = list(User.objects.filter(full_name=owner.full_name))
                if len(users) == 1:
                    user = users[0]
                    if user.user_code:
                        owner.user_code = user.user_code
                        owner.save(update_fields=['user_code'])
                        matched_via_registration += 1
                elif len(users) > 1:
                    collisions.append({
                        'owner_id': owner.id,
                        'full_name': owner.full_name,
                        'owner_type': owner.owner_type,
                        'reason': 'multiple_users_same_exact_name',
                        'matched_users': [u.email for u in users],
                    })
        
        # Tier 2: Direct User matching (only if exactly one match with exact name)
        if not user:
            users = list(User.objects.filter(full_name=owner.full_name))
            if len(users) == 1:
                user = users[0]
                if user.user_code:
                    owner.user_code = user.user_code
                    owner.save(update_fields=['user_code'])
                    matched_via_user_name += 1
            elif len(users) > 1:
                collisions.append({
                    'owner_id': owner.id,
                    'full_name': owner.full_name,
                    'owner_type': owner.owner_type,
                    'reason': 'multiple_users_same_exact_name',
                    'matched_users': [u.email for u in users],
                })
        
        if not user and owner.owner_type in ['student', 'employee']:
            # Check if there was a registration but no user found
            reg_type = 'student' if owner.owner_type == 'student' else 'employee'
            if not VehicleRegistration.objects.filter(
                full_name__iexact=owner.full_name,
                registrant_type=reg_type,
                status='accepted'
            ).exists():
                unmatched.append({
                    'owner_id': owner.id,
                    'full_name': owner.full_name,
                    'owner_type': owner.owner_type,
                    'reason': 'no_matching_registration_or_user',
                })
        elif not user:
            unmatched.append({
                'owner_id': owner.id,
                'full_name': owner.full_name,
                'owner_type': owner.owner_type,
                'reason': 'no_identifier_path',
            })
    
    print(f"\n=== Owner.user_code Backfill Report ===")
    print(f"Total Owners: {total}")
    print(f"Already had user_code: {already_set}")
    print(f"Processed: {to_process}")
    print(f"Matched via VehicleRegistration path: {matched_via_registration}")
    print(f"Matched via unique User full_name: {matched_via_user_name}")
    print(f"Same-name collisions detected: {len(collisions)}")
    print(f"Unmatched: {len(unmatched)}")
    
    for c in collisions:
        print(f"  COLLISION: ID {c['owner_id']}: {c['full_name']} ({c['owner_type']})")
        print(f"    Reason: {c['reason']}")
        print(f"    Matched Users: {c['matched_users']}")
    
    for u in unmatched:
        print(f"  UNMATCHED: ID {u['owner_id']}: {u['full_name']} ({u['owner_type']})")
        print(f"    Reason: {u['reason']}")


def reverse_backfill(apps, schema_editor):
    """Reverse: clear user_code fields."""
    Owner = apps.get_model('vehicles', 'Owner')
    Owner.objects.filter(user_code__gt='').update(user_code='')


class Migration(migrations.Migration):
    dependencies = [
        ('vehicles', '0007_owner_user_code'),
    ]

    operations = [
        migrations.RunPython(backfill_owner_user_code, reverse_backfill),
    ]
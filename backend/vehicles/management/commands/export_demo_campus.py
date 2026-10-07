"""Save the fictional demo campus (slc_manual_demo) to a file kept in git.

A PC that has no slc_manual_demo database (any PC but the one the manual's
screenshots were taken on) builds the instructor demo from this file instead:
sim_setup migrates an empty slc_sim_demo and loads it. Run it again after
changing the demo campus, then commit the file:

    . docs\\user-manual\\capture\\demo-env.ps1
    cd backend
    venv\\Scripts\\python.exe -X utf8 manage.py export_demo_campus

Runs only under demo_settings, so it can never read the live database. The
file is the same kind a system backup writes (accounts.backup_utils), plus
the demo accounts' two-factor keys, which are invented and needed for the
actions that ask for a code. Leftovers of local testing are left out: the
scheduler's run ledger (keyed by this PC's name) and anything naming a folder
on this PC.
"""
import json
import os

from django.conf import settings
from django.core import serializers
from django.core.management.base import BaseCommand, CommandError

DEMO_DB = 'slc_manual_demo'
FIXTURE = os.path.join(settings.BASE_DIR, '..', 'docs', 'user-manual', 'capture', 'demo_campus.json')

APPS = ['accounts', 'vehicles', 'scanning', 'violations', 'realtime']
# Rebuildable ML artefacts (as in a backup) and the scheduler's run ledger.
SKIP_MODELS = {'scanning.platerecognitionrecord', 'scanning.mltrainingsample', 'vehicles.dailyjobrun'}


class Command(BaseCommand):
    help = 'Save the fictional demo campus (slc_manual_demo) to docs/user-manual/capture/demo_campus.json.'

    def handle(self, **options):
        from django.apps import apps
        if settings.DATABASES['default']['NAME'] != DEMO_DB:
            raise CommandError(f'Run this under demo_settings (database {DEMO_DB}); see demo-env.ps1.')

        rows = []
        for label in APPS:
            for model in apps.get_app_config(label).get_models():
                if model._meta.label_lower in SKIP_MODELS or model._meta.proxy or not model._meta.managed:
                    continue
                rows += json.loads(serializers.serialize('json', model.objects.order_by('pk')))

        kept = []
        for row in rows:
            if row['model'] == 'vehicles.systemsettings':
                row['fields']['scheduled_backup_folder'] = ''
            # A folder on this PC (C:\...), e.g. in an audit entry of a settings change.
            if ':\\' in json.dumps(row['fields'], ensure_ascii=False).replace('\\\\', '\\'):
                continue
            kept.append(row)

        with open(FIXTURE, 'w', encoding='utf-8', newline='\n') as fh:
            json.dump(kept, fh, ensure_ascii=False, indent=1)
            fh.write('\n')
        self.stdout.write(self.style.SUCCESS(
            f'Saved {len(kept)} rows ({len(rows) - len(kept)} left out) to {os.path.normpath(FIXTURE)}.'))

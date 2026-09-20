"""`python manage.py doctor` - a read-only health check for this machine's setup.

WHAT IT DOES
    Runs a list of quick checks and prints one line per check, marked
    [ OK ], [WARN] or [FAIL], each with a plain explanation. It answers the
    questions you usually ask first when something "works on one machine but
    not another": are the installed packages the ones production uses, which
    database and file storage is this machine pointed at, is Redis reachable,
    is ffmpeg installed, are the model weights present?

WHAT IT NEVER DOES
    It changes nothing. The only outside contact is: one "SELECT 1" on the
    database, one PING to Redis, and running `pip check` (which only reads).
    No passwords or secret keys are ever printed.

OPTIONS
    --gpu   also load PyTorch and report whether the graphics card can be used
            for plate reading (takes several seconds, so it is off by default)

EXIT CODE
    0 when nothing FAILed (warnings are allowed), 1 when any check FAILed, so
    scripts such as dev.ps1 can stop early.
"""

# ── Tools this command uses ──────────────────────────────────────────────────
import platform                      # reports the Python version
import re                            # reads package names out of requirements.txt
import shutil                        # finds programs such as ffmpeg on the PATH
import subprocess                    # runs `pip check` as a separate program
import sys                           # the Python interpreter running this command
from importlib import metadata       # lists which package versions are installed
from urllib.parse import urlsplit    # takes a URL apart so passwords can be left out

import django                                         # to report Django's version
from django.conf import settings                      # the settings currently in use
from django.core.management.base import BaseCommand   # the base for manage.py commands
from django.db import connection                      # the app's database connection


class Command(BaseCommand):
    # One-line description shown by `python manage.py help doctor`.
    help = 'Read-only health check: package versions, database, Redis, storage, email, ffmpeg, model weights.'

    # ── Command-line options ─────────────────────────────────────────────────
    def add_arguments(self, parser):
        # --gpu turns on the slow PyTorch/graphics-card check.
        parser.add_argument('--gpu', action='store_true',
                            help='Also check whether PyTorch can use the graphics card (slow).')

    # ── Small helpers that print one result line each ────────────────────────
    # Every check reports through one of these three, so the output is uniform
    # and the command can count failures at the end.
    def _ok(self, what, detail=''):
        # A check that passed.
        self.stdout.write(self.style.SUCCESS('[ OK ] ') + f'{what}' + (f' - {detail}' if detail else ''))

    def _warn(self, what, detail=''):
        # Not broken, but worth knowing before you debug.
        self.warnings += 1
        self.stdout.write(self.style.WARNING('[WARN] ') + f'{what}' + (f' - {detail}' if detail else ''))

    def _fail(self, what, detail=''):
        # Something that will stop the app working properly.
        self.failures += 1
        self.stdout.write(self.style.ERROR('[FAIL] ') + f'{what}' + (f' - {detail}' if detail else ''))

    # ── The main routine: run every check in turn ────────────────────────────
    def handle(self, *args, **options):
        # Start both counters at zero; the helpers above add to them.
        self.failures = 0
        self.warnings = 0

        # Run the checks in the order you would normally investigate.
        self.check_python_and_django()
        self.check_package_versions()
        self.check_package_conflicts()
        self.check_database()
        self.check_redis()
        self.check_storage_and_email()
        self.check_ffmpeg()
        self.check_model_weights()
        if options['gpu']:
            self.check_gpu()

        # One summary line, then exit 1 if anything failed so scripts can react.
        self.stdout.write(f'\nSummary: {self.failures} failed, {self.warnings} warning(s).')
        if self.failures:
            sys.exit(1)

    # ── 1. Python and Django ─────────────────────────────────────────────────
    def check_python_and_django(self):
        # Show which Python, which Django and which settings file are in use.
        # Production runs Python 3.12 (see DEPLOY.md), so anything else is a warning.
        py = platform.python_version()
        settings_module = settings.SETTINGS_MODULE
        if py.startswith('3.12.'):
            self._ok('Python', f'{py} (matches production)')
        else:
            self._warn('Python', f'{py}; production uses 3.12')
        self._ok('Django', f'{django.get_version()}, settings module "{settings_module}"')

    # ── 2. Installed packages versus the versions production installs ────────
    def check_package_versions(self):
        # requirements.txt at the repository root holds the exact versions that
        # production installs. Compare every "name==version" (or "~=") line with
        # what is actually installed here; differences make local results unreliable.
        requirements = settings.BASE_DIR.parent / 'requirements.txt'
        if not requirements.exists():
            self._warn('Package versions', f'{requirements} not found, skipped')
            return

        # Package names are compared ignoring case and the -, _, . differences.
        def normalise(name):
            return re.sub(r'[-_.]+', '-', name).lower()

        # Build a lookup of every installed package: normalised name -> version.
        installed = {normalise(d.metadata['Name']): d.version for d in metadata.distributions()}

        mismatched, missing = [], []
        for raw in requirements.read_text(encoding='utf-8').splitlines():
            line = raw.split('#')[0].strip()           # ignore comments and blank lines
            m = re.match(r'^([A-Za-z0-9_.\-]+)(\[[^\]]+\])?\s*(==|~=)\s*([^\s;]+)', line)
            if not m:
                continue                               # not a pinned package line
            name, op, wanted = m[1], m[3], m[4]
            have = installed.get(normalise(name))
            if have is None:
                missing.append(f'{name}{op}{wanted}')
                continue
            # "2.12.0+cu130" counts as 2.12.0: the part after "+" only marks a
            # local build (here, the graphics-card build of PyTorch).
            have_public = have.split('+')[0]
            if op == '==' and have_public != wanted:
                mismatched.append(f'{name} {have} (pinned {wanted})')
            elif op == '~=' and not have_public.startswith(wanted.rsplit('.', 1)[0] + '.'):
                mismatched.append(f'{name} {have} (pinned ~={wanted})')

        if missing:
            self._fail('Package versions', f'{len(missing)} pinned package(s) not installed: ' + ', '.join(missing))
        if mismatched:
            self._warn('Package versions', f'{len(mismatched)} differ from production: ' + '; '.join(mismatched)
                       + '. Fix with: pip install -r requirements.txt (from the repository root)')
        if not missing and not mismatched:
            self._ok('Package versions', 'every pinned package matches requirements.txt (production versions)')

    # ── 3. Packages that disagree with each other ────────────────────────────
    def check_package_conflicts(self):
        # `pip check` lists installed packages whose own requirements are not met.
        # Some are expected here (see the note printed below), so this is a warning.
        try:
            result = subprocess.run([sys.executable, '-m', 'pip', 'check'],
                                    capture_output=True, text=True, timeout=120)
        except Exception as exc:  # pip missing or too slow: report, don't crash
            self._warn('Package conflicts', f'could not run pip check ({exc})')
            return
        lines = [ln for ln in result.stdout.splitlines() if ln.strip() and 'No broken requirements' not in ln]
        if not lines:
            self._ok('Package conflicts', 'pip check found none')
            return
        self._warn('Package conflicts', f'pip check lists {len(lines)}:')
        for ln in lines:
            self.stdout.write(f'         {ln}')
        # Known and accepted as of 2026-09: roboflow (only used by the offline
        # dataset tool scanning/ml/download_datasets.py) and typer are not part
        # of production and want newer numpy/opencv/click; protobuf is held back
        # for PaddleOCR.
        self.stdout.write('         (roboflow/typer are local-only tools; protobuf is held back for PaddleOCR)')

    # ── 4. Database: which one, and can we reach it? ─────────────────────────
    def check_database(self):
        # Show which database this machine would change - name and host only,
        # never the password - and prove it answers with a harmless SELECT 1.
        db = settings.DATABASES['default']
        name, host = db.get('NAME', ''), db.get('HOST', '') or 'localhost'
        where = f'{db.get("ENGINE", "").rsplit(".", 1)[-1]} "{name}" on {host}'
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1')
                cursor.fetchone()
        except Exception as exc:
            self._fail('Database', f'{where} did not answer: {exc}')
            return
        # A database that is not on this computer is shared: anything done while
        # debugging (admin edits, migrate, deleting rows) changes real data.
        if host in ('localhost', '127.0.0.1', '::1'):
            self._ok('Database', f'{where} (on this computer)')
        else:
            self._warn('Database', f'{where} answers - this is a REMOTE, shared database; '
                       'changes made while debugging are real')

    # ── 5. Redis: live updates and the Celery job queue ──────────────────────
    def check_redis(self):
        # Without REDIS_URL the app uses an in-memory channel layer: live updates
        # only reach browsers connected to this same process. With it, PING it.
        url = getattr(settings, 'REDIS_URL', '')
        if not url:
            self._warn('Redis', 'REDIS_URL not set - live updates stay inside this one process, '
                       'and Celery background jobs cannot run')
            return
        parts = urlsplit(url)
        shown = f'{parts.hostname}:{parts.port or 6379}'   # host and port only, no password
        try:
            import redis                                    # installed with channels-redis
            redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2).ping()
        except Exception as exc:
            self._fail('Redis', f'{shown} did not answer PING ({exc.__class__.__name__}) - '
                       'live updates and Celery jobs will not work')
            return
        if getattr(settings, 'REDIS_IS_LOOPBACK', False):
            self._ok('Redis', f'{shown} answers (on this computer: live updates reach only this host)')
        else:
            self._ok('Redis', f'{shown} answers')

    # ── 6. File storage and outgoing email ───────────────────────────────────
    def check_storage_and_email(self):
        # Uploaded files go either to Cloudflare R2 (the live, public bucket) or
        # to backend/media on this disk. Knowing which matters before you upload.
        storage = settings.STORAGES['default']['BACKEND']
        if 's3' in storage.lower():
            self._warn('File storage', 'Cloudflare R2 (the LIVE bucket) - files uploaded while debugging '
                       'are real and publicly readable')
        else:
            self._ok('File storage', f'local folder {settings.MEDIA_ROOT}')

        # Which mail backend sends email, and whether sending happens in the
        # background (failures then show up in the log, not in the response).
        # Keep the last two parts ("smtp.EmailBackend", "email_backends.BrevoEmailBackend")
        # so it is clear which transport is meant.
        backend = '.'.join(settings.EMAIL_BACKEND.rsplit('.', 2)[-2:])
        mode = 'in the background' if getattr(settings, 'EMAIL_SEND_ASYNC', False) else 'inline'
        self._ok('Email', f'{backend}, sent {mode}. Run `manage.py check_email` to test delivery')

    # ── 7. ffmpeg: needed to read the IP cameras ─────────────────────────────
    def check_ffmpeg(self):
        # Every camera stream goes through ffmpeg (vehicles/ffmpeg_capture.py).
        path = shutil.which('ffmpeg')
        if not path:
            self._fail('ffmpeg', 'not found on PATH - camera feeds cannot open')
            return
        # Chocolatey installs a small launcher ("shim") that starts the real
        # ffmpeg. Stopping the shim can leave the real ffmpeg running and holding
        # the camera, which shows up as frozen feeds.
        if 'chocolatey' in path.lower():
            self._warn('ffmpeg', f'{path} is a Chocolatey shim - if feeds freeze, look for leftover '
                       'ffmpeg.exe processes whose parent has exited')
        else:
            self._ok('ffmpeg', path)

    # ── 8. Machine-learning model weights ────────────────────────────────────
    def check_model_weights(self):
        # The plate detector is required for scanning; the vehicle detector is
        # optional (detection runs in plate-only mode without it).
        runs = settings.BASE_DIR / 'scanning' / 'ml' / 'runs'
        for label, folder, required in (('Plate model', 'plate_detector', True),
                                        ('Vehicle model', 'vehicle_detector', False)):
            weights = runs / folder / 'weights' / 'best.pt'
            if weights.exists():
                self._ok(label, f'{weights.relative_to(settings.BASE_DIR)} '
                                f'({weights.stat().st_size / 1_048_576:.1f} MB)')
            elif required:
                self._fail(label, f'{weights} missing - plate scanning cannot work')
            else:
                self._warn(label, f'{weights} missing - detection will run in plate-only mode')

    # ── 9. (optional) Graphics card for plate reading ────────────────────────
    def check_gpu(self):
        # Loading PyTorch takes seconds, which is why this only runs with --gpu.
        # Plate reading still works on the processor alone, just more slowly.
        try:
            import torch
        except Exception as exc:
            self._warn('GPU', f'PyTorch could not be loaded ({exc})')
            return
        if torch.cuda.is_available():
            self._ok('GPU', f'{torch.cuda.get_device_name(0)} usable (PyTorch {torch.__version__})')
        else:
            self._warn('GPU', f'no usable graphics card (PyTorch {torch.__version__}) - plate reading uses the processor')

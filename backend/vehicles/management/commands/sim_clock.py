"""Move the instructor demo's simulated clock from a terminal.

Runs only under sim_settings (the backend window `.\\dev.ps1 -SimClock` opens
already has it set):

    python manage.py sim_clock status
    python manage.py sim_clock on | off
    python manage.py sim_clock set 2026-10-09 10:00
    python manage.py sim_clock advance 3d        (also 5h, 2wd = working days, -1d)
    python manage.py sim_clock reset             (back to the real time, clock on)
    python manage.py sim_clock run-jobs          (expiry, reminders, archiving: now)
    python manage.py sim_clock code              (the demo admin's current 2FA code)

The web server picks every change up within a second.
"""
from django.core.management.base import BaseCommand, CommandError

import sim_clock


class Command(BaseCommand):
    help = "Show or move the instructor demo's simulated clock (sim_settings only)."

    def add_arguments(self, parser):
        parser.add_argument('action', nargs='?', default='status',
                            choices=['status', 'on', 'off', 'set', 'advance', 'reset', 'run-jobs', 'code'])
        parser.add_argument('value', nargs='*', help="a date for 'set', a step for 'advance'")

    def handle(self, action, value, **options):
        import sim_clock_actions as actions
        if not actions.available():
            # Said plainly rather than raised: this is what a normal-settings
            # run prints, and "OFF" is the answer to "is it on?".
            self.stdout.write('OFF: the simulated clock only runs under sim_settings (.\\dev.ps1 -SimClock).')
            return
        try:
            if action == 'on':
                state = actions.enable(True)
            elif action == 'off':
                state = actions.enable(False)
            elif action == 'set':
                state = actions.set_to(actions.parse_when(' '.join(value)))
            elif action == 'advance':
                state = actions.advance(**actions.parse_step(''.join(value)))
            elif action == 'reset':
                state = actions.reset()
            elif action == 'run-jobs':
                state = actions.run_jobs()
                for job in state['jobs']:
                    mark = 'ok ' if job['ok'] else 'ERR'
                    self.stdout.write(f"  [{mark}] {job['label']}: {job['result']}")
            elif action == 'code':
                return self._code()
            else:
                state = actions.status()
        except (ValueError, sim_clock.SimClockRefused) as exc:
            raise CommandError(str(exc))
        self._report(state)

    def _report(self, st):
        if st['enabled']:
            self.stdout.write(self.style.WARNING(
                f"SIMULATED DATE: {st['now_display']}  (real: {st['real_now_display']}, "
                f"offset {st['offset_text']})"))
        else:
            self.stdout.write(f"Simulated clock OFF: the demo runs on the real date ({st['real_now_display']}).")
        self.stdout.write(f"Database: {st['database']}  ·  Email to: {st['email_to'] or '(not sent, printed in the backend window)'}")

    def _code(self):
        import pyotp
        from accounts.models import TwoFactorDevice
        devices = TwoFactorDevice.objects.filter(user__role='admin', confirmed_at__isnull=False,
                                                 user__is_archived=False).select_related('user')
        if not devices:
            raise CommandError('No admin in this demo database has 2FA set up yet.')
        for device in devices:
            # The phone's code: computed from the real time, like the phone.
            code = pyotp.TOTP(device.secret).at(int(sim_clock.real_now().timestamp()))
            self.stdout.write(f'{device.user.email}: {code}')

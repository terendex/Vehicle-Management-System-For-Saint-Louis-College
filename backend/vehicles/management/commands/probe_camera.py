"""Probe a camera or NVR from the campus PC and print what it says.

The Add Device dialog reports a conclusion; this prints the evidence — every
URL tried and the status the device returned — which is what you need when a
camera that is plainly working refuses to be detected.

    python manage.py probe_camera 192.168.68.102 --device-id 6885002562 \
        --password secret --channel 2

Add --all-channels to sweep an NVR and find out how many cameras are on it.
"""
from django.core.management.base import BaseCommand

from vehicles import rtsp_probe


class Command(BaseCommand):
    help = 'Probe a camera/NVR for its RTSP stream URL and print every attempt.'

    def add_arguments(self, parser):
        parser.add_argument('ip')
        parser.add_argument('--device-id', default='', help='ID printed on the unit')
        parser.add_argument('--password', default='')
        parser.add_argument('--channel', type=int, default=1)
        parser.add_argument('--all-channels', action='store_true',
                            help='Try channels 1-8 and report which ones answer.')

    def handle(self, *args, **o):
        ip = o['ip']
        self.stdout.write(f'Port {rtsp_probe.RTSP_PORT} on {ip}: ', ending='')
        if not rtsp_probe.is_reachable(ip):
            self.stdout.write(self.style.ERROR('no answer'))
            self.stdout.write(
                '\nThe device is not reachable from this machine. Check the IP, '
                'that this PC is on the same network, and that no firewall is '
                'blocking port 554.')
            return
        self.stdout.write(self.style.SUCCESS('open'))

        channels = range(1, 9) if o['all_channels'] else [max(1, o['channel'])]
        found = []

        for ch in channels:
            self.stdout.write(f'\n=== Channel {ch} ===')
            res = rtsp_probe.detect(ip, o['device_id'], o['password'], channel=ch)

            for line in res.get('attempts', []):
                ok = line.endswith('-> 200')
                self.stdout.write('  ' + (self.style.SUCCESS(line) if ok else line))

            if res['ok']:
                found.append((ch, res['rtsp_url']))
                self.stdout.write(self.style.SUCCESS(
                    f"\n  WORKING: {res['rtsp_url']}  ({res['format']})"))
            else:
                self.stdout.write(self.style.WARNING(f"\n  {res['error']}"))
                self.stdout.write(f"  Best guess: {res['suggestion']}")

        if o['all_channels']:
            self.stdout.write('\n=== Summary ===')
            if found:
                for ch, url in found:
                    self.stdout.write(self.style.SUCCESS(f'  channel {ch}: {url}'))
                self.stdout.write(
                    f'\n  {len(found)} camera(s) on this device. Add each one in '
                    f'Device Management using its channel number.')
            else:
                self.stdout.write(self.style.WARNING(
                    '  No channel answered. If the device works in its own app, '
                    'check the manual for the RTSP URL it publishes and confirm '
                    'RTSP is enabled in the NVR settings.'))

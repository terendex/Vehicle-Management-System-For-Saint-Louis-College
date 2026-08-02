"""Print the host of every registered camera, one per line: "name<TAB>host".

Exists so run-campus.ps1 can check the cameras that are *actually configured*
rather than a hardcoded pair of addresses that drifted out of date and reported
NO ROUTE for cameras nobody uses.

A management command rather than `python -c "..."` in the script: Windows
PowerShell strips the quotes out of a multi-line string when handing it to a
native executable, so the inline version died with a SyntaxError that the
script's `2>$null` swallowed — the check silently reported "no cameras" while
two were registered.
"""
import re

from django.core.management.base import BaseCommand

from vehicles.models import Camera

_SCHEME = re.compile(r'^\w+://([^@]*@)?')


def host_of(rtsp_url: str) -> str:
    """Bare host from an RTSP URL, without scheme, credentials, port or path."""
    if not rtsp_url:
        return ''
    return _SCHEME.sub('', rtsp_url).split('/')[0].split(':')[0].strip()


class Command(BaseCommand):
    help = "List registered cameras as 'name<TAB>host' lines (used by run-campus.ps1)."

    def handle(self, *args, **options):
        for cam in Camera.objects.all().order_by('name'):
            host = host_of(cam.rtsp_url)
            if host:
                self.stdout.write(f"{cam.name}\t{host}")

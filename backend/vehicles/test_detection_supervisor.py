"""Where the parking auto-detector is allowed to run.

The supervisor opens RTSP streams to the campus cameras. That is right on the
campus server and wrong in the cloud, where those 192.168.x.x addresses are
unroutable: every pass there spent ~30s per zone timing out an open it could
never complete, spawned an ffmpeg child each time, and starved the single
Daphne process that also serves the site — submitting the registration form
took over a minute because every request was queued behind camera opens.

So the default is now host-dependent rather than always-on, which is subtle
enough to be worth pinning: a wrong default here is invisible in code review
and very loud in production.
"""
from unittest import mock

from django.test import TestCase

from vehicles.detection_supervisor import _autodetect_disabled

RAILWAY = {'RAILWAY_ENVIRONMENT': 'production'}
RAILWAY_DOMAIN = {'RAILWAY_PUBLIC_DOMAIN': 'slc.up.railway.app'}
CAMPUS = {}


def _env(**over):
    """A clean environment with only the given markers set."""
    base = {'DISABLE_PARKING_AUTODETECT': '', 'RAILWAY_ENVIRONMENT': '',
            'RAILWAY_PUBLIC_DOMAIN': ''}
    base.update(over)
    return mock.patch.dict('os.environ', base, clear=False)


class AutodetectHostDefaultTests(TestCase):

    def test_it_runs_on_a_host_with_no_railway_markers(self):
        """The campus server: cameras are on the same LAN, so it stays on."""
        with _env():
            self.assertFalse(_autodetect_disabled())

    def test_it_is_off_on_railway(self):
        with _env(**RAILWAY):
            self.assertTrue(_autodetect_disabled())

    def test_the_public_domain_marker_is_enough(self):
        """RAILWAY_ENVIRONMENT is not set on every plan; the domain is."""
        with _env(**RAILWAY_DOMAIN):
            self.assertTrue(_autodetect_disabled())

    def test_an_explicit_false_overrides_the_railway_default(self):
        """For a cloud host that genuinely can reach the cameras — a VPN or a
        tunnel. Explicit configuration has to beat the inferred default, or the
        setup becomes impossible to express."""
        with _env(DISABLE_PARKING_AUTODETECT='false', **RAILWAY):
            self.assertFalse(_autodetect_disabled())

    def test_an_explicit_true_turns_it_off_on_campus(self):
        with _env(DISABLE_PARKING_AUTODETECT='true'):
            self.assertTrue(_autodetect_disabled())

    def test_the_flag_is_read_case_and_space_insensitively(self):
        for raw in ('TRUE', ' yes ', '1'):
            with self.subTest(raw=raw):
                with _env(DISABLE_PARKING_AUTODETECT=raw):
                    self.assertTrue(_autodetect_disabled())
        for raw in ('FALSE', ' no ', '0'):
            with self.subTest(raw=raw):
                with _env(DISABLE_PARKING_AUTODETECT=raw, **RAILWAY):
                    self.assertFalse(_autodetect_disabled())

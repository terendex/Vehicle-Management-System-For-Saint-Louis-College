"""PTZ still authenticates after the camera password column was removed.

Cameras carry no stored password. A unit that needs one carries it inside its
rtsp_url, and a unit that needs none must not be sent a Basic header it never
asked for — some firmware answers 401 to credentials it did not want, which is
how removing the password broke PTZ on exactly the open cameras it was removed
for.
"""
from unittest.mock import patch, MagicMock

from django.test import TestCase

from vehicles.models import Camera
from vehicles.views import camera_http_credentials, _try_cgi_ptz


def cam(rtsp_url='', device_id='dev123'):
    return Camera(name='Cam X', ip='10.0.0.5', device_id=device_id,
                  rtsp_url=rtsp_url, assignment='entry')


class CredentialResolutionTests(TestCase):
    def test_credentials_come_from_the_rtsp_url_when_present(self):
        u, p = camera_http_credentials(cam('rtsp://operator:s3cret@10.0.0.5/stream1'))
        self.assertEqual((u, p), ('operator', 's3cret'))

    def test_password_containing_an_at_sign_is_recovered(self):
        """rsplit on the last @ — splitting on the first would truncate it."""
        u, p = camera_http_credentials(cam('rtsp://admin:p@ss@10.0.0.5/stream1'))
        self.assertEqual((u, p), ('admin', 'p@ss'))

    def test_percent_encoded_credentials_are_decoded(self):
        u, p = camera_http_credentials(cam('rtsp://admin:p%40ss%2Fword@10.0.0.5/stream1'))
        self.assertEqual((u, p), ('admin', 'p@ss/word'))

    def test_username_only_url_gives_an_empty_password(self):
        u, p = camera_http_credentials(cam('rtsp://operator@10.0.0.5/stream1'))
        self.assertEqual((u, p), ('operator', ''))

    def test_credential_less_url_falls_back_to_the_device_id(self):
        u, p = camera_http_credentials(cam('rtsp://10.0.0.5/stream1', device_id='dev123'))
        self.assertEqual((u, p), ('dev123', ''))

    def test_no_url_at_all_falls_back_to_the_device_id(self):
        u, p = camera_http_credentials(cam('', device_id='dev123'))
        self.assertEqual((u, p), ('dev123', ''))


class CgiAuthFallbackTests(TestCase):
    """The regression this fixes: an open camera 401s a Basic header it did
    not ask for, so PTZ must retry with no credentials."""

    def _resp(self, code):
        r = MagicMock()
        r.status_code = code
        return r

    def test_retries_without_credentials_after_a_401(self):
        seen = []

        def fake_get(url, auth=None, timeout=None):
            seen.append(auth)
            return self._resp(401 if auth is not None else 200)

        with patch('requests.get', side_effect=fake_get):
            idx = _try_cgi_ptz('http://10.0.0.5', 'dev', '', 'left', 5, cgi_form=0)

        self.assertEqual(idx, 0)
        self.assertEqual(seen[0], ('dev', ''))   # tried credentials first
        self.assertIsNone(seen[1])               # then without

    def test_credentials_are_used_when_they_work(self):
        seen = []

        def fake_get(url, auth=None, timeout=None):
            seen.append(auth)
            return self._resp(200)

        with patch('requests.get', side_effect=fake_get):
            _try_cgi_ptz('http://10.0.0.5', 'operator', 'pw', 'left', 5, cgi_form=0)

        self.assertEqual(seen, [('operator', 'pw')])   # no needless retry

    def test_a_real_error_is_not_masked_by_the_retry(self):
        with patch('requests.get', side_effect=lambda *a, **k: self._resp(500)):
            with self.assertRaises(Exception) as ctx:
                _try_cgi_ptz('http://10.0.0.5', 'dev', '', 'left', 5, cgi_form=0)
        self.assertIn('500', str(ctx.exception))

    def test_blank_username_skips_straight_to_no_auth(self):
        seen = []

        def fake_get(url, auth=None, timeout=None):
            seen.append(auth)
            return self._resp(200)

        with patch('requests.get', side_effect=fake_get):
            _try_cgi_ptz('http://10.0.0.5', '', '', 'left', 5, cgi_form=0)

        self.assertEqual(seen, [None])

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
from requests.auth import HTTPDigestAuth

from vehicles.views import (_AUTH_MODE_CACHE, camera_http_credentials,
                            _try_cgi_ptz)


def cam(rtsp_url='', device_id='dev123', password=''):
    return Camera(name='Cam X', ip='10.0.0.5', device_id=device_id,
                  rtsp_url=rtsp_url, password=password, assignment='entry')


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

    def test_the_url_username_beats_the_device_id_when_a_password_is_stored(self):
        """The regression: device_id is a hardware serial, not an ONVIF login.
        Pairing it with the stored password faulted every SOAP call, which the
        PTZ view reported as 'No PTZ service path responded successfully'."""
        u, p = camera_http_credentials(cam(
            'rtsp://admin:L2830EAA@10.0.0.5/cam/realmonitor?channel=1&subtype=0',
            device_id='16D2FCDPSF444F0', password='L2830EAA'))
        self.assertEqual((u, p), ('admin', 'L2830EAA'))

    def test_the_stored_password_wins_over_a_stale_one_in_the_url(self):
        """Admins edit the password field, not the URL, so it is the fresher
        of the two — but it does not drag the device_id along as username."""
        u, p = camera_http_credentials(cam('rtsp://admin:old@10.0.0.5/stream1',
                                           device_id='serial9', password='new'))
        self.assertEqual((u, p), ('admin', 'new'))

    def test_a_stored_password_with_no_url_user_still_uses_the_device_id(self):
        u, p = camera_http_credentials(cam('rtsp://10.0.0.5/stream1',
                                           device_id='dev123', password='pw'))
        self.assertEqual((u, p), ('dev123', 'pw'))

    def test_no_url_at_all_falls_back_to_the_device_id(self):
        u, p = camera_http_credentials(cam('', device_id='dev123'))
        self.assertEqual((u, p), ('dev123', ''))


class CgiAuthFallbackTests(TestCase):
    """Two regressions live here. An open camera 401s a credential header it
    did not ask for, so PTZ must retry with none; and ONVIF firmware
    challenges with HTTP Digest, which a Basic header never satisfies — that
    one surfaced as 'No PTZ service path responded successfully'."""

    def setUp(self):
        _AUTH_MODE_CACHE.clear()   # the winning flavour is memoised per host

    def _resp(self, code):
        r = MagicMock()
        r.status_code = code
        return r

    def _kind(self, auth):
        if auth is None:
            return 'none'
        return 'digest' if isinstance(auth, HTTPDigestAuth) else 'basic'

    def test_digest_is_tried_before_basic_and_before_no_auth(self):
        seen = []

        def fake_get(url, auth=None, timeout=None):
            seen.append(self._kind(auth))
            return self._resp(401 if auth is not None else 200)

        with patch('requests.get', side_effect=fake_get):
            idx = _try_cgi_ptz('http://10.0.0.5', 'dev', '', 'left', 5, cgi_form=0)

        self.assertEqual(idx, 0)
        self.assertEqual(seen, ['digest', 'basic', 'none'])

    def test_a_digest_camera_authenticates(self):
        seen = []

        def fake_get(url, auth=None, timeout=None):
            seen.append(auth)
            return self._resp(200 if isinstance(auth, HTTPDigestAuth) else 401)

        with patch('requests.get', side_effect=fake_get):
            idx = _try_cgi_ptz('http://10.0.0.5', 'dev', 'pw', 'left', 5, cgi_form=0)

        self.assertEqual(idx, 0)
        self.assertEqual(len(seen), 1)
        self.assertIsInstance(seen[0], HTTPDigestAuth)

    def test_basic_credentials_still_work(self):
        seen = []

        def fake_get(url, auth=None, timeout=None):
            seen.append(self._kind(auth))
            return self._resp(200 if auth == ('operator', 'pw') else 401)

        with patch('requests.get', side_effect=fake_get):
            _try_cgi_ptz('http://10.0.0.5', 'operator', 'pw', 'left', 5, cgi_form=0)

        self.assertEqual(seen, ['digest', 'basic'])

    def test_the_winning_flavour_is_tried_first_next_time(self):
        """Press-and-hold must not re-pay the failed attempts every press."""
        def fake_get(url, auth=None, timeout=None):
            return self._resp(200 if auth == ('operator', 'pw') else 401)

        with patch('requests.get', side_effect=fake_get):
            _try_cgi_ptz('http://10.0.0.5', 'operator', 'pw', 'left', 5, cgi_form=0)

            seen = []

            def recording_get(url, auth=None, timeout=None):
                seen.append(self._kind(auth))
                return fake_get(url, auth, timeout)

            with patch('requests.get', side_effect=recording_get):
                _try_cgi_ptz('http://10.0.0.5', 'operator', 'pw', 'left', 5, cgi_form=0)

        self.assertEqual(seen, ['basic'])

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

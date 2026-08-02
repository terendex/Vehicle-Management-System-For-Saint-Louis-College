"""RTSP auto-detection: ask the camera, don't ask the admin.

The network is mocked throughout — these pin the decision logic (ordering,
stopping at the first hit, what happens when nothing answers), not whether a
particular camera on a particular LAN replies.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APITestCase

from vehicles import rtsp_probe

User = get_user_model()
ENDPOINT = '/api/vehicles/cameras/detect-rtsp/'


class CandidateTests(TestCase):
    def test_device_id_is_tried_before_admin(self):
        urls = [c['url'] for c in rtsp_probe.candidate_urls('10.0.0.5', 'mydev', 'pw')]
        first_dev = next(i for i, u in enumerate(urls) if 'mydev:' in u)
        first_admin = next(i for i, u in enumerate(urls) if 'admin:' in u)
        self.assertLess(first_dev, first_admin)

    def test_admin_is_still_tried_when_the_device_id_differs(self):
        urls = ' '.join(c['url'] for c in rtsp_probe.candidate_urls('10.0.0.5', 'mydev', 'pw'))
        self.assertIn('admin:', urls)

    def test_no_duplicate_user_when_device_id_is_literally_admin(self):
        cands = rtsp_probe.candidate_urls('10.0.0.5', 'admin', 'pw')
        self.assertEqual(len(cands), len(set(c['url'] for c in cands)))

    def test_the_three_vendor_shapes_are_covered(self):
        formats = {c['format'] for c in rtsp_probe.candidate_urls('10.0.0.5', 'd', 'p')}
        self.assertTrue({'generic', 'dahua', 'hikvision'} <= formats)

    def test_credentials_are_url_encoded(self):
        """A password with @ or / would otherwise corrupt the URL it sits in."""
        url = rtsp_probe.candidate_urls('10.0.0.5', 'dev', 'p@ss/word')[0]['url']
        self.assertIn('p%40ss%2Fword', url)
        self.assertEqual(url.count('@'), 1)   # only the credential separator

    # ── cameras with no RTSP password ───────────────────────────────────────
    def test_no_password_tries_the_credential_less_url_first(self):
        """An open camera answers on rtsp://ip/path — empty credentials in
        front of it make some firmware reject the request outright."""
        cands = rtsp_probe.candidate_urls('10.0.0.5', 'dev', '')
        self.assertEqual(cands[0]['url'], 'rtsp://10.0.0.5/stream1')
        self.assertNotIn('@', cands[0]['url'])

    def test_no_password_still_tries_a_username_with_an_empty_one(self):
        urls = [c['url'] for c in rtsp_probe.candidate_urls('10.0.0.5', 'dev', '')]
        self.assertTrue(any(u.startswith('rtsp://dev:@') for u in urls))
        self.assertTrue(any(u.startswith('rtsp://admin:@') for u in urls))

    def test_no_password_covers_every_vendor_path(self):
        formats = {c['format'] for c in rtsp_probe.candidate_urls('10.0.0.5', 'dev', '')}
        self.assertTrue({'generic', 'dahua', 'hikvision'} <= formats)

    def test_password_argument_is_optional(self):
        self.assertTrue(rtsp_probe.candidate_urls('10.0.0.5', 'dev'))

    def test_password_is_redacted_in_logs(self):
        red = rtsp_probe._redact('rtsp://admin:hunter2@10.0.0.5/stream1')
        self.assertNotIn('hunter2', red)
        self.assertIn('admin', red)


class DetectTests(TestCase):
    def test_unreachable_camera_is_reported_without_probing(self):
        with patch.object(rtsp_probe, 'is_reachable', return_value=False), \
             patch.object(rtsp_probe, '_opens') as opens:
            r = rtsp_probe.detect('10.0.0.5', 'dev', 'pw')
        self.assertFalse(r['ok'])
        self.assertIn('10.0.0.5', r['error'])
        opens.assert_not_called()          # no point probing a dead host

    def test_missing_ip_is_rejected(self):
        r = rtsp_probe.detect('', 'dev', 'pw')
        self.assertFalse(r['ok'])

    def test_first_working_candidate_wins_and_probing_stops(self):
        calls = []

        def fake_opens(url, *a, **k):
            calls.append(url)
            return '/cam/realmonitor' in url      # a Dahua unit

        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, '_opens', side_effect=fake_opens):
            r = rtsp_probe.detect('10.0.0.5', 'dev', 'pw')

        self.assertTrue(r['ok'])
        self.assertEqual(r['format'], 'dahua')
        self.assertIn('/cam/realmonitor', r['rtsp_url'])
        # Stopped at the hit rather than walking the whole list.
        self.assertEqual(calls[-1], r['rtsp_url'])

    def test_generic_camera_matches_on_the_first_attempt(self):
        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, '_opens', side_effect=lambda u, *a, **k: u.endswith('/stream1')):
            r = rtsp_probe.detect('10.0.0.5', 'dev', 'pw')
        self.assertTrue(r['ok'])
        self.assertEqual(r['format'], 'generic')
        self.assertEqual(len(r['attempts']), 1)

    def test_camera_up_but_no_path_works_explains_itself(self):
        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, '_opens', return_value=False):
            r = rtsp_probe.detect('10.0.0.5', 'dev', 'pw')
        self.assertFalse(r['ok'])
        self.assertIn('password', r['error'].lower())
        self.assertGreater(len(r['attempts']), 3)     # shows what it tried

    def test_open_camera_with_no_password_is_detected(self):
        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, '_opens',
                          side_effect=lambda u, *a, **k: u == 'rtsp://10.0.0.5/stream1'):
            r = rtsp_probe.detect('10.0.0.5', 'dev', '')
        self.assertTrue(r['ok'])
        self.assertEqual(r['rtsp_url'], 'rtsp://10.0.0.5/stream1')

    def test_failure_without_a_password_suggests_one_may_be_needed(self):
        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, '_opens', return_value=False):
            r = rtsp_probe.detect('10.0.0.5', 'dev', '')
        self.assertFalse(r['ok'])
        self.assertIn('needs a password', r['error'])

    def test_returned_attempts_never_contain_the_password(self):
        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, '_opens', return_value=False):
            r = rtsp_probe.detect('10.0.0.5', 'dev', 'sup3rsecret')
        self.assertTrue(all('sup3rsecret' not in a for a in r['attempts']))


class DetectEndpointTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            email='rtsp-admin@slc.edu.ph', full_name='ADMIN', password='x', role='admin')

    def test_requires_authentication(self):
        r = self.client.post(ENDPOINT, {'ip': '10.0.0.5'}, format='json')
        self.assertIn(r.status_code, (401, 403))

    def test_success_returns_the_url_and_format(self):
        self.client.force_authenticate(self.admin)
        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, '_opens', side_effect=lambda u, *a, **k: u.endswith('/stream1')):
            r = self.client.post(ENDPOINT,
                                 {'ip': '10.0.0.5', 'device_id': 'dev', 'password': 'pw'},
                                 format='json')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data['ok'])
        self.assertEqual(r.data['format'], 'generic')

    def test_failure_is_a_400_with_a_usable_message(self):
        self.client.force_authenticate(self.admin)
        with patch.object(rtsp_probe, 'is_reachable', return_value=False):
            r = self.client.post(ENDPOINT,
                                 {'ip': '10.0.0.5', 'device_id': 'dev', 'password': 'pw'},
                                 format='json')
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.data['ok'])
        self.assertTrue(r.data['error'])

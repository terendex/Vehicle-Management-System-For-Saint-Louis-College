"""RTSP auto-detection: ask the camera, don't ask the admin.

A password is optional: IMOU/Dahua units refuse RTSP without one, a genuinely
open camera needs none, and detection has to cope with both.

The network is mocked throughout: these pin the decision logic (ordering,
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
    def test_credentialed_url_is_tried_first_when_a_password_is_given(self):
        """An IMOU/Dahua unit refuses everything else, and it is the common case."""
        cands = rtsp_probe.candidate_urls('10.0.0.5', 'dev', 'pw')
        self.assertEqual(cands[0]['url'], 'rtsp://dev:pw@10.0.0.5/stream1')

    def test_open_camera_form_is_still_tried_when_a_password_is_given(self):
        urls = [c['url'] for c in rtsp_probe.candidate_urls('10.0.0.5', 'dev', 'pw')]
        self.assertIn('rtsp://10.0.0.5/stream1', urls)

    def test_password_is_url_encoded(self):
        url = rtsp_probe.candidate_urls('10.0.0.5', 'dev', 'p@ss/word')[0]['url']
        self.assertIn('p%40ss%2Fword', url)
        self.assertEqual(url.count('@'), 1)

    def test_credential_less_url_is_tried_first_without_a_password(self):
        """An open camera answers on rtsp://ip/path — empty credentials in
        front of it make some firmware reject the request outright."""
        cands = rtsp_probe.candidate_urls('10.0.0.5', 'dev')
        self.assertEqual(cands[0]['url'], 'rtsp://10.0.0.5/stream1')
        self.assertNotIn('@', cands[0]['url'])

    def test_username_with_empty_password_is_tried_after(self):
        urls = [c['url'] for c in rtsp_probe.candidate_urls('10.0.0.5', 'dev')]
        self.assertTrue(any(u.startswith('rtsp://dev:@') for u in urls))
        self.assertTrue(any(u.startswith('rtsp://admin:@') for u in urls))

    def test_device_id_is_tried_before_admin(self):
        urls = [c['url'] for c in rtsp_probe.candidate_urls('10.0.0.5', 'mydev')]
        first_dev = next(i for i, u in enumerate(urls) if 'mydev:' in u)
        first_admin = next(i for i, u in enumerate(urls) if 'admin:' in u)
        self.assertLess(first_dev, first_admin)

    def test_no_duplicate_candidate_when_device_id_is_literally_admin(self):
        cands = rtsp_probe.candidate_urls('10.0.0.5', 'admin')
        self.assertEqual(len(cands), len(set(c['url'] for c in cands)))

    def test_device_id_is_url_encoded(self):
        """A device ID with @ or / would otherwise corrupt the URL."""
        urls = [c['url'] for c in rtsp_probe.candidate_urls('10.0.0.5', 'a/b@c')]
        creds = next(u for u in urls if u.startswith('rtsp://a'))
        self.assertIn('a%2Fb%40c', creds)
        self.assertEqual(creds.count('@'), 1)      # only the separator

    def test_every_vendor_shape_is_covered(self):
        formats = {c['format'] for c in rtsp_probe.candidate_urls('10.0.0.5', 'dev')}
        self.assertTrue({'generic', 'dahua', 'hikvision'} <= formats)

    def test_blank_device_id_still_yields_candidates(self):
        cands = rtsp_probe.candidate_urls('10.0.0.5', '')
        self.assertTrue(cands)
        self.assertEqual(cands[0]['url'], 'rtsp://10.0.0.5/stream1')

    def test_redaction_hides_any_credential_that_does_appear(self):
        """Custom URLs may still carry a password — it must not reach a log."""
        red = rtsp_probe._redact('rtsp://admin:hunter2@10.0.0.5/stream1')
        self.assertNotIn('hunter2', red)
        self.assertIn('admin', red)


class DetectTests(TestCase):
    def test_unreachable_camera_is_reported_without_probing(self):
        with patch.object(rtsp_probe, 'is_reachable', return_value=False), \
             patch.object(rtsp_probe, '_opens') as opens:
            r = rtsp_probe.detect('10.0.0.5', 'dev')
        self.assertFalse(r['ok'])
        self.assertIn('10.0.0.5', r['error'])
        opens.assert_not_called()          # no point probing a dead host

    def test_missing_ip_is_rejected(self):
        r = rtsp_probe.detect('', 'dev')
        self.assertFalse(r['ok'])

    def test_open_camera_matches_on_the_first_attempt(self):
        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, '_opens',
                          side_effect=lambda u, *a, **k: u == 'rtsp://10.0.0.5/stream1'):
            r = rtsp_probe.detect('10.0.0.5', 'dev')
        self.assertTrue(r['ok'])
        self.assertEqual(r['rtsp_url'], 'rtsp://10.0.0.5/stream1')
        self.assertEqual(r['format'], 'generic')
        self.assertEqual(len(r['attempts']), 1)

    def test_first_working_candidate_wins_and_probing_stops(self):
        calls = []

        def fake_opens(url, *a, **k):
            calls.append(url)
            return '/cam/realmonitor' in url      # a Dahua unit

        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, '_opens', side_effect=fake_opens):
            r = rtsp_probe.detect('10.0.0.5', 'dev')

        self.assertTrue(r['ok'])
        self.assertEqual(r['format'], 'dahua')
        self.assertEqual(calls[-1], r['rtsp_url'])   # stopped at the hit

    def test_camera_up_but_no_path_works_still_lets_you_save(self):
        """The blocker this fixes: with an empty URL box and no known path, the
        camera could not be added at all."""
        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, '_opens', return_value=False):
            r = rtsp_probe.detect('10.0.0.5', 'dev', 'pw')
        self.assertFalse(r['ok'])
        self.assertGreater(len(r['attempts']), 3)          # shows what it tried
        self.assertTrue(r['suggestion'].startswith('rtsp://dev:pw@10.0.0.5'))

    def test_unreachable_camera_also_gets_a_suggestion(self):
        with patch.object(rtsp_probe, 'is_reachable', return_value=False):
            r = rtsp_probe.detect('10.0.0.5', 'dev', 'pw')
        self.assertIn('rtsp://dev:pw@10.0.0.5', r['suggestion'])

    def test_suggestion_uses_a_placeholder_when_no_password_was_given(self):
        self.assertIn('PASSWORD', rtsp_probe.suggestion_for('10.0.0.5', 'dev'))

    def test_probe_stops_at_the_time_budget(self):
        """Every candidate timing out must not leave the admin waiting minutes."""
        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, 'TOTAL_BUDGET_SECONDS', 0), \
             patch.object(rtsp_probe, '_opens', return_value=False) as opens:
            r = rtsp_probe.detect('10.0.0.5', 'dev', 'pw')
        self.assertFalse(r['ok'])
        opens.assert_not_called()          # budget already spent


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
            r = self.client.post(ENDPOINT, {'ip': '10.0.0.5', 'device_id': 'dev'}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data['ok'])
        self.assertEqual(r.data['format'], 'generic')

    def test_failure_is_a_400_with_a_usable_message(self):
        self.client.force_authenticate(self.admin)
        with patch.object(rtsp_probe, 'is_reachable', return_value=False):
            r = self.client.post(ENDPOINT, {'ip': '10.0.0.5', 'device_id': 'dev'}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.data['ok'])
        self.assertTrue(r.data['error'])

    def test_the_password_reaches_the_probe(self):
        """It is the whole point of having the field — an IMOU unit refuses
        every credential-less path."""
        seen = {}

        def fake_detect(ip, device_id, password='', channel=1):
            seen.update(ip=ip, device_id=device_id, password=password, channel=channel)
            return {'ok': True, 'rtsp_url': 'rtsp://x', 'format': 'dahua', 'attempts': []}

        self.client.force_authenticate(self.admin)
        with patch.object(rtsp_probe, 'detect', side_effect=fake_detect):
            r = self.client.post(ENDPOINT,
                                 {'ip': '10.0.0.5', 'device_id': 'dev', 'password': 's3cret'},
                                 format='json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(seen['password'], 's3cret')

    def test_a_camera_can_still_be_saved_without_a_password(self):
        """Optional, not required — an open camera must not need a made-up one."""
        self.client.force_authenticate(self.admin)
        created = self.client.post('/api/vehicles/cameras/', {
            'ip': '10.0.0.7', 'device_id': 'dev7',
            'rtsp_url': 'rtsp://10.0.0.7/stream1',
            'assignment': 'parking',
        }, format='json')
        self.assertIn(created.status_code, (200, 201), msg=str(created.data))
        self.assertEqual(created.data.get('password', ''), '')

"""Several cameras behind one IP — an NVR, or a multi-lens unit.

Registration used to reject a repeated IP address ("one row per physical
camera"), which made those devices impossible to add at all. Identity moved to
the stream URL, which is what actually distinguishes one camera from another.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APITestCase

from vehicles import rtsp_probe
from vehicles.models import Camera

User = get_user_model()
CAMERAS = '/api/vehicles/cameras/'
DETECT = '/api/vehicles/cameras/detect-rtsp/'
IP = '192.168.68.102'


class ChannelPathTests(TestCase):
    def test_channel_two_asks_for_channel_two(self):
        # A list, not a dict: several entries share a format name and dict()
        # would silently keep only the last of each.
        paths = [p for _, p in rtsp_probe.paths_for(2)]
        self.assertIn('/cam/realmonitor?channel=2&subtype=0', paths)

    def test_hikvision_folds_the_channel_into_its_number(self):
        urls = [p for _, p in rtsp_probe.paths_for(3)]
        self.assertIn('/Streaming/Channels/301', urls)

    def test_channel_one_keeps_the_single_camera_shortcuts(self):
        urls = [p for _, p in rtsp_probe.paths_for(1)]
        self.assertIn('/stream1', urls)

    def test_channel_two_drops_paths_that_carry_no_channel(self):
        """/stream1 has no channel in it — probing it for channel 2 would
        return channel 1's video and quietly register the wrong camera."""
        urls = [p for _, p in rtsp_probe.paths_for(2)]
        self.assertNotIn('/stream1', urls)
        self.assertNotIn('/live', urls)

    def test_channel_reaches_the_candidate_urls(self):
        urls = [c['url'] for c in rtsp_probe.candidate_urls(IP, 'dev', 'pw', channel=2)]
        self.assertTrue(any('channel=2' in u for u in urls))
        self.assertFalse(any('channel=1&' in u for u in urls))

    def test_suggestion_follows_the_channel(self):
        s = rtsp_probe.suggestion_for(IP, 'dev', 'pw', channel=4)
        self.assertIn('channel=4', s)

    def test_a_bad_channel_falls_back_to_one(self):
        self.assertEqual(rtsp_probe.paths_for(0), rtsp_probe.paths_for(1))
        self.assertEqual(rtsp_probe.paths_for(None), rtsp_probe.paths_for(1))


class MultiChannelRegistrationTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            email='nvr-admin@slc.edu.ph', full_name='ADMIN', password='x', role='admin')

    def setUp(self):
        self.client.force_authenticate(self.admin)

    def _add(self, channel, ip=IP, device_id='6885002562'):
        return self.client.post(CAMERAS, {
            'ip': ip, 'device_id': device_id, 'password': 'pw',
            'rtsp_url': f'rtsp://a:pw@{ip}/cam/realmonitor?channel={channel}&subtype=0',
            'assignment': 'parking',
        }, format='json')

    def test_two_cameras_on_one_ip_can_both_be_added(self):
        self.assertIn(self._add(1).status_code, (200, 201))
        r = self._add(2)
        self.assertIn(r.status_code, (200, 201), msg=str(r.data))
        self.assertEqual(Camera.objects.filter(ip=IP).count(), 2)

    def test_four_channels_of_one_nvr(self):
        for ch in (1, 2, 3, 4):
            self.assertIn(self._add(ch).status_code, (200, 201), msg=f'channel {ch}')
        self.assertEqual(Camera.objects.filter(ip=IP).count(), 4)

    def test_each_channel_gets_its_own_name(self):
        self._add(1)
        self._add(2)
        names = set(Camera.objects.filter(ip=IP).values_list('name', flat=True))
        self.assertEqual(len(names), 2, f'names collided: {names}')

    def test_the_same_stream_twice_is_still_rejected(self):
        """Loosening the IP rule must not let one camera be added twice."""
        self.assertIn(self._add(2).status_code, (200, 201))
        r = self._add(2)
        self.assertEqual(r.status_code, 400)
        self.assertIn('rtsp_url', r.data)

    def test_a_shared_device_id_is_allowed(self):
        """Every channel of an NVR carries the same device ID."""
        self.assertIn(self._add(1).status_code, (200, 201))
        self.assertIn(self._add(2).status_code, (200, 201))
        self.assertEqual(
            Camera.objects.filter(device_id='6885002562').count(), 2)

    def test_detect_endpoint_accepts_a_channel(self):
        seen = {}

        def fake_detect(ip, device_id, password='', channel=1):
            seen['channel'] = channel
            return {'ok': True, 'rtsp_url': 'rtsp://x', 'format': 'dahua', 'attempts': []}

        with patch.object(rtsp_probe, 'detect', side_effect=fake_detect):
            r = self.client.post(DETECT, {'ip': IP, 'device_id': 'dev', 'channel': 3},
                                 format='json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(seen['channel'], 3)

    def test_detect_endpoint_survives_a_junk_channel(self):
        seen = {}

        def fake_detect(ip, device_id, password='', channel=1):
            seen['channel'] = channel
            return {'ok': True, 'rtsp_url': 'rtsp://x', 'format': 'dahua', 'attempts': []}

        with patch.object(rtsp_probe, 'detect', side_effect=fake_detect):
            r = self.client.post(DETECT, {'ip': IP, 'device_id': 'dev', 'channel': 'abc'},
                                 format='json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(seen['channel'], 1)

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
        match = 'rtsp://10.0.0.5/stream1'
        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, 'onvif_stream_uri', return_value=None), \
             patch.object(rtsp_probe, '_describe',
                          side_effect=lambda u, *a, **k: 200 if u == match else 404), \
             patch.object(rtsp_probe, '_opens',
                          side_effect=lambda u, *a, **k: u == match):
            r = rtsp_probe.detect('10.0.0.5', 'dev')
        self.assertTrue(r['ok'])
        self.assertEqual(r['rtsp_url'], match)
        self.assertEqual(r['format'], 'generic')

    def test_only_candidates_the_camera_accepted_are_opened(self):
        """The expensive step runs on the shortlist, not the whole list: opening
        every candidate with OpenCV is what used to exhaust the time budget."""
        opened = []

        def fake_opens(url, *a, **k):
            opened.append(url)
            return True

        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, 'onvif_stream_uri', return_value=None), \
             patch.object(rtsp_probe, '_describe',
                          side_effect=lambda u, *a, **k: 200 if '/cam/realmonitor' in u else 404), \
             patch.object(rtsp_probe, '_opens', side_effect=fake_opens):
            r = rtsp_probe.detect('10.0.0.5', 'dev')

        self.assertTrue(r['ok'])
        self.assertEqual(r['format'], 'dahua')
        self.assertEqual(len(opened), 1, f'opened more than the first match: {opened}')
        self.assertTrue(all('/cam/realmonitor' in u for u in opened))

    def test_a_url_the_camera_accepts_but_cannot_be_decoded_is_not_returned(self):
        """Some firmware answers 200 to anything; acceptance is not video."""
        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, 'onvif_stream_uri', return_value=None), \
             patch.object(rtsp_probe, '_describe', return_value=200), \
             patch.object(rtsp_probe, '_opens', return_value=False):
            r = rtsp_probe.detect('10.0.0.5', 'dev', 'pw')
        self.assertFalse(r['ok'])
        self.assertIn('decoded', r['error'])

    def test_admin_credentials_are_reached(self):
        """The bug that made an NVR undetectable: candidates were grouped by
        credential, so every `admin` URL sat behind ten device-ID ones and the
        time budget ran out first. An NVR wants `admin` for RTSP."""
        match = 'rtsp://admin:pw@10.0.0.5/cam/realmonitor?channel=1&subtype=0'
        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, 'onvif_stream_uri', return_value=None), \
             patch.object(rtsp_probe, '_describe',
                          side_effect=lambda u, *a, **k: 200 if u == match else 401), \
             patch.object(rtsp_probe, '_opens',
                          side_effect=lambda u, *a, **k: u == match):
            r = rtsp_probe.detect('10.0.0.5', 'dev', 'pw')
        self.assertTrue(r['ok'], msg=str(r.get('error')))
        self.assertEqual(r['rtsp_url'], match)

    def test_rejected_credentials_are_named_as_the_cause(self):
        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, 'onvif_stream_uri', return_value=None), \
             patch.object(rtsp_probe, '_describe', return_value=401), \
             patch.object(rtsp_probe, '_opens', return_value=False):
            r = rtsp_probe.detect('10.0.0.5', 'dev', 'wrong')
        self.assertFalse(r['ok'])
        self.assertIn('username and password', r['error'])

    def test_attempts_record_the_status_each_candidate_returned(self):
        """An admin whose camera will not connect needs to see what happened,
        not a bare 'detection failed'."""
        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, 'onvif_stream_uri', return_value=None), \
             patch.object(rtsp_probe, '_describe', return_value=404):
            r = rtsp_probe.detect('10.0.0.5', 'dev', 'pw')
        self.assertTrue(all('-> 404' in a for a in r['attempts']))
        self.assertTrue(all('pw' not in a for a in r['attempts']), 'password leaked')

    def test_camera_up_but_no_path_works_still_lets_you_save(self):
        """The blocker this fixes: with an empty URL box and no known path, the
        camera could not be added at all."""
        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, 'onvif_stream_uri', return_value=None), \
             patch.object(rtsp_probe, '_describe', return_value=404), \
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
             patch.object(rtsp_probe, 'onvif_stream_uri', return_value=None), \
             patch.object(rtsp_probe, 'TOTAL_BUDGET_SECONDS', 0), \
             patch.object(rtsp_probe, '_describe', return_value=404) as desc, \
             patch.object(rtsp_probe, '_opens', return_value=False) as opens:
            r = rtsp_probe.detect('10.0.0.5', 'dev', 'pw')
        self.assertFalse(r['ok'])
        desc.assert_not_called()           # budget already spent
        opens.assert_not_called()


class OnvifFirstTests(TestCase):
    """A device that publishes its own stream URL should not be guessed at."""

    def test_onvif_answers_when_the_common_paths_do_not(self):
        """ONVIF runs after a short fast path, not before it: the round trips it
        costs left a connection-limited camera with nothing spare for the RTSP
        URL that actually worked."""
        uri = 'rtsp://192.168.1.9:554/unicast/c1/s1/live'
        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, 'onvif_stream_uri', return_value=uri), \
             patch.object(rtsp_probe, 'PROBE_PACING_SECONDS', 0), \
             patch.object(rtsp_probe, '_opens',
                          side_effect=lambda u, *a, **k: u == uri), \
             patch.object(rtsp_probe, '_describe', return_value=404) as describe:
            r = rtsp_probe.detect('192.168.1.9', 'dev', 'pw')
        self.assertTrue(r['ok'])
        self.assertEqual(r['format'], 'onvif')
        # Only the fast path ran before ONVIF was consulted.
        self.assertEqual(describe.call_count, rtsp_probe.FAST_PATH_CANDIDATES)

    def test_a_common_path_is_found_without_touching_onvif(self):
        match = 'rtsp://admin:pw@10.0.0.5/stream1'
        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, 'PROBE_PACING_SECONDS', 0), \
             patch.object(rtsp_probe, 'onvif_stream_uri') as onvif, \
             patch.object(rtsp_probe, '_describe',
                          side_effect=lambda u, *a, **k: 200 if u == match else 400), \
             patch.object(rtsp_probe, '_opens', side_effect=lambda u, *a, **k: u == match):
            r = rtsp_probe.detect('10.0.0.5', 'dev', 'pw')
        self.assertTrue(r['ok'])
        onvif.assert_not_called()

    def test_onvif_is_skipped_once_the_camera_has_gone_quiet(self):
        """Piling HTTP requests onto a device that stopped answering RTSP only
        pushes it further under."""
        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, 'PROBE_PACING_SECONDS', 0), \
             patch.object(rtsp_probe, 'RECOVERY_PAUSE_SECONDS', 0), \
             patch.object(rtsp_probe, 'onvif_stream_uri') as onvif, \
             patch.object(rtsp_probe, '_describe', return_value=None), \
             patch.object(rtsp_probe, '_opens', return_value=False):
            rtsp_probe.detect('10.0.0.5', 'dev', 'pw')
        onvif.assert_not_called()

    def test_path_guessing_still_runs_when_onvif_is_silent(self):
        match = 'rtsp://admin:pw@10.0.0.5/live/ch1'
        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, 'onvif_stream_uri', return_value=None), \
             patch.object(rtsp_probe, '_describe',
                          side_effect=lambda u, *a, **k: 200 if u == match else 404), \
             patch.object(rtsp_probe, '_opens', side_effect=lambda u, *a, **k: u == match):
            r = rtsp_probe.detect('10.0.0.5', 'dev', 'pw')
        self.assertTrue(r['ok'], msg=str(r.get('error')))
        self.assertEqual(r['rtsp_url'], match)

    def test_an_onvif_url_that_will_not_decode_does_not_win(self):
        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, 'onvif_stream_uri', return_value='rtsp://x/y'), \
             patch.object(rtsp_probe, '_describe', return_value=404), \
             patch.object(rtsp_probe, '_opens', return_value=False):
            r = rtsp_probe.detect('10.0.0.5', 'dev', 'pw')
        self.assertFalse(r['ok'])

    def test_a_broken_onvif_service_cannot_break_detection(self):
        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, 'onvif_stream_uri', side_effect=RuntimeError('boom')), \
             patch.object(rtsp_probe, '_describe', return_value=404), \
             patch.object(rtsp_probe, '_opens', return_value=False):
            r = rtsp_probe.detect('10.0.0.5', 'dev', 'pw')
        self.assertFalse(r['ok'])          # reported, not raised

    def test_credentials_are_injected_into_the_uri_the_device_returns(self):
        out = rtsp_probe._with_credentials('rtsp://10.0.0.5/live', 'admin', 'p@ss')
        self.assertEqual(out, 'rtsp://admin:p%40ss@10.0.0.5/live')

    def test_credentials_already_present_are_left_alone(self):
        url = 'rtsp://admin:pw@10.0.0.5/live'
        self.assertEqual(rtsp_probe._with_credentials(url, 'other', 'x'), url)


class TransportFallbackTests(TestCase):
    """"Nonmatching transport in server reply" — forcing TCP hid working cameras."""

    def test_udp_is_tried_when_tcp_cannot_open(self):
        seen = []

        def fake_once(url, transport, timeout_s):
            seen.append(transport)
            return transport == 'udp'

        with patch.object(rtsp_probe, '_opens_once', side_effect=fake_once):
            self.assertTrue(rtsp_probe._opens('rtsp://10.0.0.5/live'))
        self.assertEqual(seen, ['tcp', 'udp'])

    def test_tcp_wins_when_it_works_and_udp_is_not_tried(self):
        seen = []

        def fake_once(url, transport, timeout_s):
            seen.append(transport)
            return True

        with patch.object(rtsp_probe, '_opens_once', side_effect=fake_once):
            self.assertTrue(rtsp_probe._opens('rtsp://10.0.0.5/live'))
        self.assertEqual(seen, ['tcp'])

    def test_a_hung_open_does_not_outlive_its_wall_clock(self):
        """One camera sat inside FFmpeg for 30 s despite a 4 s option, which on
        its own exhausted the whole probe budget."""
        import time as _t

        def hangs(url, transport, timeout_s):
            _t.sleep(30)
            return True

        started = _t.monotonic()
        with patch.object(rtsp_probe, '_opens_once', side_effect=hangs):
            ok = rtsp_probe._opens('rtsp://10.0.0.5/live', timeout_s=1)
        self.assertFalse(ok)
        self.assertLess(_t.monotonic() - started, 12, 'wall clock not enforced')


class ConnectionLimitedCameraTests(TestCase):
    """A camera that accepts only a handful of connections before going quiet.

    This is a real device: it answered candidate 2 with 200 and had stopped
    replying by candidate 4. Collecting every acceptance and verifying at the
    end meant returning to a URL it would no longer serve, so a camera that had
    already said yes was reported as undetectable.
    """
    MATCH = 'rtsp://admin:pw@10.0.0.5/stream1'

    def _camera(self, budget=3):
        """DESCRIBE answers `budget` times, then falls silent forever."""
        state = {'left': budget}

        def describe(url, *a, **k):
            if state['left'] <= 0:
                return None
            state['left'] -= 1
            return 200 if url == self.MATCH else 400

        return describe, state

    def test_the_accepted_url_is_verified_before_the_camera_gives_out(self):
        describe, state = self._camera(budget=3)
        opened = []

        def opens(url, *a, **k):
            # The camera would refuse this too if it had already gone quiet.
            opened.append(url)
            return state['left'] > 0 and url == self.MATCH

        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, 'onvif_stream_uri', return_value=None), \
             patch.object(rtsp_probe, 'PROBE_PACING_SECONDS', 0), \
             patch.object(rtsp_probe, '_describe', side_effect=describe), \
             patch.object(rtsp_probe, '_opens', side_effect=opens):
            r = rtsp_probe.detect('10.0.0.5', 'dev', 'pw')

        self.assertTrue(r['ok'], msg=str(r.get('error')))
        self.assertEqual(r['rtsp_url'], self.MATCH)

    def test_a_url_that_authenticated_becomes_the_suggestion(self):
        """"Add Anyway" saves the suggestion, so it must be the URL the camera
        accepted — not a vendor-shaped guess it had already refused."""
        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, 'onvif_stream_uri', return_value=None), \
             patch.object(rtsp_probe, 'PROBE_PACING_SECONDS', 0), \
             patch.object(rtsp_probe, 'SLOT_RELEASE_SECONDS', 0), \
             patch.object(rtsp_probe, '_describe',
                          side_effect=lambda u, *a, **k: 200 if u == self.MATCH else 400), \
             patch.object(rtsp_probe, '_opens', return_value=False):
            r = rtsp_probe.detect('10.0.0.5', 'dev', 'pw')
        self.assertFalse(r['ok'])
        self.assertEqual(r['suggestion'], self.MATCH)

    def test_the_probe_connection_is_released_before_the_stream_is_opened(self):
        """Holding the probe socket open is the difference between the stream
        connecting and the camera refusing it."""
        events = []

        def describe(url, *a, **k):
            events.append(('describe', url))
            if k.get('session') is not None:
                k['session'].close = lambda: events.append(('closed', url))
            return 200 if url == self.MATCH else 400

        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, 'onvif_stream_uri', return_value=None), \
             patch.object(rtsp_probe, 'PROBE_PACING_SECONDS', 0), \
             patch.object(rtsp_probe, 'SLOT_RELEASE_SECONDS', 0), \
             patch.object(rtsp_probe, '_describe', side_effect=describe), \
             patch.object(rtsp_probe, '_opens',
                          side_effect=lambda u, *a, **k: events.append(('open', u)) or True):
            rtsp_probe.detect('10.0.0.5', 'dev', 'pw')

        kinds = [e[0] for e in events]
        self.assertIn('closed', kinds, f'probe socket never released: {events}')
        self.assertLess(kinds.index('closed'), kinds.index('open'))

    def test_detection_stops_at_the_first_working_url(self):
        """Every extra candidate is another connection the camera may not have
        to spare, so the sweep must not continue past a confirmed hit."""
        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, 'onvif_stream_uri', return_value=None), \
             patch.object(rtsp_probe, 'PROBE_PACING_SECONDS', 0), \
             patch.object(rtsp_probe, '_describe',
                          side_effect=lambda u, *a, **k: 200 if u == self.MATCH else 400) as desc, \
             patch.object(rtsp_probe, '_opens',
                          side_effect=lambda u, *a, **k: u == self.MATCH):
            r = rtsp_probe.detect('10.0.0.5', 'dev', 'pw')

        self.assertTrue(r['ok'])
        # /stream1 with `admin` is the second candidate; nothing after it should
        # have been touched.
        self.assertEqual(desc.call_count, 2)
        self.assertEqual(len(r['attempts']), 2)

    def test_a_quiet_camera_gets_one_pause_before_being_written_off(self):
        RECOVERY = 0.01          # distinct from the zeroed pacing sleeps
        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, 'onvif_stream_uri', return_value=None), \
             patch.object(rtsp_probe, 'PROBE_PACING_SECONDS', 0), \
             patch.object(rtsp_probe, 'RECOVERY_PAUSE_SECONDS', RECOVERY), \
             patch.object(rtsp_probe, '_describe', return_value=None), \
             patch.object(rtsp_probe, '_opens', return_value=False), \
             patch.object(rtsp_probe.time, 'sleep') as slept:
            rtsp_probe.detect('10.0.0.5', 'dev', 'pw')
        pauses = [c.args[0] for c in slept.call_args_list]
        self.assertEqual(pauses.count(RECOVERY), 1, f'expected one pause, got {pauses}')


class AcceptsAnythingFirmwareTests(TestCase):
    """Firmware that answers 200 to every path it is asked about.

    A real camera did this: with the right credential, /, /h264, /11 and
    /stream1 all came back 200. The search then "found" a generic guess,
    spent its one verification on it, and never reached the camera's own
    documented URL — while reporting the camera as undetectable.
    """
    REAL = 'rtsp://admin:pw@10.0.0.5/cam/realmonitor?channel=1&subtype=0'

    def _always_200(self, url, *a, **k):
        # Only `admin` authenticates, exactly like the camera in question.
        return 200 if url.startswith('rtsp://admin:pw@') else 400

    def test_the_real_url_is_found_by_decoding_when_status_means_nothing(self):
        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, 'onvif_stream_uri', return_value=None), \
             patch.object(rtsp_probe, 'PROBE_PACING_SECONDS', 0), \
             patch.object(rtsp_probe, 'SLOT_RELEASE_SECONDS', 0), \
             patch.object(rtsp_probe, '_describe', side_effect=self._always_200), \
             patch.object(rtsp_probe, '_opens',
                          side_effect=lambda u, *a, **k: u == self.REAL):
            r = rtsp_probe.detect('10.0.0.5', '6885002562', 'pw')
        self.assertTrue(r['ok'], msg=str(r.get('error')))
        self.assertEqual(r['rtsp_url'], self.REAL)

    def test_only_the_credential_that_authenticated_is_decoded(self):
        """Decoding is the expensive step and these cameras allow few
        connections — spending them on a rejected username wastes the run."""
        opened = []
        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, 'onvif_stream_uri', return_value=None), \
             patch.object(rtsp_probe, 'PROBE_PACING_SECONDS', 0), \
             patch.object(rtsp_probe, 'SLOT_RELEASE_SECONDS', 0), \
             patch.object(rtsp_probe, '_describe', side_effect=self._always_200), \
             patch.object(rtsp_probe, '_opens',
                          side_effect=lambda u, *a, **k: opened.append(u) or False):
            rtsp_probe.detect('10.0.0.5', '6885002562', 'pw')
        self.assertTrue(opened)
        self.assertTrue(all(u.startswith('rtsp://admin:pw@') for u in opened),
                        f'decoded a rejected credential: {opened}')

    def test_the_number_of_blind_decodes_is_capped(self):
        opened = []
        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, 'onvif_stream_uri', return_value=None), \
             patch.object(rtsp_probe, 'PROBE_PACING_SECONDS', 0), \
             patch.object(rtsp_probe, 'SLOT_RELEASE_SECONDS', 0), \
             patch.object(rtsp_probe, '_describe', side_effect=self._always_200), \
             patch.object(rtsp_probe, '_opens',
                          side_effect=lambda u, *a, **k: opened.append(u) or False):
            rtsp_probe.detect('10.0.0.5', '6885002562', 'pw')
        self.assertLessEqual(len(opened), rtsp_probe.BLIND_DECODE_LIMIT)

    def test_the_vendor_path_is_decoded_before_the_generic_shortcut(self):
        """/stream1 is a guess; /cam/realmonitor is what the unit documents."""
        opened = []
        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, 'onvif_stream_uri', return_value=None), \
             patch.object(rtsp_probe, 'PROBE_PACING_SECONDS', 0), \
             patch.object(rtsp_probe, 'SLOT_RELEASE_SECONDS', 0), \
             patch.object(rtsp_probe, '_describe', side_effect=self._always_200), \
             patch.object(rtsp_probe, '_opens',
                          side_effect=lambda u, *a, **k: opened.append(u) or False):
            rtsp_probe.detect('10.0.0.5', '6885002562', 'pw')
        self.assertIn('/cam/realmonitor', opened[0])

    def test_the_failure_suggestion_is_the_most_likely_url(self):
        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, 'onvif_stream_uri', return_value=None), \
             patch.object(rtsp_probe, 'PROBE_PACING_SECONDS', 0), \
             patch.object(rtsp_probe, 'SLOT_RELEASE_SECONDS', 0), \
             patch.object(rtsp_probe, '_describe', side_effect=self._always_200), \
             patch.object(rtsp_probe, '_opens', return_value=False):
            r = rtsp_probe.detect('10.0.0.5', '6885002562', 'pw')
        self.assertFalse(r['ok'])
        self.assertEqual(r['suggestion'], self.REAL)
        self.assertIn('accepts any stream address', r['error'])

    def test_a_well_behaved_camera_still_uses_its_status_codes(self):
        """The control probe must not change how an honest camera is handled."""
        match = 'rtsp://admin:pw@10.0.0.5/stream1'

        def describe(url, *a, **k):
            if rtsp_probe.BOGUS_PATH in url:
                return 404              # honest: no such path
            return 200 if url == match else 404

        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, 'onvif_stream_uri', return_value=None), \
             patch.object(rtsp_probe, 'PROBE_PACING_SECONDS', 0), \
             patch.object(rtsp_probe, 'SLOT_RELEASE_SECONDS', 0), \
             patch.object(rtsp_probe, '_describe', side_effect=describe), \
             patch.object(rtsp_probe, '_opens', side_effect=lambda u, *a, **k: u == match):
            r = rtsp_probe.detect('10.0.0.5', 'dev', 'pw')
        self.assertTrue(r['ok'], msg=str(r.get('error')))
        self.assertEqual(r['rtsp_url'], match)


class PacingTests(TestCase):
    def test_probing_stops_once_the_device_goes_quiet(self):
        """A camera that stops accepting connections must not be hammered for
        another fifty candidates — and the report must say what happened."""
        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, 'onvif_stream_uri', return_value=None), \
             patch.object(rtsp_probe, 'PROBE_PACING_SECONDS', 0), \
             patch.object(rtsp_probe, '_describe', return_value=None) as describe, \
             patch.object(rtsp_probe, '_opens', return_value=False):
            r = rtsp_probe.detect('10.0.0.5', 'dev', 'pw')
        self.assertEqual(describe.call_count, rtsp_probe.DEAD_STREAK_LIMIT)
        self.assertIn('stopped answering', r['error'])


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
        ends = lambda u, *a, **k: u.endswith('/stream1')          # noqa: E731
        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, 'onvif_stream_uri', return_value=None), \
             patch.object(rtsp_probe, '_describe',
                          side_effect=lambda u, *a, **k: 200 if ends(u) else 404), \
             patch.object(rtsp_probe, '_opens', side_effect=ends):
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

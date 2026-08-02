"""The RTSP DESCRIBE handshake, against a real socket.

Detection lives or dies on this: an NVR challenges with Digest auth, and a
wrong response is indistinguishable from a wrong password — the camera just
says 401 and the admin is told their credentials are bad. So the handshake is
tested against a server that actually verifies the digest rather than against a
mock that agrees with whatever we send.
"""
import hashlib
import socket
import threading

from django.test import SimpleTestCase

from vehicles import rtsp_probe

REALM = 'IPCamera'
NONCE = '1a2b3c4d5e6f'


class _FakeRtspServer:
    """Speaks just enough RTSP to answer DESCRIBE.

    scheme: 'digest' | 'basic' | 'none'
    """
    def __init__(self, scheme='digest', user='admin', password='pw',
                 valid_paths=('/cam/realmonitor',)):
        self.scheme, self.user, self.password = scheme, user, password
        self.valid_paths = valid_paths
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def stop(self):
        self._stop.set()
        try: self.sock.close()
        except Exception: pass

    # ── request handling ────────────────────────────────────────────────
    def _expected_digest(self, target):
        md5 = lambda s: hashlib.md5(s.encode()).hexdigest()      # noqa: E731
        ha1 = md5(f'{self.user}:{REALM}:{self.password}')
        ha2 = md5(f'DESCRIBE:{target}')
        return md5(f'{ha1}:{NONCE}:{ha2}')

    def _reply(self, req):
        first = req.split('\r\n', 1)[0]
        parts = first.split()
        target = parts[1] if len(parts) > 1 else ''
        cseq = '1'
        for line in req.split('\r\n'):
            if line.lower().startswith('cseq:'):
                cseq = line.split(':', 1)[1].strip()

        path_ok = any(p in target for p in self.valid_paths)

        auth = ''
        for line in req.split('\r\n'):
            if line.lower().startswith('authorization:'):
                auth = line.split(':', 1)[1].strip()

        if self.scheme != 'none':
            if not auth:
                chal = (f'Digest realm="{REALM}", nonce="{NONCE}"'
                        if self.scheme == 'digest' else f'Basic realm="{REALM}"')
                return (f'RTSP/1.0 401 Unauthorized\r\nCSeq: {cseq}\r\n'
                        f'WWW-Authenticate: {chal}\r\n\r\n')
            if self.scheme == 'digest':
                got = dict(__import__('re').findall(r'(\w+)="([^"]*)"', auth))
                if got.get('response') != self._expected_digest(got.get('uri', target)):
                    return f'RTSP/1.0 401 Unauthorized\r\nCSeq: {cseq}\r\n\r\n'
            else:
                import base64
                want = base64.b64encode(f'{self.user}:{self.password}'.encode()).decode()
                if auth.split()[-1] != want:
                    return f'RTSP/1.0 401 Unauthorized\r\nCSeq: {cseq}\r\n\r\n'

        if not path_ok:
            return f'RTSP/1.0 404 Not Found\r\nCSeq: {cseq}\r\n\r\n'
        body = 'v=0\r\nm=video 0 RTP/AVP 96\r\n'
        return (f'RTSP/1.0 200 OK\r\nCSeq: {cseq}\r\n'
                f'Content-Type: application/sdp\r\n'
                f'Content-Length: {len(body)}\r\n\r\n{body}')

    def _serve(self):
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except Exception:
                return
            threading.Thread(target=self._session, args=(conn,), daemon=True).start()

    def _session(self, conn):
        with conn:
            conn.settimeout(3)
            try:
                while not self._stop.is_set():
                    data = conn.recv(4096)
                    if not data:
                        return
                    conn.sendall(self._reply(data.decode('utf-8', 'replace')).encode())
            except Exception:
                return


class DescribeTests(SimpleTestCase):
    def _url(self, srv, creds='admin:pw@', path='/cam/realmonitor?channel=1&subtype=0'):
        return f'rtsp://{creds}127.0.0.1:{srv.port}{path}'

    def test_digest_challenge_is_answered_correctly(self):
        """An NVR's 401 must turn into a 200, not be reported as a bad password."""
        srv = _FakeRtspServer(scheme='digest')
        self.addCleanup(srv.stop)
        self.assertEqual(rtsp_probe._describe(self._url(srv)), 200)

    def test_basic_challenge_is_answered_correctly(self):
        srv = _FakeRtspServer(scheme='basic')
        self.addCleanup(srv.stop)
        self.assertEqual(rtsp_probe._describe(self._url(srv)), 200)

    def test_a_wrong_password_still_reports_401(self):
        srv = _FakeRtspServer(scheme='digest')
        self.addCleanup(srv.stop)
        self.assertEqual(rtsp_probe._describe(self._url(srv, 'admin:nope@')), 401)

    def test_an_unknown_path_reports_404(self):
        srv = _FakeRtspServer(scheme='digest')
        self.addCleanup(srv.stop)
        self.assertEqual(rtsp_probe._describe(self._url(srv, path='/nope')), 404)

    def test_an_open_camera_needs_no_credentials(self):
        srv = _FakeRtspServer(scheme='none')
        self.addCleanup(srv.stop)
        self.assertEqual(rtsp_probe._describe(self._url(srv, creds='')), 200)

    def test_a_password_with_url_special_characters_survives(self):
        """The password is percent-encoded inside the URL; the digest has to be
        computed from the decoded value or every such login fails."""
        from urllib.parse import quote
        srv = _FakeRtspServer(scheme='digest', password='p@ss/word')
        self.addCleanup(srv.stop)
        creds = f"admin:{quote('p@ss/word', safe='')}@"
        self.assertEqual(rtsp_probe._describe(self._url(srv, creds)), 200)

    def test_many_describes_share_one_connection(self):
        """A camera that allows only a couple of connections cannot spare one
        per candidate — the sweep must not spend its whole allowance asking."""
        srv = _FakeRtspServer(scheme='digest')
        self.addCleanup(srv.stop)
        session = rtsp_probe._RtspSession('127.0.0.1', srv.port, 3)
        self.addCleanup(session.close)
        codes = [rtsp_probe._describe(self._url(srv), session=session)
                 for _ in range(5)]
        self.assertEqual(codes, [200] * 5)

    def test_a_reused_connection_stays_in_step_after_a_response_with_a_body(self):
        """The SDP body has to be drained, or the next reply is read as garbage."""
        srv = _FakeRtspServer(scheme='none')      # 200 + SDP body, no auth
        self.addCleanup(srv.stop)
        session = rtsp_probe._RtspSession('127.0.0.1', srv.port, 3)
        self.addCleanup(session.close)
        first  = rtsp_probe._describe(self._url(srv, creds=''), session=session)
        second = rtsp_probe._describe(self._url(srv, creds='', path='/nope'),
                                      session=session)
        self.assertEqual(first, 200)
        self.assertEqual(second, 404)             # not a misread continuation

    def test_a_session_reconnects_when_the_camera_drops_it(self):
        srv = _FakeRtspServer(scheme='digest')
        self.addCleanup(srv.stop)
        session = rtsp_probe._RtspSession('127.0.0.1', srv.port, 3)
        self.addCleanup(session.close)
        self.assertEqual(rtsp_probe._describe(self._url(srv), session=session), 200)
        session.sock.close()                      # camera hangs up mid-sweep
        self.assertEqual(rtsp_probe._describe(self._url(srv), session=session), 200)

    def test_a_dead_port_returns_none_rather_than_raising(self):
        s = socket.socket(); s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]; s.close()
        self.assertIsNone(rtsp_probe._describe(f'rtsp://127.0.0.1:{port}/x', timeout=0.5))


class DetectAgainstAServerTests(SimpleTestCase):
    """detect() end to end over sockets — only the decode step is stubbed."""

    # The server listens on an ephemeral port, so the host carries it: RTSP_PORT
    # is bound as a default argument in is_reachable and patching the constant
    # would not reach it.
    def test_the_working_channel_is_found_behind_a_digest_login(self):
        srv = _FakeRtspServer(scheme='digest', user='admin', password='pw',
                              valid_paths=('/cam/realmonitor?channel=2',))
        self.addCleanup(srv.stop)
        from unittest.mock import patch
        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, 'onvif_stream_uri', return_value=None), \
             patch.object(rtsp_probe, '_opens', return_value=True):
            r = rtsp_probe.detect(f'127.0.0.1:{srv.port}', 'some-device-id',
                                  'pw', channel=2)
        self.assertTrue(r['ok'], msg=str(r.get('error')))
        # The device ID is not a valid RTSP user here; `admin` is — and the
        # probe has to reach it. Before the ordering fix it never did.
        self.assertIn('admin:pw@', r['rtsp_url'])
        self.assertIn('channel=2', r['rtsp_url'])

    def test_a_wrong_password_is_reported_as_a_login_problem(self):
        srv = _FakeRtspServer(scheme='digest', password='right')
        self.addCleanup(srv.stop)
        from unittest.mock import patch
        with patch.object(rtsp_probe, 'is_reachable', return_value=True), \
             patch.object(rtsp_probe, 'onvif_stream_uri', return_value=None), \
             patch.object(rtsp_probe, '_opens', return_value=False):
            r = rtsp_probe.detect(f'127.0.0.1:{srv.port}', 'dev', 'wrong', channel=1)
        self.assertFalse(r['ok'])
        self.assertIn('username and password', r['error'])

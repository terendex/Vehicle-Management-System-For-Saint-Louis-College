"""Slip printing on a Linux campus server: finding the CUPS queue, sending the
RAW job, and turning CUPS' states into the same errors a guard sees on Windows.

The CUPS tools are faked (subprocess.run / shutil.which), so this runs on any
OS and needs no printer and no database.
"""
import os
import subprocess
from unittest.mock import patch

from django.test import SimpleTestCase

from scanning import slip_printer
from scanning.slip_printer import SlipPrinterError, find_printer, send_raw

LPSTAT_V = (b'device for Office_Laser: ipp://10.0.0.5/ipp/print\n'
            b'device for POS58: usb://Unknown/Printer?serial=FM0001\n')


def done(stdout=b'', stderr=b'', code=0):
    return subprocess.CompletedProcess([], code, stdout, stderr)


class FakeCups:
    """Answers each CUPS command from a dict keyed by (tool, first arg), and
    records every call so a test can check what was run."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        answer = self.answers.get((args[0], args[1] if len(args) > 1 else None))
        if callable(answer):
            return answer()
        return answer if answer is not None else done()


def on_linux(env=None, which='/usr/bin/lpstat'):
    """Patches that make slip_printer behave as it would on a Linux server."""
    return [
        patch.object(slip_printer.sys, 'platform', 'linux'),
        patch.object(slip_printer.shutil, 'which', return_value=which),
        patch.dict(os.environ, env or {'SLIP_PRINTER': ''}),
    ]


class LinuxTestCase(SimpleTestCase):
    env = None
    which = '/usr/bin/lpstat'

    def setUp(self):
        for p in on_linux(self.env, self.which):
            p.start()
            self.addCleanup(p.stop)

    def cups(self, answers):
        fake = FakeCups(answers)
        p = patch.object(slip_printer.subprocess, 'run', side_effect=fake)
        p.start()
        self.addCleanup(p.stop)
        return fake


class FindCupsPrinterTests(LinuxTestCase):
    def test_picks_the_thermal_queue_by_its_name_or_uri(self):
        self.cups({('lpstat', '-v'): done(LPSTAT_V)})
        self.assertEqual(find_printer(), 'POS58')

    def test_a_queue_named_otherwise_is_found_by_its_device_uri(self):
        self.cups({('lpstat', '-v'): done(b'device for gate: usb://Unknown/POS-58?serial=1\n')})
        self.assertEqual(find_printer(), 'gate')

    def test_no_thermal_printer_means_the_browser(self):
        self.cups({('lpstat', '-v'): done(b'device for Office_Laser: ipp://10.0.0.5/ipp/print\n')})
        self.assertIsNone(find_printer())

    def test_no_queues_at_all(self):
        self.cups({('lpstat', '-v'): done(stderr=b'lpstat: No destinations added.\n', code=1)})
        self.assertIsNone(find_printer())

    def test_off_never_asks_cups(self):
        fake = self.cups({})
        with patch.dict(os.environ, {'SLIP_PRINTER': 'off'}):
            self.assertIsNone(find_printer())
        self.assertEqual(fake.calls, [])

    def test_named_queue_case_insensitively(self):
        self.cups({('lpstat', '-v'): done(LPSTAT_V)})
        with patch.dict(os.environ, {'SLIP_PRINTER': 'office_laser'}):
            self.assertEqual(find_printer(), 'Office_Laser')

    def test_a_device_path_is_used_directly_even_when_missing(self):
        """Unplugged, the usblp node disappears. A named device must then fail
        loudly in _send_device, not fall back to the browser unnoticed."""
        fake = self.cups({})
        with patch.dict(os.environ, {'SLIP_PRINTER': '/dev/usb/lp0'}):
            self.assertEqual(find_printer(), '/dev/usb/lp0')
        self.assertEqual(fake.calls, [])

    def test_stopped_cups_is_reported_only_when_a_printer_is_named(self):
        down = done(stderr=b'lpstat: Scheduler is not running.\n', code=1)
        self.cups({('lpstat', '-v'): down})
        self.assertIsNone(find_printer())               # could be any Linux box
        with patch.dict(os.environ, {'SLIP_PRINTER': 'POS58'}):
            with self.assertRaisesMessage(SlipPrinterError, 'CUPS print service is stopped'):
                find_printer()


class NoCupsTests(LinuxTestCase):
    which = None                                         # Railway: no lpstat at all

    def test_no_cups_installed_means_the_browser(self):
        self.assertIsNone(find_printer())


class SendCupsTests(LinuxTestCase):
    REQUEST = done(b'request id is POS58-12 (1 file(s))\n')

    def setUp(self):
        super().setUp()
        p = patch.object(slip_printer.time, 'sleep')
        p.start()
        self.addCleanup(p.stop)

    def test_sends_raw_and_returns_once_the_job_leaves_the_queue(self):
        fake = self.cups({('lp', '-d'): self.REQUEST, ('lpstat', '-o'): done(b'')})
        send_raw('POS58', b'\x1b@slip', doc_name='Visitor Slip ABC123')
        self.assertEqual(fake.calls[0], ['lp', '-d', 'POS58', '-o', 'raw', '-t', 'Visitor Slip ABC123'])

    def test_a_disabled_queue_cancels_the_job_reenables_it_and_says_so(self):
        """CUPS never restarts a queue it stopped, so without the cupsenable
        every later slip would fail too, long after the paper was changed."""
        fake = self.cups({
            ('lp', '-d'): self.REQUEST,
            ('lpstat', '-o'): done(b'POS58-12  gate  1024  Fri 10 Oct 2026\n'),
            ('lpstat', '-p'): done(b'printer POS58 disabled since Fri 10 Oct 2026 -\n\tPaper out\n'),
        })
        with self.assertRaisesMessage(SlipPrinterError, 'offline or out of paper'):
            send_raw('POS58', b'x')
        self.assertIn(['cancel', 'POS58-12'], fake.calls)
        self.assertIn(['cupsenable', 'POS58'], fake.calls)

    def test_an_unplugged_printer_is_reported_after_the_wait(self):
        fake = self.cups({
            ('lp', '-d'): self.REQUEST,
            ('lpstat', '-o'): done(b'POS58-12  gate  1024  Fri 10 Oct 2026\n'),
            ('lpstat', '-p'): done(b'printer POS58 now printing POS58-12.  enabled since Fri -\n'
                                   b'\tWaiting for printer to become available.\n'),
        })
        with self.assertRaisesMessage(SlipPrinterError, 'not connected'):
            send_raw('POS58', b'x', wait_seconds=0)
        self.assertIn(['cancel', 'POS58-12'], fake.calls)

    def test_a_slow_job_with_no_error_is_left_to_finish(self):
        fake = self.cups({
            ('lp', '-d'): self.REQUEST,
            ('lpstat', '-o'): done(b'POS58-12  gate  1024  Fri 10 Oct 2026\n'),
            ('lpstat', '-p'): done(b'printer POS58 now printing POS58-12.  enabled since Fri -\n'),
        })
        send_raw('POS58', b'x', wait_seconds=0)
        self.assertNotIn('cancel', [c[0] for c in fake.calls])

    def test_stopped_cups(self):
        self.cups({('lp', '-d'): done(stderr=b'lp: Scheduler is not running.\n', code=1)})
        with self.assertRaisesMessage(SlipPrinterError, 'sudo systemctl start cups'):
            send_raw('POS58', b'x')

    def test_a_refused_job_quotes_cups(self):
        self.cups({('lp', '-d'): done(stderr=b'lp: The printer or class does not exist.\n', code=1)})
        with self.assertRaisesMessage(SlipPrinterError, 'does not exist'):
            send_raw('POS58', b'x')


class SendDeviceTests(LinuxTestCase):
    """The device file is written non-blocking under a deadline; os and
    select are faked, so this runs on Windows too."""

    def setUp(self):
        super().setUp()
        self.written = bytearray()
        self.closed = []
        for name, fake in (('open', lambda path, flags: 42),
                           ('close', self.closed.append)):
            p = patch.object(slip_printer.os, name, side_effect=fake)
            p.start()
            self.addCleanup(p.stop)

    def write_in_chunks(self, fd, data):
        chunk = bytes(data[:4])               # the printer takes a few bytes at a time
        self.written += chunk
        return len(chunk)

    def test_writes_every_byte_to_the_device_file_not_cups(self):
        with patch.object(slip_printer.os, 'write', side_effect=self.write_in_chunks), \
             patch('select.select', return_value=([], [42], [])), \
             patch.object(slip_printer, '_send_cups') as cups:
            send_raw('/dev/usb/lp0', b'\x1b@slip-bytes')
        self.assertEqual(bytes(self.written), b'\x1b@slip-bytes')
        self.assertEqual(self.closed, [42])
        cups.assert_not_called()

    def test_a_printer_that_stops_taking_bytes_times_out_instead_of_hanging(self):
        with patch('select.select', return_value=([], [], [])) as sel:
            with self.assertRaisesMessage(SlipPrinterError, 'stopped taking the slip'):
                slip_printer._send_device('/dev/usb/lp0', b'x', timeout=0.05)
        self.assertTrue(sel.called)
        self.assertEqual(self.closed, [42])         # never leaks the descriptor

    def test_permission_denied_names_the_lp_group(self):
        with patch.object(slip_printer.os, 'open', side_effect=PermissionError(13, 'Permission denied')):
            with self.assertRaisesMessage(SlipPrinterError, '"lp" group'):
                send_raw('/dev/usb/lp0', b'x')

    def test_a_missing_device_says_to_check_the_printer(self):
        with patch.object(slip_printer.os, 'open', side_effect=FileNotFoundError(2, 'No such file or directory')):
            with self.assertRaisesMessage(SlipPrinterError, 'plugged in'):
                send_raw('/dev/usb/lp0', b'x')

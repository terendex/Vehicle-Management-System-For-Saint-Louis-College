"""Direct printing of gate slips — visitor passes and no-plate entries, see
scanning/slips.py — on the gate's thermal printer.

The slip used to go through the browser's print dialog, which made every gate
PC depend on three Windows settings nobody would remember to repeat: a custom
48x130mm paper form, the driver's FeedLine option, and the printer's port.
Printing from the server removes all three. The slip is rendered here as one
384-dot-wide bitmap and handed to the Windows spooler as RAW ESC/POS, so the
printer driver's paper sizes, margins and scaling never touch it - and a guard
issuing a pass from a phone still gets the slip out of the printer at the gate.

A Linux campus server sends the same bytes through CUPS instead: a raw queue
(`lp -o raw`, set up by scripts/linux/slip-printer-setup.sh), or straight to
the USB device file when SLIP_PRINTER names one.

Only the campus deployment has a printer. On Railway (no CUPS) or a PC with no
thermal printer installed, `find_printer()` returns None and the view answers
503, which tells the frontend to fall back to the browser print dialog.

Settings (backend/.env):
    SLIP_PRINTER=POS58 Printer   use this Windows printer (or CUPS queue) by name
    SLIP_PRINTER=/dev/usb/lp0    Linux: write straight to this device, no CUPS
    SLIP_PRINTER=off             never print from the server (browser dialog)
    (unset)                      pick the installed thermal printer by name/driver
                                 (on Linux: by CUPS queue name or device URI)
"""
import os
import re
import shutil
import subprocess
import sys
import time

from django.conf import settings

# JP-58H / POS58: 58mm roll, 48mm printable at 203dpi = 384 dots = 48 bytes a row.
DOTS_WIDE = 384
# One CSS pixel on the browser slip is 25.4/96 mm; at 203dpi that is ~2.12 dots.
# Sizes below are the browser slip's CSS sizes converted, so both look the same.
PX = 203 / 96

# Matches the installed printer's name OR its driver name.
THERMAL_PATTERN = re.compile(r'pos-?\s?58|jp-?\s?58|58\s?mm|thermal|receipt', re.I)

FONT_DIR = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts')
# Linux has no Courier New. Liberation Mono is drawn to its exact metrics
# (fonts-liberation / fonts-liberation2), so a slip wraps the same lines;
# DejaVu Sans Mono is on nearly every desktop if neither is installed.
_LINUX_FONTS = '/usr/share/fonts/truetype'
LINUX_FONT_FILES = {
    True:  ('liberation/LiberationMono-Bold.ttf', 'liberation2/LiberationMono-Bold.ttf',
            'dejavu/DejaVuSansMono-Bold.ttf'),
    False: ('liberation/LiberationMono-Regular.ttf', 'liberation2/LiberationMono-Regular.ttf',
            'dejavu/DejaVuSansMono.ttf'),
}

# Values of SLIP_PRINTER that mean "never print from the server".
_OFF = ('off', 'none', 'browser', '0', 'false')
# CUPS tools print English only under the C locale, and their output is parsed.
_C_LOCALE = {'LC_ALL': 'C', 'LANG': 'C'}


class SlipPrinterError(Exception):
    """The printer exists but the slip did not print (offline, out of paper...)."""


# ---------------------------------------------------------------------------
#  Finding the printer
# ---------------------------------------------------------------------------
def _installed_printers():
    """[(name, driver)] for every local Windows printer. Read from the registry
    rather than EnumPrinters - it is two lines instead of a struct walk."""
    import winreg
    out = []
    root = r'SYSTEM\CurrentControlSet\Control\Print\Printers'
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root) as key:
            for i in range(winreg.QueryInfoKey(key)[0]):
                name = winreg.EnumKey(key, i)
                try:
                    with winreg.OpenKey(key, name) as pk:
                        driver = winreg.QueryValueEx(pk, 'Printer Driver')[0]
                except OSError:
                    driver = ''
                out.append((name, driver))
    except OSError:
        pass
    return out


def _cups(*args, payload=None, timeout=5):
    """Run a CUPS command-line tool; CompletedProcess, or None when CUPS is not
    installed or did not answer in time."""
    try:
        return subprocess.run(args, input=payload, capture_output=True, timeout=timeout,
                              env={**os.environ, **_C_LOCALE})
    except (OSError, subprocess.SubprocessError):
        return None


def _cups_down(done):
    """True when a CUPS tool failed because the cupsd service is not running."""
    return bool(done) and done.returncode != 0 and b'scheduler' in done.stderr.lower()


def _cups_printers():
    """[(queue, device URI)] for every CUPS queue, or [] with no CUPS.

    The URI stands in for the Windows driver name: a POS58 on USB shows up as
    usb://...POS58... even when the queue was given some other name. Queue
    names cannot contain spaces, so the line splits cleanly."""
    if not shutil.which('lpstat'):
        return []
    done = _cups('lpstat', '-v')
    if _cups_down(done):
        # A stopped cupsd cannot list its queues. With the printer named in
        # SLIP_PRINTER that is worth saying - the Windows "spooler is stopped"
        # case. Without it, this could be any Linux box, so: browser fallback.
        if os.getenv('SLIP_PRINTER', '').strip():
            raise SlipPrinterError('the CUPS print service is stopped. '
                                   'Run "sudo systemctl start cups", then print again')
        return []
    if not done or done.returncode != 0:
        return []
    out = []
    for line in done.stdout.decode(errors='replace').splitlines():
        m = re.match(r'device for (\S+?):\s*(.*)$', line.strip())
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def find_printer():
    """Name of the printer the slip goes to (a Windows printer, a CUPS queue,
    or on Linux a device path), or None to use the browser."""
    wanted = os.getenv('SLIP_PRINTER', '').strip()
    if wanted.lower() in _OFF:
        return None
    if sys.platform == 'win32':
        printers = _installed_printers()
    elif wanted.startswith('/'):
        # A device file, written to directly: no CUPS involved at all. Named
        # on purpose, so returned even when it is missing - an unplugged
        # printer's node disappears, and quietly falling back to the browser
        # would hide that. _send_device then says to check the printer.
        return wanted
    else:
        printers = _cups_printers()
    if wanted:
        return next((n for n, _ in printers if n.lower() == wanted.lower()), None)
    return next((n for n, d in printers if THERMAL_PATTERN.search(n) or THERMAL_PATTERN.search(d)), None)


# ---------------------------------------------------------------------------
#  Rendering
# ---------------------------------------------------------------------------
def _font(size_px, bold=False):
    from PIL import ImageFont
    size = round(size_px * PX)
    names = ['courbd.ttf', 'consolab.ttf'] if bold else ['cour.ttf', 'consola.ttf']
    paths = [os.path.join(FONT_DIR, n) for n in names]
    paths += [os.path.join(_LINUX_FONTS, f) for f in LINUX_FONT_FILES[bold]]
    for path in paths:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def _wrap(draw, text, font, width):
    """Greedy word wrap; a single word longer than the line is split by letter."""
    lines, line = [], ''
    for word in str(text).split():
        trial = f'{line} {word}'.strip()
        if draw.textlength(trial, font=font) <= width:
            line = trial
            continue
        if line:
            lines.append(line)
        line = ''
        while draw.textlength(word, font=font) > width:
            cut = len(word)
            while cut > 1 and draw.textlength(word[:cut], font=font) > width:
                cut -= 1
            lines.append(word[:cut])
            word = word[cut:]
        line = word
    if line or not lines:
        lines.append(line)
    return lines


def render_slip(slip, reprint=False):
    """A slip (scanning.slips.slip_data) as a 1-bit PIL image, DOTS_WIDE wide.
    Mirrors the browser slip in SecurityEntryManagement.jsx so a guard sees
    the same thing either way."""
    import qrcode
    from PIL import Image, ImageDraw, ImageOps

    pad = round(1.5 * 203 / 25.4)          # 1.5mm side padding
    inner = DOTS_WIDE - 2 * pad
    canvas = Image.new('L', (DOTS_WIDE, 3000), 255)
    draw = ImageDraw.Draw(canvas)
    y = round(1 * 203 / 25.4)

    def centered(text, font, gap_after):
        nonlocal y
        for ln in _wrap(draw, text, font, inner):
            w = draw.textlength(ln, font=font)
            draw.text(((DOTS_WIDE - w) / 2, y), ln, font=font, fill=0)
            y += round(font.size * 1.2)
        y += gap_after

    def rule():
        nonlocal y
        y += 6
        for x in range(pad, DOTS_WIDE - pad, 8):
            draw.rectangle([x, y, x + 4, y + 1], fill=0)
        y += 8

    def row(label, value, font, label_font=None):
        nonlocal y
        label_font = label_font or font
        lw = draw.textlength(label, font=label_font) + 10
        # Label sits on the value's first baseline when the value is larger.
        draw.text((pad, y + font.size - label_font.size), label, font=label_font, fill=0)
        for ln in _wrap(draw, value, font, inner - lw):
            draw.text((DOTS_WIDE - pad - draw.textlength(ln, font=font), y), ln, font=font, fill=0)
            y += round(font.size * 1.25)
        y += 3

    # Seals. Dithered here, before the text, so the final threshold leaves them alone.
    seal = round(9 * 203 / 25.4)
    logos = [os.path.join(settings.BASE_DIR, 'report_assets', f) for f in ('slclogo.jpg', 'cdsologo.jpg')]
    logos = [p for p in logos if os.path.exists(p)]
    if logos:
        gap = 12
        x = (DOTS_WIDE - (len(logos) * seal + (len(logos) - 1) * gap)) // 2
        for path in logos:
            img = ImageOps.autocontrast(Image.open(path).convert('L'), cutoff=2)
            img.thumbnail((seal, seal), Image.LANCZOS)
            img = img.convert('1').convert('L')
            canvas.paste(img, (x + (seal - img.width) // 2, y + (seal - img.height) // 2))
            x += seal + gap
        y += seal + 6

    centered('SAINT LOUIS COLLEGE', _font(12, bold=True), 2)
    small = _font(8.5)
    centered('Campus Development and Sustainability Office', small, 4)
    centered('Smart Parking and Vehicle Verification System', small, 6)
    centered(slip['title'], _font(13, bold=True), 2 if reprint else 8)
    if reprint:
        # So a replacement for a torn slip is never mistaken for a second pass.
        centered('** REPRINT **', _font(10, bold=True), 8)

    # Plate (or NP- reference), boxed.
    plate_font = _font(18, bold=True)
    plate = slip['headline']
    track = 4                                          # the browser slip's letter-spacing
    while plate_font.size > 20 and (draw.textlength(plate, font=plate_font)
                                    + track * (len(plate) - 1)) > inner - 16:
        plate_font = plate_font.font_variant(size=plate_font.size - 2)
    box_h = round(plate_font.size * 1.5)
    draw.rectangle([pad, y, DOTS_WIDE - pad - 1, y + box_h], outline=0, width=4)
    px = (DOTS_WIDE - (draw.textlength(plate, font=plate_font) + track * (len(plate) - 1))) / 2
    for ch in plate:
        draw.text((px, y + (box_h - plate_font.size) / 2 - 2), ch, font=plate_font, fill=0)
        px += draw.textlength(ch, font=plate_font) + track
    y += box_h + 6

    # Rows marked important in slips.py (who, and until when) are what a guard
    # reads at a glance: bold, and a size up from the rest.
    body, key_label, key_value = _font(10), _font(10, bold=True), _font(12, bold=True)
    for section in slip['sections']:
        rule()
        for label, value, *flag in section:
            if flag and flag[0]:
                row(f'{label}:', str(value), key_value, key_label)
            else:
                row(f'{label}:', str(value), body)
    rule()

    # A walk-in visitor's write-in form (slips.VISITOR_FORM): each label on its
    # own line over solid rules tall enough to write on, so the visitor fills
    # it in on campus instead of at the barrier.
    if slip.get('form'):
        centered('VISITOR TO FILL IN', _font(10, bold=True), 4)
        for label, lines in slip['form']:
            draw.text((pad, y), f'{label}:', font=body, fill=0)
            y += round(body.size * 1.25)
            for _ in range(lines):
                y += 44                              # ~5.5mm of writing room — room for a pen
                draw.rectangle([pad, y, DOTS_WIDE - pad - 1, y + 1], fill=0)
                y += 4
            y += 6
        rule()

    # The visited office's signature or stamp (slips.VISITOR_SIGNATURE): a
    # labelled box ~15mm tall, wide enough for a rubber stamp.
    if slip.get('signature'):
        centered(slip['signature'].upper(), _font(10, bold=True), 4)
        box = round(15 * 203 / 25.4)
        draw.rectangle([pad, y, DOTS_WIDE - pad - 1, y + box], outline=0, width=2)
        y += box + 4
        rule()

    # QR, 32mm square, drawn module by module so every module is whole dots.
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=0)
    qr.add_data(slip.get('qr') or slip['code'])
    qr.make(fit=True)
    matrix = qr.get_matrix()
    module = max(1, round(32 * 203 / 25.4) // len(matrix))
    side = module * len(matrix)
    qx, y = (DOTS_WIDE - side) // 2, y + 10
    for r, cells in enumerate(matrix):
        for c, on in enumerate(cells):
            if on:
                draw.rectangle([qx + c * module, y + r * module,
                                qx + (c + 1) * module - 1, y + (r + 1) * module - 1], fill=0)
    y += side + 10

    from .slips import ENTRY_FOOTER
    warn = _font(10, bold=True)
    footer = slip.get('footer') or ENTRY_FOOTER
    for i, line in enumerate(footer):
        centered(line, warn, 8 if i == len(footer) - 1 else 3)
    centered('Unauthorized possession is subject to penalty.', _font(8), 0)

    canvas = canvas.crop((0, 0, DOTS_WIDE, y + 4))
    return canvas.point(lambda p: 0 if p < 160 else 255).convert('1')


def to_escpos(image, feed_lines=4):
    """ESC/POS bytes for a 1-bit image: GS v 0 raster in 24-row bands - the
    same command and band height the POS58 driver itself sends - then a feed
    so the last lines clear the tear bar (the driver's FeedLine does not apply
    to RAW jobs, which bypass it)."""
    width_bytes = (image.width + 7) // 8
    # PIL '1' images pack 1 = white; ESC/POS wants 1 = burn a dot.
    data = bytes(b ^ 0xFF for b in image.tobytes())
    out = bytearray(b'\x1b\x40')                       # ESC @  initialise
    band = 24
    for top in range(0, image.height, band):
        rows = min(band, image.height - top)
        out += b'\x1d\x76\x30\x00'                     # GS v 0, normal size
        out += width_bytes.to_bytes(2, 'little') + rows.to_bytes(2, 'little')
        out += data[top * width_bytes:(top + rows) * width_bytes]
    out += b'\x1b\x64' + bytes([feed_lines])           # ESC d n  feed n lines
    return bytes(out)


# ---------------------------------------------------------------------------
#  Spooling
# ---------------------------------------------------------------------------
# JOB_STATUS_* flags that mean the slip is not coming out on its own.
_JOB_FAILED = {0x2: 'printer error', 0x20: 'printer is offline', 0x40: 'printer is out of paper',
               0x200: 'printer queue is blocked', 0x400: 'printer needs attention'}
# RPC_S_SERVER_UNAVAILABLE from OpenPrinterW: no spooler to talk to.
_SPOOLER_STOPPED = 1722
# What `lpstat -p` shows under a queue whose USB printer is not there.
_CUPS_NOT_CONNECTED = ('waiting for printer to become available', 'unplugged',
                       'not connected', 'turned off')


def send_raw(printer_name, payload, doc_name='Visitor Slip', wait_seconds=6.0):
    """Send RAW ESC/POS bytes to the printer find_printer() named, through the
    Windows spooler, a CUPS raw queue, or a Linux device file."""
    if sys.platform == 'win32':
        return _send_winspool(printer_name, payload, doc_name, wait_seconds)
    if printer_name.startswith('/'):
        return _send_device(printer_name, payload)
    return _send_cups(printer_name, payload, doc_name, wait_seconds)


# How long a device file may take to accept a whole slip. The printer takes
# bytes about as fast as it prints, so this is a long slip at full speed plus
# room to spare - not the 6s the spoolers get, which only covers queueing.
DEVICE_WRITE_SECONDS = 20


def _send_device(path, payload, timeout=None):
    """Write straight to the printer's device file (SLIP_PRINTER=/dev/usb/lp0).

    Non-blocking with a deadline: usblp blocks a plain write for as long as
    the printer refuses bytes (jammed, out of paper, paused), which would hang
    the guard's request - and every retry after it - with no answer at all."""
    import select
    timeout = DEVICE_WRITE_SECONDS if timeout is None else timeout
    try:
        fd = os.open(path, os.O_WRONLY | getattr(os, 'O_NONBLOCK', 0))
    except PermissionError as exc:
        raise SlipPrinterError(f'the server is not allowed to write to {path}. Add the user '
                               'the server runs as to the "lp" group, then restart it') from exc
    except OSError as exc:
        raise SlipPrinterError(f'cannot open {path} ({exc.strerror or exc}). '
                               'Check the printer is plugged in and switched on') from exc
    try:
        deadline = time.monotonic() + timeout
        rest = memoryview(payload)
        while rest:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SlipPrinterError('printer stopped taking the slip. Check the paper '
                                       'and that it is switched on, then print again')
            if not select.select([], [fd], [], remaining)[1]:
                continue
            try:
                rest = rest[os.write(fd, rest):]
            except BlockingIOError:
                continue
            except OSError as exc:
                raise SlipPrinterError(f'cannot write to {path} ({exc.strerror or exc}). '
                                       'Check the printer is plugged in and switched on') from exc
    finally:
        os.close(fd)


def _send_cups(printer_name, payload, doc_name, wait_seconds):
    """Queue RAW bytes on a CUPS printer and watch the job, the CUPS version of
    _send_winspool. A queue CUPS has disabled (its backend gave up on the
    printer) or a printer it cannot find after the wait is reported, and the
    job is cancelled so the next slip is not stuck behind it."""
    done = _cups('lp', '-d', printer_name, '-o', 'raw', '-t', doc_name, payload=payload, timeout=10)
    if done is None:
        raise SlipPrinterError('the CUPS print service did not answer')
    if _cups_down(done):
        raise SlipPrinterError('the CUPS print service is stopped. '
                               'Run "sudo systemctl start cups", then print again')
    m = re.search(rb'request id is (\S+)', done.stdout)
    if done.returncode != 0 or not m:
        err = done.stderr.decode(errors='replace').strip() or f'exit {done.returncode}'
        raise SlipPrinterError(f'CUPS refused the job ({err})')
    job = m.group(1).decode()

    deadline = time.monotonic() + wait_seconds
    state = ''
    while True:
        queued = _cups('lpstat', '-o', printer_name)
        if queued and queued.returncode == 0 and job.encode() not in queued.stdout.split():
            return  # gone from the queue: printed
        status = _cups('lpstat', '-p', printer_name)
        state = status.stdout.decode(errors='replace').lower() if status else ''
        if 'disabled' in state:
            # The backend gave up and CUPS stopped the queue - and it never
            # restarts it by itself, so every later slip would fail too. Drop
            # this job and re-enable the queue (the server's user is in
            # lpadmin, see install.sh) so the guard's next try reaches the printer.
            _cups('cancel', job)
            _cups('cupsenable', printer_name)
            raise SlipPrinterError('printer is offline or out of paper. Check the cable and paper, '
                                   'then print again')
        if time.monotonic() >= deadline:
            break
        time.sleep(0.25)
    # The usb backend keeps an unplugged printer's job waiting forever, with
    # the queue still enabled. That is the Linux face of the wrong-USB-port case.
    if any(s in state for s in _CUPS_NOT_CONNECTED):
        _cups('cancel', job)
        raise SlipPrinterError('printer is not connected. Check the USB cable and that it is switched on')
    # Still queued with no error after the wait: a slow USB hand-off, not a
    # failure. CUPS will finish it.


def _send_winspool(printer_name, payload, doc_name, wait_seconds):
    """Spool RAW bytes and watch the job until it leaves the queue. Raises
    SlipPrinterError when the spooler flags it - a wrong USB port shows up here
    as 'printer error' within a second or two - and deletes the stuck job so
    the next slip is not queued behind it."""
    import ctypes
    from ctypes import wintypes

    winspool = ctypes.WinDLL('winspool.drv', use_last_error=True)

    class DOC_INFO_1(ctypes.Structure):
        _fields_ = [('pDocName', wintypes.LPWSTR), ('pOutputFile', wintypes.LPWSTR),
                    ('pDatatype', wintypes.LPWSTR)]

    class SYSTEMTIME(ctypes.Structure):
        _fields_ = [(n, wintypes.WORD) for n in
                    ('wYear', 'wMonth', 'wDayOfWeek', 'wDay', 'wHour', 'wMinute', 'wSecond', 'wMilliseconds')]

    class JOB_INFO_1(ctypes.Structure):
        _fields_ = [('JobId', wintypes.DWORD), ('pPrinterName', wintypes.LPWSTR),
                    ('pMachineName', wintypes.LPWSTR), ('pUserName', wintypes.LPWSTR),
                    ('pDocument', wintypes.LPWSTR), ('pDatatype', wintypes.LPWSTR),
                    ('pStatus', wintypes.LPWSTR), ('Status', wintypes.DWORD),
                    ('Priority', wintypes.DWORD), ('Position', wintypes.DWORD),
                    ('TotalPages', wintypes.DWORD), ('PagesPrinted', wintypes.DWORD),
                    ('Submitted', SYSTEMTIME)]

    handle = wintypes.HANDLE()
    if not winspool.OpenPrinterW(printer_name, ctypes.byref(handle), None):
        err = ctypes.get_last_error()
        # 1722 = RPC server unavailable: the Print Spooler service is stopped.
        # find_printer() reads the registry, so the printer still looks
        # installed. Game boosters (Razer Cortex) stop the spooler on purpose.
        if err == _SPOOLER_STOPPED:
            raise SlipPrinterError('the Windows Print Spooler service is stopped. '
                                   'Start "Print Spooler" in Windows Services, then print again')
        raise SlipPrinterError(f'cannot open printer "{printer_name}" (error {err})')
    try:
        doc = DOC_INFO_1(doc_name, None, 'RAW')
        job_id = winspool.StartDocPrinterW(handle, 1, ctypes.byref(doc))
        if not job_id:
            raise SlipPrinterError(f'the spooler refused the job (error {ctypes.get_last_error()})')
        try:
            winspool.StartPagePrinter(handle)
            buf = (ctypes.c_char * len(payload)).from_buffer_copy(payload)
            written = wintypes.DWORD()
            ok = winspool.WritePrinter(handle, buf, len(payload), ctypes.byref(written))
            winspool.EndPagePrinter(handle)
        finally:
            winspool.EndDocPrinter(handle)
        if not ok or written.value != len(payload):
            raise SlipPrinterError('the slip could not be sent to the printer')

        deadline = time.monotonic() + wait_seconds
        needed = wintypes.DWORD()
        while time.monotonic() < deadline:
            raw = ctypes.create_string_buffer(4096)
            if not winspool.GetJobW(handle, job_id, 1, raw, len(raw), ctypes.byref(needed)):
                return  # gone from the queue: printed
            status = ctypes.cast(raw, ctypes.POINTER(JOB_INFO_1)).contents.Status
            for flag, reason in _JOB_FAILED.items():
                if status & flag:
                    winspool.SetJobW(handle, job_id, 0, None, 5)   # JOB_CONTROL_DELETE
                    raise SlipPrinterError(reason)
            time.sleep(0.25)
        # Still queued with no error flag after the wait: a slow USB hand-off,
        # not a failure. The spooler will finish it.
    finally:
        winspool.ClosePrinter(handle)


def print_slip(slip, reprint=False):
    """Print a slip (scanning.slips.slip_data). Returns the printer name, or
    None when this server has no thermal printer (the caller falls back to the
    browser). Raises SlipPrinterError when a printer exists but nothing printed."""
    printer = find_printer()
    if not printer:
        return None
    send_raw(printer, to_escpos(render_slip(slip, reprint=reprint)),
             doc_name=f"{slip['title'].title()} {slip['headline']}")
    return printer

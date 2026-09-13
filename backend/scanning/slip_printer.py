"""Direct printing of the visitor slip on the gate's thermal printer.

The slip used to go through the browser's print dialog, which made every gate
PC depend on three Windows settings nobody would remember to repeat: a custom
48x130mm paper form, the driver's FeedLine option, and the printer's port.
Printing from the server removes all three. The slip is rendered here as one
384-dot-wide bitmap and handed to the Windows spooler as RAW ESC/POS, so the
printer driver's paper sizes, margins and scaling never touch it - and a guard
issuing a pass from a phone still gets the slip out of the printer at the gate.

Only the campus deployment has a printer. On Railway (Linux) or a PC with no
thermal printer installed, `find_printer()` returns None and the view answers
503, which tells the frontend to fall back to the browser print dialog.

Settings (backend/.env):
    SLIP_PRINTER=POS58 Printer   use this Windows printer by name
    SLIP_PRINTER=off             never print from the server (browser dialog)
    (unset)                      pick the installed thermal printer by name/driver
"""
import os
import re
import sys
import time

from django.conf import settings
from django.utils import timezone

# JP-58H / POS58: 58mm roll, 48mm printable at 203dpi = 384 dots = 48 bytes a row.
DOTS_WIDE = 384
# One CSS pixel on the browser slip is 25.4/96 mm; at 203dpi that is ~2.12 dots.
# Sizes below are the browser slip's CSS sizes converted, so both look the same.
PX = 203 / 96

# Matches the installed printer's name OR its driver name.
THERMAL_PATTERN = re.compile(r'pos-?\s?58|jp-?\s?58|58\s?mm|thermal|receipt', re.I)

FONT_DIR = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts')


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


def find_printer():
    """Name of the Windows printer the slip goes to, or None to use the browser."""
    if sys.platform != 'win32':
        return None
    wanted = os.getenv('SLIP_PRINTER', '').strip()
    if wanted.lower() in ('off', 'none', 'browser', '0', 'false'):
        return None
    printers = _installed_printers()
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
    for n in names:
        path = os.path.join(FONT_DIR, n)
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


def _fmt(dt):
    if not dt:
        return '—'
    dt = timezone.localtime(dt)
    return f"{dt.strftime('%b')} {dt.day}, {dt.strftime('%I').lstrip('0')}:{dt.strftime('%M %p')}"


def render_slip(pass_):
    """The slip as a 1-bit PIL image, DOTS_WIDE wide. Mirrors the browser slip
    in SecurityEntryManagement.jsx so a guard sees the same thing either way."""
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

    def row(label, value, font):
        nonlocal y
        lw = draw.textlength(label, font=font) + 10
        draw.text((pad, y), label, font=font, fill=0)
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
    centered('--- VISITOR SLIP ---', _font(10, bold=True), 8)

    # Plate, boxed.
    plate_font = _font(18, bold=True)
    plate = pass_.plate_number
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

    body = _font(10)
    rule()
    row('Office:', pass_.office.name if pass_.office else 'N/A', body)
    row('Purpose:', pass_.purpose or 'N/A', body)
    row('Duration:', f'{pass_.allowed_duration} min', body)
    rule()
    row('Issued:', _fmt(pass_.entered_at), body)
    row('Expires:', _fmt(pass_.expires_at), body)
    row('Guard:', pass_.issued_by.full_name if pass_.issued_by else 'N/A', body)
    rule()

    # QR, 32mm square, drawn module by module so every module is whole dots.
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=0)
    qr.add_data(pass_.qr_payload)
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

    warn = _font(8.5, bold=True)
    centered('SCAN THIS QR AT THE GATE TO EXIT', warn, 2)
    centered('RETURN THIS SLIP UPON EXIT', warn, 8)
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


def send_raw(printer_name, payload, doc_name='Visitor Slip', wait_seconds=6.0):
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
        raise SlipPrinterError(f'cannot open printer "{printer_name}" (error {ctypes.get_last_error()})')
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


def print_visitor_slip(pass_):
    """Print the slip. Returns the printer name, or None when this server has
    no thermal printer (the caller falls back to the browser). Raises
    SlipPrinterError when a printer exists but the slip did not print."""
    printer = find_printer()
    if not printer:
        return None
    send_raw(printer, to_escpos(render_slip(pass_)), doc_name=f'Visitor Slip {pass_.plate_number}')
    return printer

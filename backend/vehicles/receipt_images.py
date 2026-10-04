"""Turns an iPhone HEIC receipt photo into a JPEG before it is stored.

HEIC is the iPhone's default photo format, and the payment page accepts it so
an applicant is never told their own photo is the wrong kind of file. But
Chrome and Edge cannot display HEIC: the CDSO reviewing on a Windows PC saw a
broken thumbnail where the receipt should be, and could not check the OR
number against it. Converting at upload means every later reader (the review
page, any browser, a download) gets an ordinary JPEG.

Never in the way of the payment itself. If the decoder is not installed, or
the file will not decode, the original upload is stored unchanged, exactly as
before this module existed, and the reason is logged.
"""
import io
import logging
import threading

from django.core.files.base import ContentFile

log = logging.getLogger(__name__)

HEIC_EXTENSIONS = ('heic', 'heif')

# Long side of the stored copy. The same cap the payment page applies before
# uploading a JPEG (frontend/src/utils/compressReceipt.js), so a converted HEIC
# ends up the same size as any other receipt and the printed number stays sharp.
MAX_SIDE = 2000
QUALITY = 85

# Decoding takes about 7 bytes of memory per pixel, whatever the file size
# (measured: 12 MP ~86 MB, 48 MP ~344 MB), so the cap is on pixels. The upload
# limit is no guard here: a HEIC of one flat colour can claim 150 MP in a few
# KB, and Pillow's own bomb check only refuses past ~179 MP. 50 MP covers every
# iPhone (48 MP at most). A bigger photo is stored as it came, like any HEIC
# that will not convert.
MAX_PIXELS = 50 * 1024 * 1024

# One conversion at a time per process, so a burst of uploads near a deadline
# queues for a second or two instead of stacking several decodes in memory.
_converting = threading.Lock()


def is_heic(upload):
    name = (getattr(upload, 'name', '') or '').lower()
    return name.rsplit('.', 1)[-1] in HEIC_EXTENSIONS if '.' in name else False


def heic_to_jpeg(upload):
    """A JPEG copy of a HEIC/HEIF upload, or the upload itself.

    Anything that is not HEIC is returned untouched. So is a HEIC that cannot
    be converted, so the caller can always store whatever comes back.
    """
    if not is_heic(upload):
        return upload

    try:
        # pi_heif is the decode-only build of pillow-heif. Imported here, not
        # at module level, so a server without it still starts and still takes
        # payments; it only loses the conversion.
        import pi_heif
        from PIL import Image, ImageOps
    except ImportError:
        log.warning('[receipt] pi_heif is not installed; storing %r as HEIC', upload.name)
        return upload

    try:
        pi_heif.register_heif_opener()
        upload.seek(0)
        # Everything in place and shrunk before any conversion, so no full-size
        # copies are made beyond the decode itself. Done the obvious way
        # (transpose, convert, then shrink, each a new copy) a 48-megapixel
        # photo took ~385 MB to convert; this way ~344 MB, the decode's floor.
        with _converting, Image.open(upload) as img:
            # Opening reads only the header, so this check costs nothing.
            if img.width * img.height > MAX_PIXELS:
                log.warning('[receipt] %r is %dx%d, over the %d-pixel cap; storing it as HEIC',
                            upload.name, img.width, img.height, MAX_PIXELS)
                upload.seek(0)
                return upload
            img.load()
            # A photo taken sideways is stored upright. pi_heif already applies
            # the rotation while decoding and clears the tag, so this is a
            # safety net that normally finds nothing to do.
            ImageOps.exif_transpose(img, in_place=True)
            img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
            if img.mode != 'RGB':
                img = img.convert('RGB')             # HEIC can carry alpha; JPEG cannot
            out = io.BytesIO()
            img.save(out, format='JPEG', quality=QUALITY, optimize=True)
    except Exception:   # noqa: BLE001 — a bad photo must not fail a payment
        log.warning('[receipt] could not convert %r to JPEG; storing it as uploaded',
                    upload.name, exc_info=True)
        upload.seek(0)
        return upload

    stem = upload.name.rsplit('.', 1)[0] or 'receipt'
    return ContentFile(out.getvalue(), name=f'{stem}.jpg')

"""iPhone HEIC receipt photos are stored as JPEG (vehicles/receipt_images.py).

Chrome and Edge cannot display HEIC, so a HEIC receipt showed the CDSO a broken
thumbnail at review time. The payment step converts it on upload, and falls
back to storing the original whenever it cannot, so a payment never fails over
the photo's format.
"""
import base64
import io
import sys
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from PIL import Image

from vehicles.receipt_images import heic_to_jpeg, is_heic
from vehicles.test_registration_payment import PS, PaymentTestCase

# A real 120x80 HEIC: left half blue, right half red, so a test can tell the
# picture survived the conversion the right way round. Made with pillow-heif,
# because pi_heif (what the server installs) can only decode.
SAMPLE_HEIC = base64.b64decode(
    'AAAAHGZ0eXBoZWljAAAAAG1pZjFoZWljbWlhZgAAAVZtZXRhAAAAAAAAACFoZGxyAAAAAAAAAABw'
    'aWN0AAAAAAAAAAAAAAAAAAAAACJpbG9jAAAAAERAAAEAAQAAAAABegABAAAAAAAAAGUAAAAjaWlu'
    'ZgAAAAAAAQAAABVpbmZlAgAAAAABAABodmMxAAAAAA5waXRtAAAAAAABAAAA1mlwcnAAAAC3aXBj'
    'bwAAAHhodmNDAQNwAAAAAAAAAAAAHvAA/P34+AAADwNgAAEAGEABDAH//wNwAAADAJAAAAMAAAMA'
    'HroCQGEAAQArQgEBA3AAAAMAkAAAAwAAAwAeoDyBRZbqSSmubgIaDAgAAAMAyAAAAwAIQGIAAQAH'
    'RAHBcrAiQAAAABNjb2xybmNseAABAA0ABoAAAAAUaXNwZQAAAAAAAAB4AAAAUAAAABBwaXhpAAAA'
    'AAMICAgAAAAXaXBtYQAAAAAAAAABAAEEgQIDBAAAAG1tZGF0AAAAYSgBrwng4SWBs5dV///pm373'
    '+0m3/+ZxlHp3dflTz+vDiQ5pwDBfkgFxXcADLsTZ0L6zvJMShdig8J5kZ8+VX1qlhEErK484QcvK'
    'veQSsYLKeAAAAwAAAwAAmQDOdYAAMuA='
)


# The same picture stored landscape but tagged "rotate 90° clockwise to
# display", the way an iPhone saves a photo taken with the phone held upright.
# Shown correctly it is 80x120, blue on top and red below.
ROTATED_HEIC = base64.b64decode(
    'AAAAHGZ0eXBoZWljAAAAAG1pZjFoZWljbWlhZgAAAaFtZXRhAAAAAAAAACFoZGxyAAAAAAAAAABw'
    'aWN0AAAAAAAAAAAAAAAAAAAAADRpbG9jAAAAAERAAAIAAQAAAAABxQABAAAAAAAAAGUAAgAAAAAC'
    'KgABAAAAAAAAACQAAAA4aWluZgAAAAAAAgAAABVpbmZlAgAAAAABAABodmMxAAAAABVpbmZlAgAA'
    'AQACAABFeGlmAAAAAA5waXRtAAAAAAABAAAA4GlwcnAAAADAaXBjbwAAAHhodmNDAQNwAAAAAAAA'
    'AAAAHvAA/P34+AAADwNgAAEAGEABDAH//wNwAAADAJAAAAMAAAMAHroCQGEAAQArQgEBA3AAAAMA'
    'kAAAAwAAAwAeoDyBRZbqSSmubgIaDAgAAAMAyAAAAwAIQGIAAQAHRAHBcrAiQAAAABNjb2xybmNs'
    'eAABAA0ABoAAAAAUaXNwZQAAAAAAAAB4AAAAUAAAABBwaXhpAAAAAAMICAgAAAAJaXJvdAMAAAAY'
    'aXBtYQAAAAAAAAABAAEFgQIDBIUAAAAaaXJlZgAAAAAAAAAOY2RzYwACAAEAAQAAAJFtZGF0AAAA'
    'YSgBrwng4SWBs5dV///pm373+0m3/+ZxlHp3dflTz+vDiQ5pwDBfkgFxXcADLsTZ0L6zvJMShdig'
    '8J5kZ8+VX1qlhEErK484QcvKveQSsYLKeAAAAwAAAwAAmQDOdYAAMuAAAAAGRXhpZgAATU0AKgAA'
    'AAgAAQESAAMAAAABAAYAAAAAAAA='
)


def heic_file(name='IMG_0001.HEIC', data=SAMPLE_HEIC):
    return SimpleUploadedFile(name, data, content_type='image/heic')


class HeicReceiptUploadTests(PaymentTestCase):

    def stored(self, reg):
        reg.refresh_from_db()
        self.addCleanup(reg.or_receipt_image.delete, save=False)
        return reg.or_receipt_image

    def test_heic_is_stored_as_a_viewable_jpeg(self):
        reg = self.submit()
        res = self.pay(reg, receipt=heic_file())
        self.assertEqual(res.status_code, 200, res.data)

        image = self.stored(reg)
        self.assertTrue(image.name.lower().endswith('.jpg'), image.name)
        with image.open('rb') as fh:
            img = Image.open(io.BytesIO(fh.read()))
            img.load()
        self.assertEqual(img.format, 'JPEG')
        self.assertEqual(img.size, (120, 80))
        # Left blue, right red: same picture, same way round. JPEG is lossy,
        # so the dominant channel is compared rather than exact values.
        left, right = img.getpixel((10, 40)), img.getpixel((110, 40))
        self.assertGreater(left[2], left[0])
        self.assertGreater(right[0], right[2])
        self.assertEqual(reg.payment_status, PS.PAID)

    def test_undecodable_heic_is_kept_and_the_payment_still_lands(self):
        # A photo the decoder chokes on must not cost the applicant the
        # payment: it is stored as uploaded, exactly as before conversion.
        reg = self.submit()
        res = self.pay(reg, receipt=heic_file('broken.heic', b'\x00\x00\x00\x18ftypheic not really'))
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(self.stored(reg).name.endswith('.heic'))
        self.assertEqual(reg.payment_status, PS.PAID)

    def test_without_the_decoder_heic_is_kept(self):
        # A server whose install missed pi-heif still takes payments.
        reg = self.submit()
        with mock.patch.dict(sys.modules, {'pi_heif': None}):   # None in sys.modules makes the import raise
            res = self.pay(reg, receipt=heic_file('IMG_0002.heic'))
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(self.stored(reg).name.endswith('.heic'))


class HeicToJpegUnitTests(SimpleTestCase):

    def test_upright_photo_is_rotated_once_not_twice(self):
        # libheif applies the rotation on decode and pi_heif then resets the
        # EXIF tag, so exif_transpose must find nothing left to do. Rotating
        # twice would hand the CDSO a receipt lying on its side.
        out = heic_to_jpeg(heic_file('IMG_0003.HEIC', ROTATED_HEIC))
        img = Image.open(io.BytesIO(out.read()))
        self.assertEqual(img.size, (80, 120))
        top, bottom = img.getpixel((40, 10)), img.getpixel((40, 110))
        self.assertGreater(top[2], top[0])          # blue on top
        self.assertGreater(bottom[0], bottom[2])    # red below

    def test_stored_jpeg_carries_no_photo_metadata(self):
        # An iPhone photo's EXIF can hold where it was taken. None is copied.
        out = heic_to_jpeg(heic_file('IMG_0004.HEIC', ROTATED_HEIC))
        self.assertEqual(len(Image.open(io.BytesIO(out.read())).getexif()), 0)

    def test_photo_over_the_pixel_cap_is_kept_without_decoding(self):
        # Memory follows pixels, not file size, so an oversized picture is
        # refused from its header alone and stored as it came.
        upload = heic_file('huge.heic')
        with mock.patch('vehicles.receipt_images.MAX_PIXELS', 120 * 80 - 1), \
                mock.patch('pi_heif.as_plugin._LibHeifImageFile.load') as load:   # where the decode happens
            self.assertIs(heic_to_jpeg(upload), upload)
        load.assert_not_called()
        self.assertEqual(upload.tell(), 0)          # rewound, ready to be stored

    def test_other_formats_are_passed_through_untouched(self):
        for name in ('r.jpg', 'r.png', 'r.pdf', 'no_extension'):
            upload = SimpleUploadedFile(name, b'whatever')
            self.assertIs(heic_to_jpeg(upload), upload, name)

    def test_heic_detection_ignores_case(self):
        self.assertTrue(is_heic(heic_file('A.HEIC')))
        self.assertTrue(is_heic(heic_file('a.heif')))
        self.assertFalse(is_heic(heic_file('heic')))   # no extension at all

    def test_large_photo_is_scaled_to_the_receipt_cap(self):
        # An iPhone photo is ~4000px; the stored copy matches the 2000px cap
        # the payment page applies to every other photo.
        big = Image.new('RGB', (4000, 3000), (30, 30, 30))
        with mock.patch('PIL.Image.open') as opened:
            opened.return_value.__enter__.return_value = big
            out = heic_to_jpeg(heic_file())
        self.assertEqual(Image.open(io.BytesIO(out.read())).size, (2000, 1500))

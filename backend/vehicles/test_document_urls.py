"""Registration documents are handed to the reviewer as a URL that resolves.

The bucket keeps the files at object keys; the public host they used to be
served from only answers while the bucket is world-readable, which is both off
by default and the wrong setting for a licence photo. These pin the signing —
and the fallbacks that must not blow up a review page when it cannot happen.
"""
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase
from rest_framework.test import APITestCase

from vehicles.document_urls import signed_document_url
from vehicles.models import VehicleRegistration
from vehicles.serializers import VehicleRegistrationSerializer

User = get_user_model()


class _FakeClient:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def generate_presigned_url(self, op, Params, ExpiresIn):  # noqa: N803 (boto3 spelling)
        if self.fail:
            raise RuntimeError('signing is broken')
        self.calls.append((op, Params, ExpiresIn))
        return f"https://api.example/{Params['Bucket']}/{Params['Key']}?X-Amz-Signature=abc"


def _bucket_field(name='receipts/r.jpg', fail=False, public_url='https://public.example/r.jpg'):
    """A FieldFile lookalike backed by a bucket storage, as django-storages shapes it."""
    client  = _FakeClient(fail=fail)
    storage = SimpleNamespace(
        bucket_name='a-bucket',
        connection=SimpleNamespace(meta=SimpleNamespace(client=client)),
    )
    field = SimpleNamespace(name=name, storage=storage, url=public_url)
    return field, client


class SignedDocumentUrlTests(TestCase):
    def test_missing_file_has_no_url(self):
        self.assertIsNone(signed_document_url(None))

    def test_bucket_file_is_signed_against_the_api_endpoint(self):
        field, client = _bucket_field()
        url = signed_document_url(field)
        self.assertIn('X-Amz-Signature', url)
        self.assertNotIn('public.example', url)
        op, params, expires = client.calls[0]
        self.assertEqual(op, 'get_object')
        self.assertEqual(params, {'Bucket': 'a-bucket', 'Key': 'receipts/r.jpg'})
        self.assertGreater(expires, 0)

    def test_signing_failure_falls_back_to_the_plain_url(self):
        # A broken signer must not blank the reviewer's page.
        field, _ = _bucket_field(fail=True)
        with self.assertLogs('vehicles.document_urls', level='ERROR'):
            self.assertEqual(signed_document_url(field), 'https://public.example/r.jpg')

    def test_local_storage_url_is_made_absolute(self):
        # Local FileSystemStorage has nothing to sign — Django serves the file.
        field = SimpleNamespace(name='receipts/r.jpg', storage=SimpleNamespace(),
                                url='/media/receipts/r.jpg')
        request = RequestFactory().get('/api/vehicles/registrations/')
        self.assertEqual(signed_document_url(field, request),
                         'http://testserver/media/receipts/r.jpg')


class RegistrationDocumentSerializationTests(APITestCase):
    """The serializer must hand out the fetchable URL, not the stored key."""

    def setUp(self):
        self.reg = VehicleRegistration.objects.create(
            registrant_type='student', full_name='DELA CRUZ, JUAN',
            email='doc-url@slc.edu.ph', vehicle_type='Motorcycle',
            plate_number='DOC1234',
            or_receipt_image=SimpleUploadedFile('r.jpg', b'\xff\xd8\xff\xe0jpeg',
                                                content_type='image/jpeg'),
        )
        self.addCleanup(self.reg.or_receipt_image.delete, save=False)

    def test_uploaded_document_serializes_to_a_url_not_a_key(self):
        data = VehicleRegistrationSerializer(self.reg).data
        self.assertIn('/media/receipts/', data['or_receipt_image'])

    def test_absent_documents_serialize_as_null(self):
        data = VehicleRegistrationSerializer(self.reg).data
        self.assertIsNone(data['drivers_license_image'])
        self.assertIsNone(data['assessment_form'])

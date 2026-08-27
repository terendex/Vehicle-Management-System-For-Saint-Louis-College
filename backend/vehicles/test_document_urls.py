"""The document-URL signing client.

These exercise the caching logic, not boto3: a fake storage stands in for S3 so
the suite needs no bucket, no credentials and no network. What matters is that
one client is built per process rather than one per thread, because rebuilding
it costs about a second and that second used to land on a reviewer's page load.
"""
import threading

from django.test import SimpleTestCase

from vehicles import document_warmup
from vehicles.document_urls import (
    _SIGNING_CLIENT_ATTR, _shared_signing_client, signed_document_url,
    warm_signing_client,
)


class FakeClient:
    def __init__(self):
        self.calls = []

    def generate_presigned_url(self, op, Params, ExpiresIn):   # noqa: N803 (boto3 spelling)
        self.calls.append((op, Params, ExpiresIn))
        return f"https://signed.example/{Params['Key']}?expires={ExpiresIn}"


class FakeSession:
    def __init__(self, owner):
        self.owner = owner

    def client(self, *args, **kwargs):
        self.owner.clients_built += 1
        return FakeClient()


class FakeStorage:
    """Stands in for S3Storage, counting how often a client gets built."""

    def __init__(self):
        self.bucket_name  = 'test-bucket'
        self.region_name  = 'auto'
        self.use_ssl      = True
        self.endpoint_url = 'https://example.r2.cloudflarestorage.com'
        self.client_config = None
        self.verify       = None
        self.clients_built = 0

    def _create_session(self):
        return FakeSession(self)


class FakeFieldFile:
    def __init__(self, storage, name='receipts/Dela_Cruz_Juan.jpg'):
        self.storage = storage
        self.name = name
        self.url = f'https://public.example/{name}'

    def __bool__(self):
        return bool(self.name)


class SigningClientCacheTests(SimpleTestCase):

    def setUp(self):
        self.storage = FakeStorage()

    def test_the_client_is_built_once_not_once_per_call(self):
        for _ in range(5):
            signed_document_url(FakeFieldFile(self.storage))
        self.assertEqual(self.storage.clients_built, 1)

    def test_the_client_is_shared_across_threads(self):
        """The regression this guards: django-storages caches its connection in
        a threading.local, so every worker thread rebuilt it — about a second
        each time, on whichever request landed on a fresh thread."""
        seen = []

        def sign():
            signed_document_url(FakeFieldFile(self.storage))
            seen.append(getattr(self.storage, _SIGNING_CLIENT_ATTR))

        threads = [threading.Thread(target=sign) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(self.storage.clients_built, 1)
        self.assertEqual(len(seen), 4)
        self.assertEqual(len({id(c) for c in seen}), 1)   # all the same client

    def test_a_signed_url_carries_the_key_and_a_ttl(self):
        url = signed_document_url(FakeFieldFile(self.storage), ttl=1234)
        self.assertIn('receipts/Dela_Cruz_Juan.jpg', url)
        self.assertIn('expires=1234', url)

    def test_no_file_means_no_url(self):
        self.assertIsNone(signed_document_url(None))
        self.assertEqual(self.storage.clients_built, 0)

    def test_local_storage_falls_back_to_its_own_url(self):
        """FileSystemStorage has no bucket and nothing to sign — Django serves
        those files itself."""
        class LocalStorage:
            pass

        field = FakeFieldFile(LocalStorage())
        self.assertEqual(signed_document_url(field), field.url)

    def test_a_broken_storage_does_not_take_the_page_down(self):
        """A django-storages upgrade could rename the attributes we mirror. The
        page must still render — a document link that falls back to the public
        URL beats a 500 on the review queue."""
        class BrokenStorage(FakeStorage):
            def _create_session(self):
                raise RuntimeError('attribute renamed upstream')

            @property
            def connection(self):
                raise RuntimeError('no connection either')

        field = FakeFieldFile(BrokenStorage())
        self.assertEqual(signed_document_url(field), field.url)

    def test_warm_up_builds_the_client_before_any_request(self):
        client = _shared_signing_client(self.storage)
        self.assertIsNotNone(client)
        self.assertEqual(self.storage.clients_built, 1)
        # A request arriving afterwards reuses it rather than paying again.
        signed_document_url(FakeFieldFile(self.storage))
        self.assertEqual(self.storage.clients_built, 1)

    def test_warm_up_is_a_no_op_without_a_bucket(self):
        """A campus install on local storage has nothing to warm."""
        warm_signing_client()   # default_storage may be either; must not raise


class WarmupStartTests(SimpleTestCase):

    def test_management_commands_do_not_warm_up(self):
        """`manage.py migrate` and the test runner gain nothing from a client
        they will never use."""
        document_warmup._started = False
        with self.settings():
            import sys
            argv, sys.argv = sys.argv, ['manage.py', 'migrate']
            try:
                document_warmup.start()
            finally:
                sys.argv = argv
        self.assertFalse(document_warmup._started)

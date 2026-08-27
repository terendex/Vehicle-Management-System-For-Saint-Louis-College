"""Browser-reachable URLs for the documents attached to a registration.

The bucket's public host is not a sound way to hand these to a reviewer, for
two independent reasons — either one alone would be enough:

  * It only works while the bucket is publicly readable, and R2 ships with the
    public development URL turned off. With it off, every document URL fails to
    load and the reviewer sees a broken thumbnail with no way to reach the file.
  * Making it readable makes it readable to *everyone*. These are a licence
    photo, an assessment form and an official receipt — name, address, student
    number and licence number between them — and the object keys are guessable
    because an upload keeps its filename: `receipts/Dela_Cruz_Juan.jpg` is the
    applicant's own name. A public bucket turns that into an open directory of
    personal documents.

So the URL is signed against the S3 API endpoint instead. It works whether or
not the bucket is public, it is unguessable, and it stops working on its own.

Deliberately scoped to these three fields rather than switched on globally
(`AWS_QUERYSTRING_AUTH`). Violation-evidence photos are embedded in emails by
their URL, and a signed link would go dead in the recipient's inbox hours after
it was sent — a mail already delivered cannot be re-signed.
"""
import logging
import threading
from datetime import timedelta

logger = logging.getLogger(__name__)

# Long enough that a review session started in the morning still has working
# thumbnails after lunch, short enough that a link pasted somewhere it does not
# belong expires by itself. The UI degrades to a filename link on expiry rather
# than showing a broken image, so overrunning it is recoverable by a refresh.
DOCUMENT_URL_TTL = int(timedelta(hours=6).total_seconds())


# django-storages caches its boto3 connection in a `threading.local()`
# (storages/backends/s3.py), so the client is rebuilt from scratch by every
# worker thread that signs its first URL — and building one costs ~1s, almost
# all of it botocore loading its S3 service model. Under an ASGI thread pool
# that is paid again on every fresh thread, which is what made the Vehicle
# Registration page feel slow at random.
#
# Presigning makes no request: it is an HMAC over a URL, computed locally. A
# botocore *client* (unlike a boto3 resource) is documented as thread-safe, so
# one per process is enough and the second-long build happens once instead of
# once per thread. Cached on the storage object itself, so its lifetime is the
# storage's and a replaced storage gets a matching client.
_SIGNING_CLIENT_ATTR = '_slc_shared_signing_client'
_signing_client_lock = threading.Lock()


def _shared_signing_client(storage):
    """One process-wide boto3 S3 client for `storage`, or None if unavailable."""
    client = getattr(storage, _SIGNING_CLIENT_ATTR, None)
    if client is not None:
        return client

    with _signing_client_lock:
        # Another thread may have built it while this one waited for the lock.
        client = getattr(storage, _SIGNING_CLIENT_ATTR, None)
        if client is not None:
            return client

        try:
            # Mirrors how django-storages builds its own connection, so the
            # signature, endpoint and addressing style are identical — only the
            # caching differs.
            session = storage._create_session()
            client = session.client(
                's3',
                region_name=storage.region_name,
                use_ssl=storage.use_ssl,
                endpoint_url=storage.endpoint_url,
                config=storage.client_config,
                verify=storage.verify,
            )
        except Exception:
            # A django-storages upgrade could rename any of those attributes.
            # Falling back to the per-thread connection is only slow, not wrong.
            logger.exception('Could not build a shared signing client; '
                             'falling back to the per-thread connection')
            try:
                client = storage.connection.meta.client
            except Exception:
                logger.exception('No S3 client available for signing')
                return None

        setattr(storage, _SIGNING_CLIENT_ATTR, client)
        return client


def warm_signing_client():
    """Build the signing client ahead of the first request.

    Called from a background thread at server start so the ~1s botocore import
    lands on nobody's page load. Safe to call when there is no S3 storage
    configured — it just does nothing.
    """
    from django.core.files.storage import default_storage

    storage = getattr(default_storage, '_wrapped', default_storage)
    if not getattr(storage, 'bucket_name', None):
        return          # local FileSystemStorage — nothing to sign
    if _shared_signing_client(storage) is not None:
        logger.info('[documents] signing client ready')


def signed_document_url(file_field, request=None, ttl=DOCUMENT_URL_TTL):
    """A URL the reviewer's browser can actually fetch, or None if no file.

    Falls back to the storage's own URL for local FileSystemStorage, which has
    nothing to sign — Django serves those files itself.
    """
    if not file_field:
        return None

    storage = file_field.storage
    bucket  = getattr(storage, 'bucket_name', None)

    if bucket:
        client = _shared_signing_client(storage)
        if client is not None:
            try:
                return client.generate_presigned_url(
                    'get_object',
                    Params={'Bucket': bucket, 'Key': file_field.name},
                    ExpiresIn=ttl,
                )
            except Exception:
                # Signing is not worth failing the whole review page over: log it
                # and hand back the unsigned URL, which at least works if the
                # bucket does happen to be public.
                logger.exception('Could not sign document URL for %s', file_field.name)

    try:
        url = file_field.url
    except Exception:
        logger.exception('No URL available for %s', file_field.name)
        return None
    return request.build_absolute_uri(url) if request is not None else url

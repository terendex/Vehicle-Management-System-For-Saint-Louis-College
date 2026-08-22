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
from datetime import timedelta

logger = logging.getLogger(__name__)

# Long enough that a review session started in the morning still has working
# thumbnails after lunch, short enough that a link pasted somewhere it does not
# belong expires by itself. The UI degrades to a filename link on expiry rather
# than showing a broken image, so overrunning it is recoverable by a refresh.
DOCUMENT_URL_TTL = int(timedelta(hours=6).total_seconds())


def signed_document_url(file_field, request=None, ttl=DOCUMENT_URL_TTL):
    """A URL the reviewer's browser can actually fetch, or None if no file.

    Falls back to the storage's own URL for local FileSystemStorage, which has
    nothing to sign — Django serves those files itself.
    """
    if not file_field:
        return None

    storage = file_field.storage
    bucket  = getattr(storage, 'bucket_name', None)
    conn    = getattr(storage, 'connection', None)

    if bucket and conn is not None:
        try:
            return conn.meta.client.generate_presigned_url(
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

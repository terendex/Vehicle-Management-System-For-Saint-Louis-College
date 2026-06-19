import os
import boto3
from botocore.exceptions import ClientError
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Upload all local media/ files to Cloudflare R2"

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-upload files that already exist in R2 (default: skip existing)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be uploaded without actually uploading",
        )

    def handle(self, *args, **options):
        if not os.getenv("USE_R2", "false").lower() == "true":
            raise CommandError(
                "USE_R2 is not set to true in your .env. Enable it before running this command."
            )

        required = ["R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME", "R2_ACCOUNT_ID"]
        missing = [v for v in required if not os.getenv(v)]
        if missing:
            raise CommandError(f"Missing R2 environment variables: {', '.join(missing)}")

        media_root = settings.MEDIA_ROOT
        if not os.path.isdir(media_root):
            raise CommandError(f"MEDIA_ROOT does not exist: {media_root}")

        bucket   = os.getenv("R2_BUCKET_NAME")
        endpoint = f"https://{os.getenv('R2_ACCOUNT_ID')}.r2.cloudflarestorage.com"
        force    = options["force"]
        dry_run  = options["dry_run"]

        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY"),
            region_name="auto",
        )

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — nothing will be uploaded\n"))

        uploaded = skipped = errors = 0

        for dirpath, _, filenames in os.walk(media_root):
            for filename in filenames:
                local_path = os.path.join(dirpath, filename)
                # R2 key uses forward slashes, relative to MEDIA_ROOT
                r2_key = os.path.relpath(local_path, media_root).replace("\\", "/")

                if not force:
                    try:
                        s3.head_object(Bucket=bucket, Key=r2_key)
                        self.stdout.write(f"  SKIP  {r2_key}")
                        skipped += 1
                        continue
                    except ClientError as e:
                        if e.response["Error"]["Code"] != "404":
                            self.stdout.write(self.style.ERROR(f"  ERROR checking {r2_key}: {e}"))
                            errors += 1
                            continue

                if dry_run:
                    self.stdout.write(f"  WOULD UPLOAD  {r2_key}")
                    uploaded += 1
                    continue

                try:
                    s3.upload_file(local_path, bucket, r2_key, ExtraArgs={"ACL": "public-read"})
                    self.stdout.write(self.style.SUCCESS(f"  OK    {r2_key}"))
                    uploaded += 1
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"  ERROR {r2_key}: {e}"))
                    errors += 1

        self.stdout.write("\n" + "─" * 40)
        if dry_run:
            self.stdout.write(self.style.WARNING(f"Would upload: {uploaded}  |  Would skip: {skipped}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Uploaded: {uploaded}  |  Skipped: {skipped}  |  Errors: {errors}"))

import os
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from django.core.management.base import BaseCommand, CommandError

ML_DIR = Path(__file__).resolve().parent.parent.parent / "ml"
R2_PREFIX = "ml-weights"
# R2 key suffix → local path.  Two independent models:
#   plate_detector   — license plates (keep in sync for teammates)
#   vehicle_detector — unified single-class vehicle model
WEIGHT_FILES = {
    "plate_detector/best.pt":   ML_DIR / "runs" / "plate_detector"   / "weights" / "best.pt",
    "vehicle_detector/best.pt": ML_DIR / "runs" / "vehicle_detector" / "weights" / "best.pt",
    "vehicle_detector/last.pt": ML_DIR / "runs" / "vehicle_detector" / "weights" / "last.pt",
}


def _s3_client():
    required = ["R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME", "R2_ACCOUNT_ID"]
    missing = [v for v in required if not os.getenv(v)]
    if missing:
        raise CommandError(f"Missing R2 environment variables: {', '.join(missing)}")

    return boto3.client(
        "s3",
        endpoint_url=f"https://{os.getenv('R2_ACCOUNT_ID')}.r2.cloudflarestorage.com",
        aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY"),
        region_name="auto",
    )


class Command(BaseCommand):
    help = "Push or pull ML model weights (best.pt / last.pt) to/from Cloudflare R2"

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--push", action="store_true", help="Upload local weights → R2")
        group.add_argument("--pull", action="store_true", help="Download weights from R2 → local")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Overwrite existing files (default: skip if destination already exists)",
        )

    def handle(self, *args, **options):
        s3 = _s3_client()
        bucket = os.getenv("R2_BUCKET_NAME")

        if options["push"]:
            self._push(s3, bucket, options["force"])
        else:
            self._pull(s3, bucket, options["force"])

    # ── push ──────────────────────────────────────────────────────────────────

    def _push(self, s3, bucket, force):
        uploaded = skipped = 0

        for fname, local in WEIGHT_FILES.items():
            if not local.exists():
                self.stdout.write(f"  SKIP  {fname} (not found locally)")
                continue

            r2_key = f"{R2_PREFIX}/{fname}"

            if not force:
                try:
                    s3.head_object(Bucket=bucket, Key=r2_key)
                    self.stdout.write(f"  SKIP  {fname} (already in R2 — use --force to overwrite)")
                    skipped += 1
                    continue
                except ClientError as e:
                    if e.response["Error"]["Code"] != "404":
                        raise

            size_mb = local.stat().st_size / 1024 / 1024
            self.stdout.write(f"  PUSH  {fname} ({size_mb:.0f} MB) → R2 ...")
            s3.upload_file(str(local), bucket, r2_key)
            self.stdout.write(self.style.SUCCESS(f"  OK    {fname}"))
            uploaded += 1

        self.stdout.write(f"\nPushed: {uploaded}  |  Skipped: {skipped}")

    # ── pull ──────────────────────────────────────────────────────────────────

    def _pull(self, s3, bucket, force):
        downloaded = skipped = 0

        for fname, local in WEIGHT_FILES.items():
            r2_key = f"{R2_PREFIX}/{fname}"
            local.parent.mkdir(parents=True, exist_ok=True)

            if local.exists() and not force:
                self.stdout.write(f"  SKIP  {fname} (already exists locally — use --force to overwrite)")
                skipped += 1
                continue

            try:
                self.stdout.write(f"  PULL  {fname} ← R2 ...")
                s3.download_file(bucket, r2_key, str(local))
                size_mb = local.stat().st_size / 1024 / 1024
                self.stdout.write(self.style.SUCCESS(f"  OK    {fname} ({size_mb:.0f} MB)"))
                downloaded += 1
            except ClientError as e:
                code = e.response["Error"]["Code"]
                if code in ("404", "NoSuchKey"):
                    self.stdout.write(f"  SKIP  {fname} (not found in R2)")
                else:
                    self.stdout.write(self.style.ERROR(f"  ERROR {fname}: {e}"))

        self.stdout.write(f"\nDownloaded: {downloaded}  |  Skipped: {skipped}")

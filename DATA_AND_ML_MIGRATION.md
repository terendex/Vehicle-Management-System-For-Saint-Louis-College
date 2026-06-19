# Data & ML Migration Guide

This guide is for whoever handles the database and ML assets for this project. It covers how to migrate the PostgreSQL database to Neon, how to handle image storage on Cloudflare R2, and how to transfer ML model weights between machines.

---

## Table of Contents

- [Database (Neon)](#database-neon)
- [Image Storage (Cloudflare R2)](#image-storage-cloudflare-r2)
- [ML Model Weights](#ml-model-weights)
- [ML Training Dataset](#ml-training-dataset)
- [Full Reset (Start Fresh)](#full-reset-start-fresh)

---

## Database (Neon)

The project uses a shared cloud PostgreSQL database hosted on [Neon](https://neon.tech). Everyone on the team points to the same database via `DATABASE_URL` in their `.env`.

### Check what's in the database

1. Go to [console.neon.tech](https://console.neon.tech)
2. Open the project → click **Tables** (left sidebar) to browse rows visually
3. Or use the **SQL Editor** to run queries:
   ```sql
   SELECT COUNT(*) FROM vehicles_vehicle;
   SELECT COUNT(*) FROM scanning_accesslog;
   SELECT COUNT(*) FROM accounts_user;
   SELECT COUNT(*) FROM scanning_mltrainingsample;
   ```

### Export the database (backup)

Run this from any machine that has PostgreSQL installed and can reach your local DB:

```bash
# Windows — use the full path if pg_dump isn't in PATH
"D:\PostgreSQL\bin\pg_dump.exe" -U postgres -d plate_db -Fc -f plate_db.dump

# Mac/Linux
pg_dump -U postgres -d plate_db -Fc -f plate_db.dump
```

This creates a single binary dump file (`plate_db.dump`).

> `-Fc` = custom format, smaller file size and supports selective restore.

### Import into Neon (restore)

```bash
# Windows
"D:\PostgreSQL\bin\pg_restore.exe" -d "YOUR_NEON_DATABASE_URL" --no-owner --no-privileges plate_db.dump

# Mac/Linux
pg_restore -d "YOUR_NEON_DATABASE_URL" --no-owner --no-privileges plate_db.dump
```

Replace `YOUR_NEON_DATABASE_URL` with the full connection string from your `.env` (`DATABASE_URL`).

> `--no-owner` and `--no-privileges` prevent errors from mismatched PostgreSQL user names between local and Neon.

### Apply pending migrations

After any restore or after pulling new code that includes new migrations:

```bash
cd backend
venv\Scripts\activate
python manage.py migrate
```

### Reset the Neon database (wipe and reimport)

If you need a clean slate on Neon:

1. Go to Neon dashboard → your project → **Settings** → **Reset database** (or drop and recreate via SQL Editor)
2. Re-run the import command above with a fresh dump

---

## Image Storage (Cloudflare R2)

All uploaded images (scan snapshots, ML training samples, owner photos) are stored in Cloudflare R2 when `USE_R2=true` is set in `.env`. Django's `ImageField` handles this automatically — no manual uploads needed during normal operation.

### Check what's in R2

1. Go to [dash.cloudflare.com](https://dash.cloudflare.com)
2. Click **R2 Object Storage** → open your bucket (`slc-entry-management-ml`)
3. You'll see folders like `snapshots/`, `ml_samples/`, `owners/` as the app creates them

### Upload an existing `media/` folder to R2 (one-time migration)

If someone has existing images in a local `backend/media/` folder that need to be moved to R2, use the AWS CLI (which works with R2's S3-compatible API):

**Install AWS CLI:** https://aws.amazon.com/cli/

```bash
# Configure it for R2 (run once)
aws configure set aws_access_key_id YOUR_R2_ACCESS_KEY_ID
aws configure set aws_secret_access_key YOUR_R2_SECRET_ACCESS_KEY
aws configure set default.region auto

# Sync your local media folder to R2
aws s3 sync backend/media/ s3://YOUR_BUCKET_NAME/ --endpoint-url https://YOUR_R2_ACCOUNT_ID.r2.cloudflarestorage.com
```

Replace `YOUR_BUCKET_NAME` and `YOUR_R2_ACCOUNT_ID` with the values from your `.env`.

### Download all R2 images to local (backup)

```bash
aws s3 sync s3://YOUR_BUCKET_NAME/ backend/media/ --endpoint-url https://YOUR_R2_ACCOUNT_ID.r2.cloudflarestorage.com
```

---

## ML Model Weights

The trained model weights (`best.pt`, `last.pt`) live in `backend/scanning/ml/weights/`. These files are **not tracked in Git** (too large) and must be transferred manually between machines.

### Where the weights file lives

```
backend/scanning/ml/weights/best.pt   ← the model the app actually uses
backend/scanning/ml/weights/last.pt   ← only needed to resume training
```

If `weights/best.pt` does not exist, the app falls back to running EasyOCR on the full image (no YOLO plate detection). The app still works, just less accurately.

### How to share weights with a teammate

Option 1 — **Google Drive / USB**: zip the `weights/` folder and share directly.

Option 2 — **Upload to R2 manually** (convenient if already set up):

```bash
aws s3 cp backend/scanning/ml/weights/best.pt s3://YOUR_BUCKET_NAME/ml-weights/best.pt --endpoint-url https://YOUR_R2_ACCOUNT_ID.r2.cloudflarestorage.com
```

To download on another machine:

```bash
aws s3 cp s3://YOUR_BUCKET_NAME/ml-weights/best.pt backend/scanning/ml/weights/best.pt --endpoint-url https://YOUR_R2_ACCOUNT_ID.r2.cloudflarestorage.com
```

### Train a new model from scratch

See the full training guide at [backend/scanning/ml/README.md](../backend/scanning/ml/README.md).

Quick reference:

```bash
cd backend
venv\Scripts\activate

# Download datasets from Roboflow + train in one command
python -m scanning.ml.train --download --api-key YOUR_ROBOFLOW_KEY --epochs 100 --batch 16

# Or train on the existing dataset/ folder
python -m scanning.ml.train --epochs 100 --batch 16

# Resume an interrupted training run
python -m scanning.ml.train --resume
```

After training, `weights/best.pt` is created automatically and the app picks it up on the next restart.

---

## ML Training Dataset

The base training dataset lives in `backend/scanning/ml/dataset/` and **is tracked in Git**:

```
dataset/
├── data.yaml           # YOLO dataset config
├── images/
│   ├── train/          # ~80% of images
│   └── val/            # ~20% of images
└── labels/
    ├── train/          # YOLO annotations (.txt) for train images
    └── val/            # YOLO annotations (.txt) for val images
```

You don't need to re-download it — it comes with the repo via `git pull`.

### Production ML samples (collected from live scans)

When the app scans a plate in production, it saves a cropped image to R2 as an `MLTrainingSample` record (stored in the database). These can be reviewed in the admin dashboard and used to retrain the model incrementally.

To trigger an incremental retrain using accumulated samples:

```bash
# Via the API (from admin dashboard)
POST /api/scan/ml/retrain/

# Or manually via Django management
cd backend
python manage.py shell
>>> from scanning.tasks import retrain_model
>>> retrain_model.delay()
```

---

## Full Reset (Start Fresh)

If you need to wipe everything and start over:

**1. Reset the database on Neon:**
```sql
-- Run in Neon SQL Editor
DROP SCHEMA public CASCADE;
CREATE SCHEMA public;
```
Then re-run migrations:
```bash
python manage.py migrate
```

**2. Clear R2 images:**
Go to the R2 bucket dashboard → select all files → Delete. Or via CLI:
```bash
aws s3 rm s3://YOUR_BUCKET_NAME/ --recursive --endpoint-url https://YOUR_R2_ACCOUNT_ID.r2.cloudflarestorage.com
```

**3. Remove model weights:**
Delete `backend/scanning/ml/weights/best.pt` locally. The app falls back to EasyOCR-only mode until retrained.

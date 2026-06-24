#!/bin/sh
set -e

# Pull ML weights from R2 on first start if not present locally.
# Skipped silently when R2 credentials are not configured — app falls back to EasyOCR.
WEIGHTS_PATH="/app/scanning/ml/weights/best.pt"
if [ ! -f "$WEIGHTS_PATH" ]; then
    if [ -n "$R2_ACCESS_KEY_ID" ] && [ -n "$R2_SECRET_ACCESS_KEY" ] && \
       [ -n "$R2_BUCKET_NAME" ]  && [ -n "$R2_ACCOUNT_ID" ]; then
        echo "[entrypoint] best.pt not found — pulling from R2..."
        cd /app && python manage.py sync_ml_weights --pull \
            || echo "[entrypoint] Warning: R2 pull failed, app will use EasyOCR fallback"
    else
        echo "[entrypoint] Warning: best.pt not found and R2 not configured (EasyOCR fallback)"
    fi
fi

exec "$@"

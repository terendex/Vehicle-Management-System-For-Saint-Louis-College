"""
scanning/tasks.py — Celery tasks for the ML feedback loop.

`schedule_retrain_task` is enqueued by `collector.record_scan()` when enough
high-confidence samples have accumulated since the last training run.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from celery import shared_task
from django.conf import settings
from django.core.files.storage import default_storage

log = logging.getLogger(__name__)


@shared_task(name="scanning.ml_retrain", bind=True)
def ml_retrain_task(self):
    task_id = self.request.id
    log.info("[%s] Starting incremental YOLOv8 retrain…", task_id)

    project_root = Path(settings.BASE_DIR)
    train_script = project_root / "scanning" / "ml" / "train.py"

    cmd = [
        sys.executable,
        str(train_script),
        "--epochs", "50",
        "--batch", "16",
        "--model-size", "n",
        "--resume",
        "--incremental",  # export accumulated MLTrainingSample records into the dataset
    ]

    try:
        result = subprocess.run(
            cmd,
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=3600,
        )
        if result.returncode == 0:
            log.info("[%s] Retrain completed successfully", task_id)
            return {"status": "success", "stdout": result.stdout[-1000:]}
        else:
            log.error("[%s] Retrain failed:\n%s", task_id, result.stderr[-1000:])
            return {"status": "error", "stderr": result.stderr[-1000:]}
    except subprocess.TimeoutExpired:
        log.error("[%s] Retrain timed out after 1 hour", task_id)
        return {"status": "timeout", "stderr": "Training exceeded 1-hour limit"}

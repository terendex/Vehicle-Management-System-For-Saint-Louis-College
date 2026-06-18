"""
vehicle_detection.py — Vehicle detection using YOLOv8.

Detects vehicles in images for tracking and license plate association.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

log = logging.getLogger(__name__)

VEHICLE_MODEL_PATH = Path(__file__).resolve().parent / "weights" / "vehicle_detector.pt"
_model = None


def _get_vehicle_model():
    global _model
    if _model is None:
        try:
            from ultralytics import YOLO
            if VEHICLE_MODEL_PATH.exists():
                _model = YOLO(str(VEHICLE_MODEL_PATH))
                log.info("[VEHICLE] Model loaded from %s", VEHICLE_MODEL_PATH)
            else:
                log.warning("[VEHICLE] No vehicle weights at %s", VEHICLE_MODEL_PATH)
        except ImportError:
            log.error("[VEHICLE] ultralytics not installed")
    return _model


def detect_vehicles(img: np.ndarray, conf: float = 0.25) -> list[dict]:
    model = _get_vehicle_model()
    if model is None:
        h, w = img.shape[:2]
        return [{"bbox": (0, 0, w, h), "confidence": 1.0, "crop": img}]
    
    results = model.predict(img, conf=conf, verbose=False, max_det=100)
    detections = []
    for r in results:
        for box in r.boxes:
            x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
            score = float(box.conf[0])
            x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
            crop = img[y1:y2, x1:x2]
            detections.append({
                "bbox": (x1, y1, x2 - x1, y2 - y1),
                "confidence": score,
                "crop": crop,
            })
    return detections


def is_gpu_available() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError:
        return False


def set_gpu_enabled(enabled: bool = True):
    global _model
    if enabled and is_gpu_available():
        try:
            from ultralytics import YOLO
            if VEHICLE_MODEL_PATH.exists():
                _model = YOLO(str(VEHICLE_MODEL_PATH))
                _model.to('cuda')
                log.info("[VEHICLE] GPU enabled")
        except Exception as e:
            log.warning("[VEHICLE] GPU enable failed: %s", e)
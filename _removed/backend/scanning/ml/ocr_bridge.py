"""
ocr_bridge.py — Async OCR handler for real-time video pipeline

Decouples PaddleOCR from the main detection loop. Runs OCR in a thread pool
only when needed (new tracks or periodic refresh) to maintain high FPS.
"""

from __future__ import annotations

import logging
import numpy as np
from typing import Any

from .reader import _ocr_crop, normalize_plate

log = logging.getLogger(__name__)

async def run_ocr_async(detections: list[dict[str, Any]], ocr_interval: int = 10) -> list[dict[str, Any]]:
    """
    Run OCR on detections that need text extraction.
    Returns detections with plate_text populated where OCR succeeded.
    """
    from concurrent.futures import ThreadPoolExecutor
    import asyncio
    
    loop = asyncio.get_event_loop()
    
    results = []
    for det in detections:
        bbox = det["bbox"]
        crop = det["crop"]
        aspect = det.get("aspect_ratio", 1.0)
        
        if not det.get("plate_text") and crop is not None:
            try:
                plate_text, conf = await loop.run_in_executor(
                    None, _ocr_crop, crop, aspect
                )
                det["plate_text"] = plate_text or ""
                det["ocr_confidence"] = conf or 0.0
            except Exception as exc:
                log.warning("[OCR-BRIDGE] OCR failed: %s", exc)
                det["plate_text"] = ""
        
        results.append(det)
    
    return results
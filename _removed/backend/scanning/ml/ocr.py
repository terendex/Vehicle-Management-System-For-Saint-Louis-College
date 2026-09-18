"""
ocr.py — OCR processing with majority voting for license plate recognition.

Features:
- PaddleOCR with multiple image variants for better accuracy
- Majority voting across buffered plate crops
- Low-confidence result filtering
- Real-time processing optimization
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Optional

import cv2
import numpy as np

from .validator import is_valid_ph_plate, normalize_plate, extract_plate_candidates, combine_multiline_text

log = logging.getLogger(__name__)

_MIN_WIDTH = 400
_ocr_reader = None


def _gpu_available() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError:
        return False


def _get_ocr():
    """Lazy-load PaddleOCR reader — uses GPU when CUDA is available."""
    global _ocr_reader
    if _ocr_reader is None:
        try:
            from paddleocr import PaddleOCR
            use_gpu = _gpu_available()
            _ocr_reader = PaddleOCR(use_angle_cls=True, lang='en', use_gpu=use_gpu, show_log=False)
            log.info("[OCR] PaddleOCR loaded (gpu=%s)", use_gpu)
        except ImportError:
            log.error("[OCR] paddleocr not installed")
    return _ocr_reader


def _preprocess(img: np.ndarray) -> np.ndarray:
    """Preprocess image for OCR."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    sharp = cv2.addWeighted(gray, 2.0, blur, -1.0, 0)
    clahe = cv2.createCLAHE(clipLimit=4.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(sharp)
    return cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)


def _preprocess_aggressive(img: np.ndarray) -> np.ndarray:
    """Aggressive preprocessing for difficult plates."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=6.0, tileGridSize=(4, 4))
    enhanced = clahe.apply(gray)
    sharp = cv2.filter2D(enhanced, -1, np.array([[-1, -1, -1], [-1, 9, -1], [-1, -1, -1]]))
    return cv2.cvtColor(sharp, cv2.COLOR_GRAY2BGR)


def _deskew(img: np.ndarray, aspect_ratio: float = 1.0) -> np.ndarray:
    """Skip deskew for motorcycle plates (two-row format)."""
    if aspect_ratio < 2.0:
        return img
    try:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        closed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return img
        c = max(contours, key=cv2.contourArea)
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
        if len(approx) == 4:
            pts = approx.reshape(4, 2).astype(np.float32)
            rect = np.zeros((4, 2), dtype=np.float32)
            s = pts.sum(axis=1)
            rect[0], rect[2] = pts[np.argmin(s)], pts[np.argmax(s)]
            diff = np.diff(pts, axis=1)
            rect[1], rect[3] = pts[np.argmin(diff)], pts[np.argmax(diff)]
            tl, tr, br, bl = rect
            maxW = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
            maxH = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
            dst = np.array([[0, 0], [maxW-1, 0], [maxW-1, maxH-1], [0, maxH-1]], dtype=np.float32)
            M = cv2.getPerspectiveTransform(rect, dst)
            return cv2.warpPerspective(img, M, (maxW, maxH))
    except Exception as e:
        log.debug("[OCR] Deskew failed: %s", e)
    return img


def _correct_chars(text: str) -> str:
    """Correct common OCR misreads for Philippine plates — LTO post-processing."""
    text = text.replace('_', '').replace('+', '').replace('.', '').replace(' ', '').upper()
    
    if not text:
        return text
    
    n = len(text)
    
    if n == 7:
        # Try ABC1234 layout (3L + 4D)
        l1, l2, l3 = text[0], text[1], text[2]
        d1, d2, d3, d4 = text[3], text[4], text[5], text[6]
        l1 = 'O' if l1 in ('0', 'D') else l1
        l2 = 'O' if l2 in ('0', 'D') else l2
        l3 = 'O' if l3 in ('0', 'D') else l3
        d1 = '0' if d1 in ('O', 'Q') else d1
        d2 = '0' if d2 in ('O', 'Q') else d2
        d3 = '0' if d3 in ('O', 'Q') else d3
        d4 = '0' if d4 in ('O', 'Q') else d4
        result = f"{l1}{l2}{l3}{d1}{d2}{d3}{d4}"
        if is_valid_ph_plate(result):
            return result

        # Try AB1234C layout (2L + 4D + 1L)
        la, lb = text[0], text[1]
        d1, d2, d3, d4 = text[2], text[3], text[4], text[5]
        lc = text[6]
        la = 'O' if la in ('0', 'D') else la
        lb = 'O' if lb in ('0', 'D') else lb
        d1 = '0' if d1 in ('O', 'Q') else d1
        d2 = '0' if d2 in ('O', 'Q') else d2
        d3 = '0' if d3 in ('O', 'Q') else d3
        d4 = '0' if d4 in ('O', 'Q') else d4
        lc = 'O' if lc in ('0', 'D') else lc
        result = f"{la}{lb}{d1}{d2}{d3}{d4}{lc}"
        if is_valid_ph_plate(result):
            return result

        # Try A1234BC layout (1L + 4D + 2L)
        la = text[0]
        d1, d2, d3, d4 = text[1], text[2], text[3], text[4]
        lb, lc = text[5], text[6]
        la = 'O' if la in ('0', 'D') else la
        d1 = '0' if d1 in ('O', 'Q') else d1
        d2 = '0' if d2 in ('O', 'Q') else d2
        d3 = '0' if d3 in ('O', 'Q') else d3
        d4 = '0' if d4 in ('O', 'Q') else d4
        lb = 'O' if lb in ('0', 'D') else lb
        lc = 'O' if lc in ('0', 'D') else lc
        result = f"{la}{d1}{d2}{d3}{d4}{lb}{lc}"
        if is_valid_ph_plate(result):
            return result
    
    if n == 6:
        def _l(c): return 'O' if c in ('0', 'D') else c
        def _d(c): return '0' if c in ('O', 'Q') else c
        t = text
        six_layouts = [
            # (layout_string using the corrected chars)
            lambda t: f"{_l(t[0])}{_l(t[1])}{_l(t[2])}{_d(t[3])}{_d(t[4])}{_d(t[5])}",  # ABC123 / 3L+3D
            lambda t: f"{_d(t[0])}{_d(t[1])}{_d(t[2])}{_l(t[3])}{_l(t[4])}{_l(t[5])}",  # 123ABC / 3D+3L
            lambda t: f"{_l(t[0])}{_d(t[1])}{_d(t[2])}{_d(t[3])}{_l(t[4])}{_l(t[5])}",  # N123BC / 1L+3D+2L
            lambda t: f"{_l(t[0])}{_l(t[1])}{_d(t[2])}{_d(t[3])}{_d(t[4])}{_l(t[5])}",  # NB123C / 2L+3D+1L
            lambda t: f"{_l(t[0])}{_d(t[1])}{_d(t[2])}{_d(t[3])}{_d(t[4])}{_l(t[5])}",  # N1234C / 1L+4D+1L
        ]
        for layout_fn in six_layouts:
            result = layout_fn(t)
            if is_valid_ph_plate(result):
                return result
    
    return text


def run_ocr(crop: np.ndarray, aspect_ratio: float = 1.0) -> tuple[Optional[str], float]:
    """
    Run OCR on a single plate crop.
    
    Returns:
        (plate_text, confidence) or (None, 0.0) on failure.
    """
    ocr = _get_ocr()
    if ocr is None:
        log.error("[OCR] PaddleOCR not available")
        return None, 0.0

    h, w = crop.shape[:2]
    log.info("[OCR] Processing crop %dx%d, aspect=%.2f", w, h, aspect_ratio)
    if w < _MIN_WIDTH:
        scale = _MIN_WIDTH / max(w, 1)
        new_h = max(int(h * scale), 80)
        crop = cv2.resize(crop, (_MIN_WIDTH, new_h), interpolation=cv2.INTER_CUBIC)
        log.info("[OCR] Upscaled to %dx%d", crop.shape[1], crop.shape[0])

    deskewed = _deskew(crop, aspect_ratio)

    if len(deskewed.shape) == 3:
        gray = cv2.cvtColor(deskewed, cv2.COLOR_BGR2GRAY)
    else:
        gray = deskewed

    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    variants = [
        ("binary", cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)),
    ]

    results = []
    for label, img_v in variants:
        try:
            raw = ocr.ocr(img_v, cls=True)
            page = raw[0] if raw else []
            text_results = [(item[0], item[1][0], item[1][1]) for item in page if item and len(item) == 2]
            combined_text, avg_conf = combine_multiline_text(text_results)
            if combined_text and avg_conf > 0.15:
                results.append((combined_text, avg_conf))
        except Exception as e:
            log.debug("[OCR] Failed variant %s: %s", label, e)

    if not results:
        log.warning("[OCR] No text results found in any variant")
        return None, 0.0

    by_text = {}
    for text, conf in results:
        by_text[text] = max(by_text.get(text, 0.0), conf)
    
    best_text = max(by_text.items(), key=lambda x: x[1])
    log.info("[OCR] Best text: '%s' (conf=%.2f)", best_text[0], best_text[1])
    
    corrected = _correct_chars(best_text[0])
    normalized = normalize_plate(corrected)
    
    if is_valid_ph_plate(normalized):
        if best_text[1] >= 0.20:
            log.info("[OCR] Valid plate: %s (conf=%.2f)", normalized, best_text[1])
            return normalized, best_text[1]
        else:
            log.info("[OCR] Valid format but too low confidence: %s (conf=%.2f)", normalized, best_text[1])
            return None, 0.0
    
    for candidate in extract_plate_candidates(best_text[0]):
        candidate_corrected = _correct_chars(candidate)
        if is_valid_ph_plate(normalize_plate(candidate_corrected)):
            if best_text[1] >= 0.20:
                log.info("[OCR] Valid plate from candidate: %s (conf=%.2f)", candidate_corrected, best_text[1])
                return candidate_corrected, best_text[1]
            else:
                log.info("[OCR] Valid candidate format but too low confidence: %s (conf=%.2f)", candidate_corrected, best_text[1])
                return None, 0.0
    
    try:
        from .reader import _ocr_crop as reader_ocr_crop
        plate_text, conf = reader_ocr_crop(deskewed, aspect_ratio)
        if plate_text and is_valid_ph_plate(plate_text):
            if conf >= 0.20:
                log.info("[OCR] Valid plate from reader fallback: %s (conf=%.2f)", plate_text, conf)
                return plate_text, conf
            else:
                log.info("[OCR] Valid reader fallback format but too low confidence: %s (conf=%.2f)", plate_text, conf)
                return None, 0.0
    except Exception as e:
        log.debug("[OCR] Reader fallback failed: %s", e)
    
    if best_text[1] >= 0.40:
        log.info("[OCR] Invalid plate format but high confidence: corrected='%s' normalized='%s' (conf=%.2f)", corrected, normalized, best_text[1])
        return corrected, best_text[1]
    
    log.info("[OCR] Discarding invalid low-confidence plate format: corrected='%s' normalized='%s' (conf=%.2f)", corrected, normalized, best_text[1])
    return None, 0.0


def majority_vote_ocr(crops: list[np.ndarray], aspect_ratios: list[float] = None) -> tuple[Optional[str], float]:
    """
    Run OCR on multiple plate crops and use majority voting.
    
    Args:
        crops: List of plate crop images
        aspect_ratios: Optional list of aspect ratios for each crop
    
    Returns:
        (plate_text, confidence) - final result from majority voting.
    """
    if not crops:
        return None, 0.0

    if aspect_ratios is None:
        aspect_ratios = [1.0] * len(crops)

    all_results = []
    for crop, ar in zip(crops, aspect_ratios):
        text, conf = run_ocr(crop, ar)
        if text and conf > 0.1:
            all_results.append((text, conf))

    if not all_results:
        return None, 0.0

    by_text = {}
    for text, conf in all_results:
        by_text[text] = max(by_text.get(text, 0.0), conf)

    votes = Counter()
    for text, _ in all_results:
        votes[text] += 1

    best_text = None
    best_votes = 0
    best_conf = 0.0
    for text, count in votes.items():
        if count > best_votes or (count == best_votes and by_text.get(text, 0) > best_conf):
            best_votes = count
            best_text = text
            best_conf = by_text.get(text, 0)

    return best_text, best_conf
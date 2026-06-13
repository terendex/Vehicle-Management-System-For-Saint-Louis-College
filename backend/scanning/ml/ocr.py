"""
ocr.py — OCR processing with majority voting for license plate recognition.

Features:
- EasyOCR with multiple image variants for better accuracy
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

from .validator import is_valid_ph_plate, normalize_plate

log = logging.getLogger(__name__)

_MIN_WIDTH = 320
_ocr_reader = None


def _get_ocr():
    """Lazy-load EasyOCR reader."""
    global _ocr_reader
    if _ocr_reader is None:
        try:
            import easyocr
            _ocr_reader = easyocr.Reader(["en"], gpu=False)
        except ImportError:
            log.error("[OCR] easyocr not installed")
    return _ocr_reader


def _preprocess(img: np.ndarray) -> np.ndarray:
    """Preprocess image for OCR."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    return cv2.addWeighted(gray, 1.8, blur, -0.8, 0)


def _deskew(img: np.ndarray, aspect_ratio: float = 1.0) -> np.ndarray:
    """Skip deskew for motorcycle plates (two-row format)."""
    if aspect_ratio < 2.0:
        return img
    try:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 3))
        closed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return img
        c = max(contours, key=cv2.contourArea)
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.03 * peri, True)
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
    """Correct common OCR misreads for Philippine plates."""
    to_digit = str.maketrans({'B': '8', 'O': '0', 'I': '1', 'L': '1', 'Z': '2', 'S': '5', 'G': '6', 'Q': '9'})
    to_letter = str.maketrans({'H': 'M', 'W': 'M'})
    
    candidates = [
        text.translate(to_digit),
        text.translate(to_letter),
        text.translate(to_digit).translate(to_letter),
        text,
    ]
    
    for candidate in candidates:
        if is_valid_ph_plate(normalize_plate(candidate)):
            return candidate
    return text


def run_ocr(crop: np.ndarray, aspect_ratio: float = 1.0) -> tuple[Optional[str], float]:
    """
    Run OCR on a single plate crop.
    
    Returns:
        (plate_text, confidence) or (None, 0.0) on failure.
    """
    ocr = _get_ocr()
    if ocr is None:
        log.error("[OCR] EasyOCR not available")
        return None, 0.0

    h, w = crop.shape[:2]
    log.info("[OCR] Processing crop %dx%d, aspect=%.2f", w, h, aspect_ratio)
    if w < _MIN_WIDTH:
        scale = _MIN_WIDTH / max(w, 1)
        crop = cv2.resize(crop, (_MIN_WIDTH, max(int(h * scale), 20)), interpolation=cv2.INTER_CUBIC)

    deskewed = _deskew(crop, aspect_ratio)
    
    variants = [
        ("raw", deskewed),
        ("enhanced", _preprocess(deskewed)),
    ]
    
    results = []
    for label, img_v in variants:
        try:
            text_results = ocr.readtext(img_v, text_threshold=0.3, link_threshold=0.3, low_text=0.2)
            for item in text_results:
                if len(item) >= 3:
                    _, text, conf = item[0], item[1], item[2]
                    if conf > 0.15:
                        results.append((text, conf))
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
        log.info("[OCR] Valid plate: %s", normalized)
        return normalized, best_text[1]
    log.info("[OCR] Invalid plate format: corrected='%s' normalized='%s'", corrected, normalized)
    return corrected, best_text[1]


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
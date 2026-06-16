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

from .validator import is_valid_ph_plate, normalize_plate, extract_plate_candidates, combine_multiline_text

log = logging.getLogger(__name__)

_MIN_WIDTH = 400
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
    
    if n == 6:
        if text[0].isdigit():
            d1, d2, d3 = text[0], text[1], text[2]
            l1, l2, l3 = text[3], text[4], text[5]
            d1 = '0' if d1 in ('O',) else d1
            d2 = '0' if d2 in ('O',) else d2
            d3 = '0' if d3 in ('O',) else d3
            l1 = 'O' if l1 in ('0',) else l1
            l2 = 'O' if l2 in ('0',) else l2
            l3 = 'O' if l3 in ('0',) else l3
            result = f"{d1}{d2}{d3}{l1}{l2}{l3}"
            if is_valid_ph_plate(result):
                return result
        else:
            l1, l2, l3 = text[0], text[1], text[2]
            d1, d2, d3 = text[3], text[4], text[5]
            l1 = 'O' if l1 in ('0', 'D') else l1
            l2 = 'O' if l2 in ('0', 'D') else l2
            l3 = 'O' if l3 in ('0', 'D') else l3
            d1 = '0' if d1 in ('O', 'Q') else d1
            d2 = '0' if d2 in ('O', 'Q') else d2
            d3 = '0' if d3 in ('O', 'Q') else d3
            result = f"{l1}{l2}{l3}{d1}{d2}{d3}"
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
        log.error("[OCR] EasyOCR not available")
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
    allowlist = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-'
    for label, img_v in variants:
        try:
            text_results = ocr.readtext(
                img_v, 
                text_threshold=0.15, 
                link_threshold=0.15, 
                low_text=0.05, 
                mag_ratio=1.5,
                allowlist=allowlist
            )
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
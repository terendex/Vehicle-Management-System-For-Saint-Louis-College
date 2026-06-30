"""
──────────────────────────────────────────────────────────────────────
  reader.py — License Plate Detection + OCR Pipeline
──────────────────────────────────────────────────────────────────────

  Two-stage pipeline:
    1. YOLO — locates the license plate region (bounding box)
    2. PaddleOCR — reads the text from the cropped plate region

  If no trained YOLO model is found, falls back to running PaddleOCR
  on the full preprocessed frame (legacy behaviour).
──────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
import cv2
import numpy as np

# cudnn64_8.dll (required by paddlepaddle-gpu 2.6.x) lives alongside the
# PyTorch CUDA libs which ship cudnn64_9.dll copied as cudnn64_8.dll.
_TORCH_LIB = Path(r"C:\Users\axel jonas tangalin\AppData\Local\Programs\Python\Python39\Lib\site-packages\torch\lib")
if _TORCH_LIB.exists():
    os.environ['PATH'] = str(_TORCH_LIB) + os.pathsep + os.environ.get('PATH', '')

from .validator import is_valid_ph_plate, normalize_plate, extract_plate_candidates, combine_multiline_text
from .detection import detect_plates, is_gpu_available

log = logging.getLogger(__name__)

_TO_DIGIT = str.maketrans({
    'B': '8', 'O': '0', 'I': '1', 'L': '1', 'l': '1',
    'Z': '2', 'S': '5', 'G': '6', 'Q': '9', 'g': '9', 'q': '9',
    'D': '0', 'U': '0', 'A': '4', 'R': '2', 'T': '7', 'J': '1',
    'Y': '7', 'b': '6', 'o': '0', 'i': '1', 'z': '2', 's': '5',
    'g': '9', 'q': '9', 'd': '0',
    'H': '4',
    'h': '4',
    'N': '4',
    'n': '4',
    'M': '4',
    'm': '4',
})

_TO_LETTER = str.maketrans({
    '8': 'B', '0': 'O', '1': 'I', '2': 'Z', '5': 'S',
    '6': 'G', '9': 'Q', '3': 'E', '7': 'T',
    '4': 'A',
    '1': 'L',
    'M': '4',
    'm': '4',
})


def _correct_plate_chars(text: str) -> str:
    """Correct common OCR misreads for Philippine plates — position-aware."""
    import re as _re
    text = text.replace('_', '').replace('+', '').replace('.', '')
    text = text.upper()

    clean = normalize_plate(text)
    # Pure-digit plates (all numbers, no letters) are almost always misreads of mixed
    # letter+digit plates for this use-case (motorcycle/car on a college campus).
    # Diplomatic plates (7 digits) are extremely rare, so don't early-return for them —
    # instead fall through to layout correction which may produce a better candidate.
    _is_pure_digits = bool(_re.match(r'^[0-9]+$', clean))
    if is_valid_ph_plate(clean) and not _is_pure_digits:
        return text

    to_digit = str.maketrans({
        'B': '8', 'O': '0', 'I': '1', 'L': '1', 'Z': '2', 'S': '5',
        'G': '6', 'Q': '9', 'U': '0', 'D': '0', 'A': '4', 'R': '2',
        'T': '7', 'J': '1', 'Y': '7',
    })
    to_letter = str.maketrans({
        '0': 'O', '1': 'I', '2': 'Z', '5': 'S', '6': 'G', '9': 'Q',
        '4': 'A', '7': 'T', '3': 'E', '8': 'B',
    })
    letter_fix = str.maketrans({
        'H': 'M', 'W': 'M',
    })
    
    layouts = [
        # ── 7-char ───────────────────────────────────
        (3, 'L', 4, 'D'),           # ABC1234
        (2, 'L', 4, 'D', 1, 'L'),  # AB1234C
        (1, 'L', 4, 'D', 2, 'L'),  # A1234BC
        # ── 6-char ───────────────────────────────────
        (3, 'L', 3, 'D'),           # ABC123
        (3, 'D', 3, 'L'),           # 123ABC
        (1, 'L', 3, 'D', 2, 'L'),  # N123BC
        (2, 'L', 3, 'D', 1, 'L'),  # NB123C
        (1, 'L', 4, 'D', 1, 'L'),  # N1234C
        (2, 'L', 3, 'D', 1, 'L'),  # AB123C
        (1, 'L', 4, 'D', 1, 'L'),  # A1234B
        # ── other ────────────────────────────────────
        (2, 'L', 4, 'D'),           # AB1234
        (2, 'L', 5, 'D'),           # AB12345
        (1, 'L', 2, 'D', 3, 'L'),  # A12ABC
    ]
    
    for layout in layouts:
        if len(layout) == 4:
            n1, t1, n2, t2 = layout
            if len(clean) != n1 + n2:
                continue
            part1 = clean[:n1]
            part2 = clean[n1:]
            if t1 == 'L':
                part1_fixed = part1.translate(to_letter).translate(letter_fix)
            else:
                part1_fixed = part1.translate(to_digit)
            if t2 == 'L':
                part2_fixed = part2.translate(to_letter).translate(letter_fix)
            else:
                part2_fixed = part2.translate(to_digit)
            candidate = normalize_plate(part1_fixed + part2_fixed)
            if is_valid_ph_plate(candidate):
                return candidate
        elif len(layout) == 6:
            n1, t1, n2, t2, n3, t3 = layout
            if len(clean) != n1 + n2 + n3:
                continue
            parts = [clean[:n1], clean[n1:n1+n2], clean[n1+n2:]]
            types = [t1, t2, t3]
            fixed = []
            for p, t in zip(parts, types):
                if t == 'L':
                    fixed.append(p.translate(to_letter).translate(letter_fix))
                else:
                    fixed.append(p.translate(to_digit))
            candidate = normalize_plate(''.join(fixed))
            if is_valid_ph_plate(candidate):
                return candidate
    
    for i in range(len(clean)):
        for trans_table in [to_digit, to_letter, letter_fix]:
            translated = clean[:i] + clean[i:i+1].translate(trans_table) + clean[i+1:]
            if is_valid_ph_plate(normalize_plate(translated)):
                return translated

    # If OCR produced a pure-digit result longer than 6 chars, try substrings of
    # length 6 with layout correction — OCR often inserts extra noise characters.
    if _is_pure_digits and len(clean) >= 7:
        for start in range(len(clean) - 5):
            substr = clean[start:start + 6]
            corrected = _correct_plate_chars(substr)
            cn = normalize_plate(corrected)
            if is_valid_ph_plate(cn) and not _re.match(r'^[0-9]+$', cn):
                return cn

    return text


# ── Lazy singletons ─────────────────────────────────────────────────

_ocr_reader = None  # PaddleOCR instance, lazy-loaded
_ocr_load_failures = 0
_OCR_MAX_RETRIES = 3
_ocr_disabled_logged = False  # log the "disabled" message only once

# PaddleOCR's ocr() is not thread-safe under concurrent calls on the
# same instance.  Serialise all ocr() calls across camera streams.
_OCR_LOCK = threading.Lock()

_yolo_model = None
_yolo_loaded = False

WEIGHTS_PATH = Path(__file__).resolve().parent / "weights" / "best.pt"

# Confidence above which we skip the expensive L/M/R tiled passes
_OCR_EARLY_EXIT_CONF = 0.60



def _get_ocr():
    """Lazy-load PaddleOCR with up to _OCR_MAX_RETRIES attempts."""
    global _ocr_reader, _ocr_load_failures, _ocr_disabled_logged
    if _ocr_reader is not None:
        return _ocr_reader
    if _ocr_load_failures >= _OCR_MAX_RETRIES:
        if not _ocr_disabled_logged:
            _ocr_disabled_logged = True
            log.error(
                "[OCR] PaddleOCR failed to load after %d attempts — OCR is disabled. "
                "Restart the server to retry.",
                _OCR_MAX_RETRIES,
            )
        return None
    try:
        from paddleocr import PaddleOCR
        _use_gpu = is_gpu_available()
        _ocr_reader = PaddleOCR(use_angle_cls=True, lang='en', use_gpu=_use_gpu, show_log=False)
        log.info("[OCR] PaddleOCR loaded OK (gpu=%s)", _use_gpu)
        _ocr_load_failures = 0
    except Exception as exc:
        _ocr_load_failures += 1
        log.error(
            "[OCR] PaddleOCR load failed (attempt %d/%d): %s",
            _ocr_load_failures, _OCR_MAX_RETRIES, exc,
        )
    return _ocr_reader


def _get_yolo():
    """
    Lazy-load the YOLO plate-detection model.
    Returns None if no trained weights exist yet.
    """
    global _yolo_model, _yolo_loaded
    if _yolo_loaded:
        return _yolo_model
    _yolo_loaded = True

    if WEIGHTS_PATH.exists():
        try:
            from ultralytics import YOLO
            _yolo_model = YOLO(str(WEIGHTS_PATH))
            log.info("✅ YOLO plate-detector loaded from %s", WEIGHTS_PATH)
        except Exception as exc:
            log.warning("⚠️  Failed to load YOLO model: %s", exc)
            _yolo_model = None
    else:
        log.info(
            "ℹ️  No YOLO weights found at %s — using PaddleOCR-only fallback.",
            WEIGHTS_PATH,
        )
    return _yolo_model


# ── Image helpers ───────────────────────────────────────────────────

def _decode(image_bytes: bytes) -> np.ndarray:
    """Decode raw bytes into a BGR OpenCV image."""
    nparr = np.frombuffer(image_bytes, np.uint8)
    return cv2.imdecode(nparr, cv2.IMREAD_COLOR)


def _preprocess_for_ocr(img: np.ndarray) -> np.ndarray:
    """Convert a BGR image to a sharpened grayscale for OCR."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    sharp = cv2.addWeighted(gray, 1.8, blur, -0.8, 0)
    return sharp


def _preprocess_aggressive(img: np.ndarray) -> np.ndarray:
    """Aggressive preprocessing for difficult plates."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=6.0, tileGridSize=(4, 4))
    enhanced = clahe.apply(gray)
    sharp = cv2.filter2D(enhanced, -1, np.array([[-1, -1, -1], [-1, 9, -1], [-1, -1, -1]]))
    return sharp


def _deskew_plate(img: np.ndarray, aspect_ratio: float = 1.0) -> np.ndarray:
    """
    Try to find the white license plate rectangle within the crop and
    perspective-correct it. Returns the original img if no rectangle found.
    
    For motorcycle/tricycle plates (aspect_ratio < 2.0), skip deskew as it
    tends to crop to only one row of the two-row plate.
    """
    # Skip deskew for motorcycle/tricycle plates (two-row format)
    # Deskew often crops to only one row, losing the other row of characters
    if aspect_ratio < 2.0:
        log.info("[DESKEW] Skipped for motorcycle plate (aspect=%.2f)", aspect_ratio)
        return img
    
    try:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)
        # Morphological closing to merge plate region
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 3))
        closed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return img
        # Pick the largest contour by area
        c = max(contours, key=cv2.contourArea)
        area = cv2.contourArea(c)
        if area < 300:  # too small to be a plate
            return img
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.03 * peri, True)
        if len(approx) == 4:
            # Perspective warp to a flat rectangle
            pts = approx.reshape(4, 2).astype(np.float32)
            # Order points: TL, TR, BR, BL
            rect = np.zeros((4, 2), dtype=np.float32)
            s = pts.sum(axis=1)
            rect[0] = pts[np.argmin(s)]
            rect[2] = pts[np.argmax(s)]
            diff = np.diff(pts, axis=1)
            rect[1] = pts[np.argmin(diff)]
            rect[3] = pts[np.argmax(diff)]
            tl, tr, br, bl = rect
            maxW = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
            maxH = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
            if maxW < 30 or maxH < 10:
                return img
            dst = np.array([[0, 0], [maxW-1, 0], [maxW-1, maxH-1], [0, maxH-1]], dtype=np.float32)
            M = cv2.getPerspectiveTransform(rect, dst)
            warped = cv2.warpPerspective(img, M, (maxW, maxH))
            log.info("[DESKEW] Perspective correction applied: %dx%d", maxW, maxH)
            return warped
    except Exception as e:
        log.debug("[DESKEW] Failed: %s", e)
    return img


# ── Plate localisation within a large vehicle crop ──────────────────

def find_plate_in_crop(img: np.ndarray) -> np.ndarray:
    """
    Given a large vehicle-body crop, attempt to locate and return just the
    license plate sub-region using white-area detection.

    Philippine plates are white (high V, low S in HSV) rectangles that are
    wider than they are tall.  If no confident candidate is found the full
    crop is returned unchanged so OCR still gets a chance.
    """
    if img is None or img.size == 0:
        return img

    h, w = img.shape[:2]
    if w < 40 or h < 20:
        return img

    try:
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        # White region: low saturation, high brightness
        mask = cv2.inRange(hsv,
                           np.array([0,   0, 170]),
                           np.array([180, 80, 255]))

        # Close small gaps so the plate area becomes one solid blob
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (20, 6))
        closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL,
                                        cv2.CHAIN_APPROX_SIMPLE)
        candidates = []
        for c in contours:
            rx, ry, rw, rh = cv2.boundingRect(c)
            if rw < 60 or rh < 25:
                continue
            ar = rw / max(rh, 1)
            if 1.0 <= ar <= 6.0:               # plate-like aspect ratio
                fill = cv2.contourArea(c) / (rw * rh)
                if fill > 0.35:                 # mostly solid white, not noise
                    candidates.append((rw * rh, rx, ry, rw, rh))

        if not candidates:
            return img

        # Largest white rectangle wins
        candidates.sort(reverse=True)
        _, rx, ry, rw, rh = candidates[0]

        pad = 8
        x1 = max(0, rx - pad)
        y1 = max(0, ry - pad)
        x2 = min(w, rx + rw + pad)
        y2 = min(h, ry + rh + pad)
        plate_crop = img[y1:y2, x1:x2]

        if plate_crop.size == 0:
            return img

        log.info("[PLATE-FIND] Extracted %dx%d plate region from %dx%d vehicle crop",
                 x2 - x1, y2 - y1, w, h)
        return plate_crop

    except Exception as exc:
        log.debug("[PLATE-FIND] Failed: %s", exc)
        return img


# ── YOLO detection ──────────────────────────────────────────────────

def _detect_plates(img: np.ndarray, conf: float = 0.25):
    return detect_plates(img, conf)


# ── OCR on a single plate crop ──────────────────────────────────────

def _combine_lmr_text(texts: dict[str, str]) -> str:
    """Combine L/M/R region texts into a potential plate."""
    l_text = texts.get('L', '')
    m_text = texts.get('M', '')
    r_text = texts.get('R', '')
    if l_text and m_text and r_text:
        return l_text + m_text + r_text
    if m_text and r_text:
        return m_text + r_text
    if l_text and m_text:
        return l_text + m_text
    return ''


def _to_bw(img: np.ndarray) -> np.ndarray:
    """
    Convert a BGR plate crop to a clean black-and-white binary image.

    Uses HSV white-region extraction first (best for Philippine green plates
    with white text), then falls back to CLAHE + Otsu if the HSV mask is too
    sparse (e.g. yellow, red, or heavily shadowed plates).
    """
    if len(img.shape) == 2:
        gray = img
    else:
        # HSV approach: isolate white text/background (low saturation, decent brightness)
        # V threshold lowered to 140 to handle nighttime / underexposed RTSP frames.
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        white_mask = cv2.inRange(hsv, (0, 0, 140), (180, 80, 255))
        # If at least 3% of pixels are "white" the HSV mask is usable
        if white_mask.sum() > img.shape[0] * img.shape[1] * 0.03 * 255:
            return white_mask
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    cl = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(4, 4))
    gray = cl.apply(gray)
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return bw


def _ocr_crop(crop: np.ndarray, aspect_ratio: float = 1.0) -> tuple[str, float] | tuple[None, None]:
    """
    Run PaddleOCR on a cropped plate image.

    Converts the crop to black-and-white first, then scans.
    Falls back to inverted B&W if the normal pass finds nothing.
    Returns (normalized_plate, confidence) or (None, None) on failure.
    """
    MIN_WIDTH = 640
    h_crop, w_crop = crop.shape[:2]

    if w_crop < MIN_WIDTH:
        scale = MIN_WIDTH / max(w_crop, 1)
        new_w = MIN_WIDTH
        new_h = max(int(h_crop * scale), 80)
        crop = cv2.resize(crop, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)
        # Sharpen after upscale to restore edge crispness lost during interpolation
        kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32)
        crop = cv2.filter2D(crop, -1, kernel)
        log.info("[OCR-CROP] Upscaled crop to %dx%d (scale=%.2f)", crop.shape[1], crop.shape[0], scale)

    deskewed = _deskew_plate(crop, aspect_ratio)
    ocr = _get_ocr()
    if ocr is None:
        return None, None

    # Convert to B&W variants fed to PaddleOCR
    bw     = _to_bw(deskewed)
    bw_inv = cv2.bitwise_not(bw)
    _gray  = cv2.cvtColor(deskewed, cv2.COLOR_BGR2GRAY) if len(deskewed.shape) == 3 else deskewed
    # CLAHE-sharpened — good for faded / low-contrast plates
    _cl         = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    _cl_applied = _cl.apply(_gray)
    clahe_sharp = cv2.addWeighted(_cl_applied, 1.5,
                                  cv2.GaussianBlur(_cl_applied, (3, 3), 0), -0.5, 0)
    # Adaptive threshold — handles uneven lighting across the plate (night / shadows)
    _denoised = cv2.medianBlur(_gray, 3)
    adaptive  = cv2.adaptiveThreshold(
        _denoised, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 10
    )
    # Bilateral + Otsu — edge-preserving denoise before threshold (good for RTSP compression artifacts)
    _bilateral = cv2.bilateralFilter(_gray, 9, 75, 75)
    _, bilateral_bw = cv2.threshold(_bilateral, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Gamma-brightened — lifts underexposed nighttime frames so Otsu gets a cleaner histogram
    _gamma_lut = np.array([min(255, int(255 * ((i / 255.0) ** (1.0 / 1.8)))) for i in range(256)], dtype=np.uint8)
    _gamma_gray = cv2.LUT(_gray, _gamma_lut)
    _, gamma_bw  = cv2.threshold(_gamma_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)


    def _run_ocr(img: np.ndarray, label: str):
        if img is None or img.size == 0:
            return
        # PaddleOCR requires a 3-channel BGR image
        img_input = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR) if len(img.shape) == 2 else img
        with _OCR_LOCK:
            raw_result = ocr.ocr(img_input, cls=True)
        page = raw_result[0] if raw_result else None
        if not page:
            log.info("[OCR-CROP] %s: PaddleOCR found 0 text regions", label)
            return
        # Normalise to (bbox, text, conf) tuples that combine_multiline_text expects
        raw = [(item[0], item[1][0], item[1][1]) for item in page if item and len(item) == 2]
        log.info("[OCR-CROP] %s: PaddleOCR found %d text regions", label, len(raw))
        combined_text, avg_conf = combine_multiline_text(raw)
        if combined_text:
            text_clean = normalize_plate(combined_text)
            if text_clean:
                corrected = _correct_plate_chars(text_clean)
                valid = is_valid_ph_plate(corrected)
                conf = float(avg_conf)
                if valid:
                    conf = min(conf * 1.15, 1.0)
                log.info("[OCR-CROP]   %s: '%s' -> '%s' conf=%.3f valid=%s",
                         label, text_clean, corrected, conf, valid)
                yield corrected, conf, valid

    candidates: list[tuple[float, str]] = []
    fallback:   list[tuple[float, str]] = []

    # Color and grayscale first — PaddleOCR's detector works on gradient information
    # that binary thresholding can destroy; color/grayscale variants tend to outperform
    # pure binary for plate OCR in practice.
    _variants = [
        (deskewed,    "color"),
        (clahe_sharp, "clahe"),
        (_gray,       "gray"),
        (bw,          "bw"),
        (bw_inv,      "bw_inv"),
        (adaptive,    "adaptive"),
        (bilateral_bw,"bilateral"),
        (gamma_bw,    "gamma"),
    ]
    for img_v, label in _variants:
        for text, conf, valid in _run_ocr(img_v, label):
            if valid and conf > 0.08:
                candidates.append((conf, text))
                if conf >= _OCR_EARLY_EXIT_CONF:
                    log.info("[OCR-CROP] Early exit — high-conf valid plate: '%s' (%.2f)", text, conf)
                    return text, conf
            elif conf > 0.05:
                fallback.append((conf, text))

    # L/M/R tiled pass — helps when text is split across regions
    h_img, w_img = bw.shape[:2]
    if w_img > 90:
        third      = w_img // 3
        two_thirds = 2 * third
        lmr_texts: dict[str, str] = {}
        for img_v, label in _variants:
            for sv, sl in [
                (img_v[:, :third],          f"{label}_L"),
                (img_v[:, third:two_thirds],f"{label}_M"),
                (img_v[:, two_thirds:],     f"{label}_R"),
            ]:
                for text, conf, valid in _run_ocr(sv, sl):
                    if valid and conf > 0.08:
                        candidates.append((conf, text))
                    elif conf > 0.05:
                        fallback.append((conf, text))
                    region = sl.split("_")[-1]
                    if text:
                        lmr_texts[region] = lmr_texts.get(region, "") + text

        combined = _combine_lmr_text(lmr_texts)
        if combined and is_valid_ph_plate(normalize_plate(combined)):
            log.info("[OCR-CROP] Valid combined LMR: '%s'", combined)
            return normalize_plate(combined), 0.5

    for pool in (candidates, fallback):
        if not pool:
            continue
        pool.sort(reverse=True)
        by_text: dict[str, float] = {}
        for conf, text in pool:
            by_text[text] = max(by_text.get(text, 0.0), conf)
        best_text, best_conf = max(by_text.items(), key=lambda x: x[1])
        if is_valid_ph_plate(best_text):
            log.info("[OCR-CROP] Best valid: '%s' (conf=%.3f)", best_text, best_conf)
            return best_text, best_conf
        for candidate in extract_plate_candidates(best_text):
            if is_valid_ph_plate(candidate):
                log.info("[OCR-CROP] Valid plate from candidate: '%s'", candidate)
                return candidate, best_conf
        log.info("[OCR-CROP] Best (invalid): '%s' (conf=%.3f)", best_text, best_conf)
        return best_text, best_conf

    raw_text, raw_conf = _run_raw_ocr_fallback(crop)
    if raw_text:
        log.info("[OCR-CROP] Raw fallback: '%s' (conf=%.3f)", raw_text, raw_conf)
        return raw_text, raw_conf

    log.info("[OCR-CROP] No plate candidates found")
    return None, None


def _run_raw_ocr_fallback(crop: np.ndarray) -> tuple[str | None, float]:
    if len(crop.shape) == 3:
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    else:
        gray = crop
    cl = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    gray = cl.apply(gray)
    scale = 640 / max(gray.shape[1], 1)
    up = cv2.resize(gray, (int(gray.shape[1] * scale), int(gray.shape[0] * scale)), interpolation=cv2.INTER_LANCZOS4)
    up_bgr = cv2.cvtColor(up, cv2.COLOR_GRAY2BGR)
    try:
        ocr = _get_ocr()
        with _OCR_LOCK:
            raw_result = ocr.ocr(up_bgr, cls=True)
        page = raw_result[0] if raw_result else None
        log.info("[OCR-CROP][RAW-FB] raw regions: %d", len(page) if page else 0)
        if not page:
            return None, 0.0
        candidates: list[tuple[float, str]] = []
        for item in page:
            if not item or len(item) != 2:
                continue
            _, (text, confidence) = item
            text_c = normalize_plate(str(text).strip())
            if not text_c:
                continue
            conf = float(confidence)
            corrected = _correct_plate_chars(text_c)
            if is_valid_ph_plate(normalize_plate(corrected)):
                candidates.append((conf, corrected))
            elif is_valid_ph_plate(text_c):
                candidates.append((conf, text_c))
        if candidates:
            candidates.sort(reverse=True)
            return candidates[0][1], candidates[0][0]
    except Exception as exc:
        log.debug("[OCR-CROP][RAW-FB] failed: %s", exc)
    return None, 0.0


# ── Public API ──────────────────────────────────────────────────────

def read_plate(image_bytes: bytes) -> list[dict]:
    """
    Main entry point — detect + read Philippine license plates.

    Returns a list of dicts, each with:
        plate_text — the recognized plate number
        bbox       — relative bounding box {"x", "y", "width", "height"} (0–1)
        confidence — detection confidence

    Returns an empty list when no plates are found.
    """
    img = _decode(image_bytes)
    if img is None:
        log.error("[READ] Failed to decode image bytes (len=%d)", len(image_bytes))
        return []

    h, w = img.shape[:2]
    log.info("[READ] Decoded image: %dx%d", w, h)
    results: list[dict] = []

    detections = _detect_plates(img)

    for det in detections:
        plate_text, conf = _ocr_crop(det["crop"], det.get("aspect_ratio", 1.0))
        if plate_text:
            results.append({
                "plate_text": plate_text,
                "bbox": det["bbox"],
                "confidence": conf,
            })

    if results:
        log.info("[READ] Found %d detections: %s", len(results), results)
    return results
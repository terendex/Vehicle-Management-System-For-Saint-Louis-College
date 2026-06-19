"""
──────────────────────────────────────────────────────────────────────
  reader.py — License Plate Detection + OCR Pipeline
──────────────────────────────────────────────────────────────────────

  Two-stage pipeline:
    1. YOLO — locates the license plate region (bounding box)
    2. EasyOCR — reads the text from the cropped plate region

  If no trained YOLO model is found, falls back to running EasyOCR
  on the full preprocessed frame (legacy behaviour).
──────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np
import easyocr

from .validator import is_valid_ph_plate, normalize_plate, extract_plate_candidates, combine_multiline_text
from .detection import detect_plates, VEHICLE_TYPE_CLASSES, is_gpu_available

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
    text = text.replace('_', '').replace('+', '').replace('.', '')
    text = text.upper()
    
    if is_valid_ph_plate(normalize_plate(text)):
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
    
    clean = normalize_plate(text)
    
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
    
    return text


# ── Lazy singletons ─────────────────────────────────────────────────

_ocr_reader: easyocr.Reader | None = None
_ocr_load_failures = 0
_OCR_MAX_RETRIES = 3

_yolo_model = None
_yolo_loaded = False

WEIGHTS_PATH = Path(__file__).resolve().parent / "weights" / "best.pt"

# Confidence above which we skip the expensive L/M/R tiled passes
_OCR_EARLY_EXIT_CONF = 0.60


def requires_digital_id(class_name: str) -> bool:
    return class_name in VEHICLE_TYPE_CLASSES


def _get_ocr():
    """Lazy-load EasyOCR with up to _OCR_MAX_RETRIES attempts."""
    global _ocr_reader, _ocr_load_failures
    if _ocr_reader is not None:
        return _ocr_reader
    if _ocr_load_failures >= _OCR_MAX_RETRIES:
        log.error(
            "[OCR] EasyOCR failed to load after %d attempts — OCR is disabled. "
            "Restart the server to retry.",
            _OCR_MAX_RETRIES,
        )
        return None
    try:
        _use_gpu = is_gpu_available()
        _ocr_reader = easyocr.Reader(["en"], gpu=_use_gpu)
        log.info("[OCR] EasyOCR loaded OK (gpu=%s)", _use_gpu)
        _ocr_load_failures = 0
    except Exception as exc:
        _ocr_load_failures += 1
        log.error(
            "[OCR] EasyOCR load failed (attempt %d/%d): %s",
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
            "ℹ️  No YOLO weights found at %s — using EasyOCR-only fallback.",
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


def _ocr_crop(crop: np.ndarray, aspect_ratio: float = 1.0) -> tuple[str, float] | tuple[None, None]:
    """
    Run EasyOCR on a cropped plate image.

    Generates multiple augmented variants and ranks results.
    Returns (normalized_plate, confidence) or (None, None) on failure.
    """
    MIN_WIDTH = 400
    h_crop, w_crop = crop.shape[:2]

    if w_crop < MIN_WIDTH:
        scale = MIN_WIDTH / max(w_crop, 1)
        crop = cv2.resize(
            crop, (MIN_WIDTH, max(int(h_crop * scale), 80)),
            interpolation=cv2.INTER_CUBIC,
        )
        log.info("[OCR-CROP] Upscaled crop to %dx%d", crop.shape[1], crop.shape[0])

    deskewed = _deskew_plate(crop, aspect_ratio)
    ocr = _get_ocr()

    def _run_ocr(img, label):
        if img is None or getattr(img, "size", 0) == 0:
            return
        allowlist = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-'
        raw = ocr.readtext(
            img, text_threshold=0.15, link_threshold=0.15, low_text=0.05, mag_ratio=1.5,
            allowlist=allowlist,
        )
        log.info("[OCR-CROP] %s: EasyOCR found %d text regions", label, len(raw))
        combined_text, avg_conf = combine_multiline_text(raw)
        if combined_text:
            text_clean = normalize_plate(combined_text)
            if text_clean:
                corrected = _correct_plate_chars(text_clean)
                valid = is_valid_ph_plate(corrected)
                conf = float(avg_conf)
                if valid:
                    conf *= 1.15
                    conf = min(conf, 1.0)
                log.info("[OCR-CROP]   %s: '%s' -> '%s' conf=%.3f valid=%s",
                         label, text_clean, corrected, conf, valid)
                yield corrected, conf, valid

    candidates: list[tuple[float, str]] = []
    fallback: list[tuple[float, str]] = []

    gray = cv2.cvtColor(deskewed, cv2.COLOR_BGR2GRAY) if len(deskewed.shape) == 3 else deskewed
    binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    # Inverted binary — handles plates where characters are lighter than background
    binary_inv = cv2.bitwise_not(binary)
    # CLAHE-sharpened — improves contrast on dim, faded, or shadowed plates
    _cl = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    clahe_gray = _cl.apply(gray)
    clahe_sharp = cv2.addWeighted(clahe_gray, 1.5,
                                  cv2.GaussianBlur(clahe_gray, (3, 3), 0), -0.5, 0)
    base_variants: list[tuple[np.ndarray, str]] = [
        (cv2.cvtColor(binary,      cv2.COLOR_GRAY2BGR), "binary"),
        (cv2.cvtColor(binary_inv,  cv2.COLOR_GRAY2BGR), "binary_inv"),
        (cv2.cvtColor(clahe_sharp, cv2.COLOR_GRAY2BGR), "clahe"),
    ]

    for img_v, label in base_variants:
        for text, conf, valid in _run_ocr(img_v, label):
            if valid and conf > 0.08:
                candidates.append((conf, text))
                if conf >= _OCR_EARLY_EXIT_CONF:
                    log.info("[OCR-CROP] Early exit — high-conf valid plate: '%s' (%.2f)", text, conf)
                    return text, conf
            elif conf > 0.05:
                fallback.append((conf, text))

    h_img, w_img = deskewed.shape[:2]
    if w_img > 90:
        third = w_img // 3
        two_thirds = 2 * third
        lmr_texts: dict[str, str] = {}
        for img_v, label in base_variants:
            variants_l = [
                (img_v[:, :third],          f"{label}_L"),
                (img_v[:, third:two_thirds],f"{label}_M"),
                (img_v[:, two_thirds:],     f"{label}_R"),
            ]
            for sv, sl in variants_l:
                for text, conf, valid in _run_ocr(sv, sl):
                    if valid and conf > 0.08:
                        candidates.append((conf, text))
                    elif conf > 0.05:
                        fallback.append((conf, text))
                    if sl.endswith('_L') and text:
                        lmr_texts['L'] = lmr_texts.get('L', '') + text
                    elif sl.endswith('_M') and text:
                        lmr_texts['M'] = lmr_texts.get('M', '') + text
                    elif sl.endswith('_R') and text:
                        lmr_texts['R'] = lmr_texts.get('R', '') + text
        
        combined = _combine_lmr_text(lmr_texts)
        if combined and is_valid_ph_plate(normalize_plate(combined)):
            log.info("[OCR-CROP] Valid combined LMR: '%s'", combined)
            return normalize_plate(combined), 0.5

    if candidates:
        candidates.sort(reverse=True)
        by_text: dict[str, float] = {}
        for conf, text in candidates:
            by_text[text] = max(by_text.get(text, 0.0), conf)
        best_text = max(by_text.items(), key=lambda x: x[1])
        if is_valid_ph_plate(best_text[0]):
            log.info("[OCR-CROP] Best valid: '%s' (conf=%.3f)", best_text[0], best_text[1])
            return best_text[0], best_text[1]
        for candidate in extract_plate_candidates(best_text[0]):
            if is_valid_ph_plate(candidate):
                log.info("[OCR-CROP] Valid plate from candidate: '%s'", candidate)
                return candidate, best_text[1]
        log.info("[OCR-CROP] Best fallback: '%s' (conf=%.3f)", best_text[0], best_text[1])
        return best_text[0], best_text[1]

    if fallback:
        fallback.sort(reverse=True)
        by_text = {}
        for conf, text in fallback:
            by_text[text] = max(by_text.get(text, 0.0), conf)
        best_text = max(by_text.items(), key=lambda x: x[1])
        if is_valid_ph_plate(best_text[0]):
            log.info("[OCR-CROP] Valid fallback: '%s' (conf=%.3f)", best_text[0], best_text[1])
            return best_text[0], best_text[1]
        for candidate in extract_plate_candidates(best_text[0]):
            if is_valid_ph_plate(candidate):
                log.info("[OCR-CROP] Valid plate from fallback candidate: '%s'", candidate)
                return candidate, best_text[1]
        log.info("[OCR-CROP] Best fallback: '%s' (conf=%.3f)", best_text[0], best_text[1])
        return best_text[0], best_text[1]

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
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    scale = 400 / max(bw.shape[1], 1)
    up = cv2.resize(bw, (int(bw.shape[1] * scale), int(bw.shape[0] * scale)), interpolation=cv2.INTER_CUBIC)
    try:
        ocr = _get_ocr()
        raw = ocr.readtext(up, text_threshold=0.15, link_threshold=0.15, low_text=0.05, mag_ratio=1.5)
        log.info("[OCR-CROP][RAW-FB] raw regions: %d", len(raw))
        candidates: list[tuple[float, str]] = []
        for item in raw:
            if len(item) != 3:
                continue
            _, text, confidence = item
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
    Main entry point — detect + read Philippine license plates or unplated vehicles.

    Returns a list of dicts, each with:
        plate_text — the recognized plate number (None for unplated vehicles)
        vehicle_type — bicycle/e_bike/electric_scooter for unplated vehicles
        bbox       — relative bounding box {"x", "y", "width", "height"} (0–1)
        confidence — detection confidence
        requires_digital_id — True for unplated vehicles

    Returns an empty list when no vehicles are found.
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
        class_name = det.get("class_name", "")
        vehicle_type = det.get("vehicle_type")
        
        if requires_digital_id(class_name) and vehicle_type:
            results.append({
                "vehicle_type": vehicle_type,
                "bbox": det["bbox"],
                "confidence": det["score"],
                "plate_text": None,
                "requires_digital_id": True,
            })
            continue
        
        plate_text, conf = _ocr_crop(det["crop"], det.get("aspect_ratio", 1.0))
        if plate_text:
            results.append({
                "plate_text": plate_text,
                "bbox": det["bbox"],
                "confidence": conf,
                "vehicle_type": None,
                "requires_digital_id": False,
            })

    if results:
        log.info("[READ] Found %d detections: %s", len(results), results)
    return results
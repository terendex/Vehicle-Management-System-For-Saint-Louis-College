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

from .validator import is_valid_ph_plate, normalize_plate

log = logging.getLogger(__name__)

_TO_DIGIT = str.maketrans({
    'B': '8', 'O': '0', 'I': '1', 'L': '1', 'l': '1',
    'Z': '2', 'S': '5', 'G': '6', 'Q': '9', 'g': '9', 'q': '9',
    'D': '0', 'U': '0', 'A': '4', 'R': '2', 'T': '7', 'J': '1',
    'Y': '7', 'b': '6', 'o': '0', 'i': '1', 'z': '2', 's': '5',
    'g': '9', 'q': '9', 'd': '0',
    'H': '4',  # H -> 4 (rare but seen)
    'h': '4',
    'N': '4',  # N -> 4
    'n': '4',
})

_TO_LETTER = str.maketrans({
    '8': 'B', '0': 'O', '1': 'I', '2': 'Z', '5': 'S',
    '6': 'G', '9': 'Q', '4': 'A', '7': 'T', '3': 'E',
    '4': 'H',  # 4 -> H (for motorcycle plates)
    '4': 'N',  # 4 -> N
    '8': 'B',  # 8 -> B (already there)
    '1': 'L',  # 1 -> L (for I/L confusion)
})


def _correct_plate_chars(text: str) -> str:
    to_digit = text.translate(_TO_DIGIT)
    to_letter = text.translate(_TO_LETTER)
    if is_valid_ph_plate(to_digit):
        return to_digit
    if is_valid_ph_plate(to_letter):
        return to_letter
    # Try both translations combined for mixed cases
    mixed = to_digit.translate(_TO_LETTER)
    if is_valid_ph_plate(mixed):
        return mixed
    mixed2 = to_letter.translate(_TO_DIGIT)
    if is_valid_ph_plate(mixed2):
        return mixed2
    return text


# ── Lazy singletons ─────────────────────────────────────────────────

_ocr_reader: easyocr.Reader | None = None
_yolo_model = None                       # ultralytics.YOLO | None
_yolo_loaded = False                     # ensures we only try once

WEIGHTS_PATH = Path(__file__).resolve().parent / "weights" / "best.pt"


def _get_ocr():
    """Lazy-load EasyOCR (takes ~2 s on first call)."""
    global _ocr_reader
    if _ocr_reader is None:
        _ocr_reader = easyocr.Reader(["en"], gpu=False)
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
    # Sharpening via unsharp mask
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    sharp = cv2.addWeighted(gray, 1.8, blur, -0.8, 0)
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
    """
    Run the YOLO model on a BGR image.

    Returns a list of dicts, each with:
        crop  — the cropped plate region (BGR)
        bbox  — relative bounding box {x, y, width, height}  (0–1)
        score — detection confidence
    """
    model = _get_yolo()
    if model is None:
        log.warning("[DETECT] No YOLO model loaded — skipping detection")
        return []

    h, w = img.shape[:2]
    log.info("[DETECT] Running YOLO on %dx%d image, conf=%.2f", w, h, conf)
    results = model.predict(img, conf=conf, verbose=False)

    detections = []
    for r in results:
        log.info("[DETECT] YOLO returned %d boxes", len(r.boxes))
        for box in r.boxes:
            x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
            score = float(box.conf[0])
            cls_id = int(box.cls[0]) if hasattr(box, 'cls') else -1
            log.info("[DETECT] Box: (%.0f,%.0f)-(%.0f,%.0f) conf=%.3f cls=%d", x1, y1, x2, y2, score, cls_id)
            x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)

            box_w = x2 - x1
            box_h = y2 - y1
            aspect_ratio = box_w / max(box_h, 1)

            if score < 0.30 or aspect_ratio < 0.8 or aspect_ratio > 3.5:
                log.info(
                    "[DETECT] Dropped box: conf=%.3f aspect=%.2f", score, aspect_ratio,
                )
                continue

            # Motorcycle/tricycle plates are two-row (narrow & tall, aspect < 2.0)
            # so we need generous bottom padding to capture the second row of digits
            pad_x = int(box_w * 0.15)
            pad_y_top = int(box_h * 0.15)
            if aspect_ratio < 2.0:
                pad_y_bottom = int(box_h * 1.2)  # capture digits below the YOLO box
            else:
                pad_y_bottom = int(box_h * 0.15)

            cx1 = max(0, x1 - pad_x)
            cy1 = max(0, y1 - pad_y_top)
            cx2 = min(w, x2 + pad_x)
            cy2 = min(h, y2 + pad_y_bottom)

            log.info("[DETECT] aspect=%.2f → pad_x=%d pad_y_top=%d pad_y_bottom=%d crop=(%d,%d)-(%d,%d)",
                     aspect_ratio, pad_x, pad_y_top, pad_y_bottom, cx1, cy1, cx2, cy2)

            crop_w = cx2 - cx1
            crop_h = cy2 - cy1

            detections.append({
                "crop":  img[cy1:cy2, cx1:cx2],
                "bbox":  {
                    "x":      float(cx1 / w),
                    "y":      float(cy1 / h),
                    "width":  float(crop_w / w),
                    "height": float(crop_h / h),
                },
                "score": score,
                "aspect_ratio": aspect_ratio,
            })

    # Sort by confidence descending
    detections.sort(key=lambda d: d["score"], reverse=True)
    log.info("[DETECT] Total detections after YOLO: %d", len(detections))
    return detections


# ── OCR on a single plate crop ──────────────────────────────────────

def _ocr_crop(crop: np.ndarray, aspect_ratio: float = 1.0) -> tuple[str, float] | tuple[None, None]:
    """
    Run EasyOCR on a cropped plate image.

    Generates multiple augmented variants and ranks results.
    Returns (normalized_plate, confidence) or (None, None) on failure.
    """
    MIN_WIDTH = 320
    h_crop, w_crop = crop.shape[:2]

    if w_crop < MIN_WIDTH:
        scale = MIN_WIDTH / max(w_crop, 1)
        crop = cv2.resize(
            crop, (MIN_WIDTH, max(int(h_crop * scale), 20)),
            interpolation=cv2.INTER_CUBIC,
        )
        log.info("[OCR-CROP] Upscaled crop to %dx%d", crop.shape[1], crop.shape[0])

    deskewed = _deskew_plate(crop, aspect_ratio)
    ocr = _get_ocr()

    def _run_ocr(img, label):
        if img is None or getattr(img, "size", 0) == 0:
            return
        raw = ocr.readtext(
            img, text_threshold=0.3, link_threshold=0.3, low_text=0.2,
        )
        log.info("[OCR-CROP] %s: EasyOCR found %d text regions", label, len(raw))
        for item in raw:
            if len(item) == 3:
                _, text, confidence = item
            else:
                text, confidence = str(item[1]), 0.0
            text_raw = str(text).strip()
            log.info("[OCR-CROP]   %s: raw='%s' conf=%.3f", label, text_raw, confidence)
            text_clean = normalize_plate(text_raw)
            if not text_clean:
                continue
            corrected = _correct_plate_chars(text_clean)
            valid = is_valid_ph_plate(corrected)
            conf = float(confidence)
            if valid:
                conf *= 1.15
                conf = min(conf, 1.0)
            log.info("[OCR-CROP]   %s: '%s' -> '%s' conf=%.3f valid=%s",
                     label, text_clean, corrected, conf, valid)
            yield corrected, conf, valid

    candidates: list[tuple[float, str]] = []
    fallback: list[tuple[float, str]] = []

    clahe_clip = 2.0
    clahe_grid = 4
    clahe = cv2.createCLAHE(clipLimit=clahe_clip, tileGridSize=(clahe_grid, clahe_grid))
    h_img, w_img = deskewed.shape[:2]

    base_variants: list[tuple[np.ndarray, str]] = [
        (deskewed,          "raw"),
        (cv2.GaussianBlur(deskewed, (3, 3), 0), "blur3"),
    ]
    sharp_kernel = np.array([[-1, -1, -1], [-1, 9, -1], [-1, -1, -1]])
    base_variants.extend([
        (cv2.filter2D(deskewed, -1, sharp_kernel), "sharpen"),
        (cv2.addWeighted(deskewed, 1.6, cv2.GaussianBlur(deskewed, (0, 0), 3), -0.6, 0), "unsharp"),
    ])
    gray = cv2.cvtColor(deskewed, cv2.COLOR_BGR2GRAY) if len(deskewed.shape) == 3 else deskewed
    enhanced = cv2.cvtColor(clahe.apply(gray), cv2.COLOR_GRAY2BGR)
    base_variants.append((enhanced, "clahe"))
    binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    base_variants.append((cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR), "otsu"))

    for img_v, label in base_variants:
        for text, conf, valid in _run_ocr(img_v, label):
            if valid and conf > 0.15:
                candidates.append((conf, text))
            elif conf > 0.12:
                fallback.append((conf, text))

    if w_img > 90:
        third = w_img // 3
        two_thirds = 2 * third
        for img_v, label in base_variants:
            variants_l = [
                (img_v[:, :third],          f"{label}_L"),
                (img_v[:, third:two_thirds],f"{label}_M"),
                (img_v[:, two_thirds:],     f"{label}_R"),
            ]
            for sv, sl in variants_l:
                for text, conf, valid in _run_ocr(sv, sl):
                    if valid and conf > 0.12:
                        candidates.append((conf, text))
                    elif conf > 0.10:
                        fallback.append((conf, text))

    if candidates:
        candidates.sort(reverse=True)
        by_text: dict[str, float] = {}
        for conf, text in candidates:
            by_text[text] = max(by_text.get(text, 0.0), conf)
        best_text = max(by_text.items(), key=lambda x: x[1])
        log.info("[OCR-CROP] Best valid: '%s' (conf=%.3f)", best_text[0], best_text[1])
        return best_text[0], best_text[1]

    if fallback:
        fallback.sort(reverse=True)
        by_text = {}
        for conf, text in fallback:
            by_text[text] = max(by_text.get(text, 0.0), conf)
        best_text = max(by_text.items(), key=lambda x: x[1])
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
        raw = ocr.readtext(up, text_threshold=0.2, link_threshold=0.2, low_text=0.1)
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
            if is_valid_ph_plate(text_c):
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

    Returns an empty list when no plates are found.
    """
    img = _decode(image_bytes)
    if img is None:
        log.error("[READ] Failed to decode image bytes (len=%d)", len(image_bytes))
        return []

    h, w = img.shape[:2]
    log.info("[READ] Decoded image: %dx%d", w, h)
    results: list[dict] = []

    # ── Stage 1: Try YOLO detection first ───────────────────────────
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
        log.info("[READ] Stage 1 (YOLO+OCR) found %d plates: %s", len(results), [r["plate_text"] for r in results])
        return results

    log.info("[READ] YOLO found %d boxes but OCR failed on all — falling back to full-frame OCR", len(detections))

    for det in detections:
        plate_text, conf = _run_raw_ocr_fallback(det["crop"])
        if plate_text:
            results.append({
                "plate_text": plate_text,
                "bbox": det["bbox"],
                "confidence": conf,
            })
    if results:
        log.info("[READ] Raw fallback recovered %d plates", len(results))
        return results

    # ── Stage 2: Fallback — OCR on the full image ───────────────────
    log.info("[READ] YOLO found no valid plates — falling back to full-frame OCR")
    ocr = _get_ocr()
    ocr_results = ocr.readtext(img)

    log.info("[READ] Full-frame OCR found %d text regions", len(ocr_results))
    for (bbox, text, confidence) in ocr_results:
        valid = is_valid_ph_plate(text)
        log.info("[READ]   text=%r conf=%.3f valid_ph=%s", text, confidence, valid)
        if confidence > 0.2 and valid:
            xs = [p[0] for p in bbox]
            ys = [p[1] for p in bbox]
            x_min, x_max = min(xs), max(xs)
            y_min, y_max = min(ys), max(ys)

            results.append({
                "plate_text": normalize_plate(text),
                "bbox": {
                    "x":      float(x_min / w),
                    "y":      float(y_min / h),
                    "width":  float((x_max - x_min) / w),
                    "height": float((y_max - y_min) / h),
                },
                "confidence": confidence,
            })

    log.info("[READ] Final results: %d plates: %s", len(results), [r["plate_text"] for r in results])
    return results
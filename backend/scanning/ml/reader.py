from __future__ import annotations

import cv2
import numpy as np
import easyocr
from .validator import is_valid_ph_plate, normalize_plate

reader = easyocr.Reader(['en'], gpu=False)

def preprocess(image_bytes: bytes) -> np.ndarray:
    nparr = np.frombuffer(image_bytes, np.uint8)
    img   = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    gray  = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur  = cv2.GaussianBlur(gray, (5, 5), 0)
    _, thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return thresh

def read_plate(image_bytes: bytes) -> str | None:
    processed  = preprocess(image_bytes)
    results    = reader.readtext(processed)
    candidates = []
    for (_, text, confidence) in results:
        if confidence > 0.5 and is_valid_ph_plate(text):
            candidates.append((confidence, normalize_plate(text)))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]
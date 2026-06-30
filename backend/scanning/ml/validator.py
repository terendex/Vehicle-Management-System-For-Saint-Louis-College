import re

PH_PLATE_PATTERNS = [
    # ── 7-character plates ───────────────────────────────────────
    re.compile(r'^[A-Z]{3}[0-9]{4}$'),           # ABC1234  — private/PUV/govt (post-2014)

    # ── 6-character plates ───────────────────────────────────────
    re.compile(r'^[A-Z]{3}[0-9]{3}$'),           # ABC123   — pre-2014 car
    re.compile(r'^[0-9]{3}[A-Z]{3}$'),           # 123ABC   — private/PUV/govt
    re.compile(r'^[A-Z][0-9]{3}[A-Z]{2}$'),      # N123BC   — private/PUV/govt
    re.compile(r'^[A-Z]{2}[0-9]{3}[A-Z]$'),      # NB123C   — private/PUV/govt
    re.compile(r'^[A-Z][0-9]{4}[A-Z]$'),         # N1234C   — private

    # ── Letters-both-ends variants ───────────────────────────────
    re.compile(r'^[A-Z]{1,2}[0-9]{4}[A-Z]{1,2}$'),  # AB1234C / A1234BC
    re.compile(r'^[A-Z]{1,2}[0-9]{3}[A-Z]{1,2}$'),  # AB123C  / A123BC

    # ── Diplomatic (7 digits) ────────────────────────────────────
    re.compile(r'^[0-9]{7}$'),                    # 0011234

    # ── Older / other formats ────────────────────────────────────
    re.compile(r'^[A-Z]{2}\s?[0-9]{4}$'),        # AB1234
    re.compile(r'^[A-Z]{2}\s?[0-9]{5}$'),        # AB12345
    re.compile(r'^[0-9]{3}[A-Z]{1,3}$'),         # 123AB
    re.compile(r'^[0-9]{2}[A-Z]{3,4}$'),         # 12ABCD
    re.compile(r'^[0-9]{4}$'),                    # 1234 (old motorcycle)
    re.compile(r'^[0-9]{1,3}[A-Z]{2,4}[0-9]{0,2}$'),
    re.compile(r'^[A-Z]{1,3}[0-9]{1,6}$'),
]

def is_valid_ph_plate(text: str) -> bool:
    cleaned = normalize_plate(text)
    if len(cleaned) < 4:   # shortest real PH plate is 4 chars (old motorcycle)
        return False
    return any(p.match(cleaned) for p in PH_PLATE_PATTERNS)

def normalize_plate(text: str) -> str:
    return re.sub(r'[^A-Z0-9]', '', text.upper())

def extract_plate_candidates(text: str) -> list[str]:
    """Extract potential plate numbers from OCR text."""
    text = text.upper().strip()
    
    to_digits_map = {
        'A': '4', 'B': '8', 'D': '0', 'G': '6', 'I': '1', 'J': '1', 'L': '1',
        'O': '0', 'Q': '9', 'R': '2', 'S': '5', 'T': '7', 'U': '0', 'Z': '2',
    }

    to_letters_map = {
        '0': 'O', '1': 'I', '2': 'Z', '3': 'E', '4': 'A', '5': 'S', '6': 'G',
        '7': 'T', '8': 'B', '9': 'Q',
    }

    bracket_digits = {'[': ['4', '1'], ']': ['1', '4']}
    bracket_letters = {'[': ['I', 'L'], ']': ['I', 'L']}

    def translate_chars(t, mapping):
        return "".join(mapping.get(c, c) for c in t)

    variants = [text]
    for char in ['[', ']']:
        if char in text:
            new_variants = []
            for v in variants:
                new_variants.append(v.replace(char, ''))
                for replacement in bracket_digits.get(char, []):
                    new_variants.append(v.replace(char, replacement))
                for replacement in bracket_letters.get(char, []):
                    new_variants.append(v.replace(char, replacement))
            variants = list(set(new_variants))

    normalized_variants = []
    for v in variants:
        v_clean = re.sub(r'[^A-Z0-9]', '', v)
        if v_clean:
            normalized_variants.append(v_clean)
    normalized_variants = list(set(normalized_variants))

    def tl(chars, mapping):
        return translate_chars(chars, mapping)

    def L(s): return tl(s, to_letters_map)   # force-convert to letters
    def D(s): return tl(s, to_digits_map)     # force-convert to digits

    candidates = []
    for v in normalized_variants:
        candidates.append(v)

        if len(v) == 6:
            # All official 6-char Philippine plate layouts:
            candidates.append(L(v[:3]) + D(v[3:]))          # ABC123  — 3L+3D
            candidates.append(D(v[:3]) + L(v[3:]))          # 123ABC  — 3D+3L
            candidates.append(L(v[0]) + D(v[1:4]) + L(v[4:]))  # N123BC  — 1L+3D+2L
            candidates.append(L(v[:2]) + D(v[2:5]) + L(v[5]))  # NB123C  — 2L+3D+1L
            candidates.append(L(v[0]) + D(v[1:5]) + L(v[5]))   # N1234C  — 1L+4D+1L

            # H/W → M misread fix on the letter portions
            for f in list(candidates):
                if any(c in f for c in ('H', 'W')):
                    candidates.append(f.replace('H', 'M').replace('W', 'M'))

        elif len(v) == 7:
            # All official 7-char Philippine plate layouts:
            candidates.append(L(v[:3]) + D(v[3:]))              # ABC1234 — 3L+4D
            candidates.append(D(v[:3]) + L(v[3:]))              # 123ABCD — 3D+4L (rare)
            candidates.append(L(v[:2]) + D(v[2:6]) + L(v[6]))  # AB1234C — 2L+4D+1L
            candidates.append(L(v[0]) + D(v[1:5]) + L(v[5:]))  # A1234BC — 1L+4D+2L
            candidates.append(L(v[:2]) + D(v[2:]))              # AB12345 — 2L+5D
            candidates.append(D(v))                              # 0011234 — diplomatic (7D)

    # Legacy fallback candidate generation
    for v in normalized_variants:
        letters = re.findall(r'[A-Z]+', v)
        digits = re.findall(r'[0-9]+', v)
        if len(letters) >= 2 and len(digits) >= 2:
            candidates.append(''.join(letters[:3] + digits[:4]))
            candidates.append(''.join(letters[:2] + digits[:4]))
            candidates.append(''.join(letters[:3] + digits[:3]))
            candidates.append(''.join(letters[:1] + digits[:2] + letters[1:4]))
            candidates.append(''.join(digits[:3] + letters[:3]))
            candidates.append(''.join(digits[:3] + letters[:4]))
            candidates.append(''.join(digits[:3] + letters[:2]))
        if len(v) >= 5:
            candidates.append(v[:3] + v[-3:])
            candidates.append(v[:2] + v[-3:])
            if v[0].isdigit() and v[1].isdigit() and v[2].isdigit():
                candidates.append(v[:3] + v[3:])

    return list(set(candidates))

def combine_multiline_text(text_results: list) -> tuple[str, float]:
    """
    Sorts and groups PaddleOCR text regions to handle two-row (motorcycle) or multi-region plates.
    Each item in text_results is: (bbox, text, conf)
    where bbox is list of 4 points: [[x0, y0], [x1, y1], [x2, y2], [x3, y3]]
    """
    if not text_results:
        return "", 0.0
    
    valid_items = []
    for item in text_results:
        if len(item) >= 2:
            bbox = item[0]
            text = str(item[1]).strip()
            conf = float(item[2]) if len(item) > 2 else 1.0
            if text:
                valid_items.append((bbox, text, conf))
                
    if not valid_items:
        return "", 0.0

    processed_items = []
    for bbox, text, conf in valid_items:
        try:
            xs = [pt[0] for pt in bbox]
            ys = [pt[1] for pt in bbox]
            x_min, x_max = min(xs), max(xs)
            y_min, y_max = min(ys), max(ys)
            
            cx = (x_min + x_max) / 2.0
            cy = (y_min + y_max) / 2.0
            height = max(1, y_max - y_min)
            
            processed_items.append({
                "bbox": bbox,
                "text": text,
                "conf": conf,
                "cx": cx,
                "cy": cy,
                "height": height,
                "y_min": y_min
            })
        except Exception:
            processed_items.append({
                "bbox": bbox,
                "text": text,
                "conf": conf,
                "cx": 0,
                "cy": 0,
                "height": 1,
                "y_min": 0
            })

    processed_items.sort(key=lambda item: item["cy"])

    rows = []
    for item in processed_items:
        placed = False
        for row in rows:
            rep = row[0]
            limit = 0.5 * (rep["height"] + item["height"])
            if abs(rep["cy"] - item["cy"]) < limit:
                row.append(item)
                placed = True
                break
        if not placed:
            rows.append([item])

    combined_words = []
    total_conf = 0.0
    word_count = 0
    
    rows.sort(key=lambda r: sum(item["cy"] for item in r) / len(r))
    
    for row in rows:
        row.sort(key=lambda item: item["cx"])
        for item in row:
            combined_words.append(item["text"])
            total_conf += item["conf"]
            word_count += 1

    combined_text = " ".join(combined_words)
    avg_conf = total_conf / max(1, word_count)
    return combined_text, avg_conf
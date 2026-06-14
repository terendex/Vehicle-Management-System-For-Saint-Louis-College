import re

PH_PLATE_PATTERNS = [
    re.compile(r'^[A-Z]{3}\s?[0-9]{4}$'),
    re.compile(r'^[A-Z]{3}\s?[0-9]{3}$'),
    re.compile(r'^[A-Z]{2}\s?[0-9]{4}$'),
    re.compile(r'^[A-Z]{2}\s?[0-9]{5}$'),
    re.compile(r'^[0-9]{3}[A-Z]{3}$'),
    re.compile(r'^[A-Z]{1,3}[0-9]{1,6}$'),
    re.compile(r'^[0-9]{4}$'),
    re.compile(r'^[A-Z]{1,4}[0-9]{1,4}[A-Z]?$'),
    re.compile(r'^[A-Z][0-9]{2}[A-Z]{3}$'),
    re.compile(r'^[0-9]{3}[A-Z]{1,3}$'),
    re.compile(r'^[0-9]{2}[A-Z]{3,4}$'),
    re.compile(r'^[0-9]{1,3}[A-Z]{2,4}[0-9]{0,2}$'),
]

def is_valid_ph_plate(text: str) -> bool:
    cleaned = normalize_plate(text)
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

    candidates = []
    for v in normalized_variants:
        candidates.append(v)
        if len(v) == 6:
            # 3 letters + 3 digits
            f1 = translate_chars(v[:3], to_letters_map) + translate_chars(v[3:], to_digits_map)
            candidates.append(f1)
            # 3 digits + 3 letters (e.g. 474ASM)
            digits_part = translate_chars(v[:3], to_digits_map)
            
            letters_part = translate_chars(v[3:], to_letters_map)
            letters_part_variants = [letters_part]
            # H is commonly misread for M in OCR
            for idx_l in range(len(v[3:])):
                if v[3 + idx_l] in ('H', '4', 'W'):
                    variant = list(letters_part)
                    variant[idx_l] = 'M'
                    letters_part_variants.append(''.join(variant))
            for lp in letters_part_variants:
                candidates.append(digits_part + lp)
                
            # 2 letters + 4 digits
            f3 = translate_chars(v[:2], to_letters_map) + translate_chars(v[2:], to_digits_map)
            candidates.append(f3)
            
        elif len(v) == 7:
            # 3 letters + 4 digits
            f1 = translate_chars(v[:3], to_letters_map) + translate_chars(v[3:], to_digits_map)
            candidates.append(f1)
            # 2 letters + 5 digits
            f2 = translate_chars(v[:2], to_letters_map) + translate_chars(v[2:], to_digits_map)
            candidates.append(f2)

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
    Sorts and groups EasyOCR text regions to handle two-row (motorcycle) or multi-region plates.
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
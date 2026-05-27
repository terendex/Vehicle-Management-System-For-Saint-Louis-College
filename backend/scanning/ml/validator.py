import re

PH_PATTERNS = [
    re.compile(r'^[A-Z]{3}\s?\d{3,4}$'),
    re.compile(r'^[A-Z]\d[A-Z]\s?\d[A-Z]\d$'),
    re.compile(r'^\d{2}-?\d{4}$'),
]

def is_valid_ph_plate(text: str) -> bool:
    cleaned = text.upper().strip().replace('-', '')
    return any(p.match(cleaned) for p in PH_PATTERNS)

def normalize_plate(text: str) -> str:
    return text.upper().strip().replace(' ', '').replace('-', '')
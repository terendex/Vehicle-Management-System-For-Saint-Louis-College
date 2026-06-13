import re

PH_PLATE_PATTERNS = [
    re.compile(r'^[A-Z]{3}\s?[0-9]{4}$'),      # NAA 1234 (current private vehicle)
    re.compile(r'^[A-Z]{3}\s?[0-9]{3}$'),      # ABC 123 (older format)
    re.compile(r'^[A-Z]{2}\s?[0-9]{4}$'),      # AB 1234
    re.compile(r'^[A-Z]{2}\s?[0-9]{5}$'),      # AB 12345 (some motorcycle plates)
    re.compile(r'^[0-9]{3}[A-Z]{3}$'),         # 123ABC (motorcycle style)
    re.compile(r'^[A-Z]{1,3}[0-9]{1,6}$'),     # Temporary/conduction sticker variants
    re.compile(r'^[0-9]{4}$'),                 # Diplomatic plate numbers only
]

def is_valid_ph_plate(text: str) -> bool:
    cleaned = text.upper().strip().replace('-', '').replace(' ', '')
    return any(p.match(cleaned) for p in PH_PLATE_PATTERNS)

def normalize_plate(text: str) -> str:
    return text.upper().strip().replace(' ', '').replace('-', '')
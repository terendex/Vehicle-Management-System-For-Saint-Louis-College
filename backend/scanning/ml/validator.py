import re

PH_PATTERNS = [
    re.compile(r'^[A-Z]{3}\s?\d{3,4}$'),           # Car: ABC 123 / ABC1234
    re.compile(r'^[A-Z]\d[A-Z]\s?\d[A-Z]\d$'),     # Car: A1B2C3
    re.compile(r'^\d{2}-?\d{4}$'),                  # Old government: 12-3456
    re.compile(r'^\d{3}[A-Z]{3}$'),                 # Motorcycle/tricycle: 474ASM
    re.compile(r'^\d{3}\s?[A-Z]{3}$'),              # Motorcycle/tricycle with space: 474 ASM
    re.compile(r'^[A-Z]{2}\d{4,5}$'),               # Motorcycle: AB12345
    re.compile(r'^\d{4}[A-Z]{2}$'),                 # Some regional motorcycle: 1234AB
]

def is_valid_ph_plate(text: str) -> bool:
    cleaned = text.upper().strip().replace('-', '').replace(' ', '')
    return any(p.match(cleaned) for p in PH_PATTERNS)

def normalize_plate(text: str) -> str:
    return text.upper().strip().replace(' ', '').replace('-', '')
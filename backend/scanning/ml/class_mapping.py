"""
Class mapping configuration for vehicle detection datasets.

Canonical classes:
  license_plate — the plate itself (triggers OCR)
  vehicle       — car, jeep, jeepney, bus, truck, van, SUV, pickup (4-wheel+)
  motor         — motorcycle, motorbike, tricycle (2–3 wheel motorized)
  ebike         — electric bicycle (requires digital ID, no plate)
  escooter      — electric scooter (requires digital ID, no plate)
"""
from __future__ import annotations
from typing import Optional

LICENSE_PLATE_CLASS = "license_plate"

# Classes that need a digital ID instead of a license plate
VEHICLE_TYPE_CLASSES = ["ebike", "escooter"]

CLASS_MAPPING = {
    # ── License plate ────────────────────────────────────────────
    "license_plate":    "license_plate",
    "plate":            "license_plate",
    "lp":               "license_plate",

    # ── Vehicle (4-wheel and up) ─────────────────────────────────
    "vehicle":          "vehicle",
    "car":              "vehicle",
    "Car":              "vehicle",
    "automobile":       "vehicle",
    "jeep":             "vehicle",
    "jeepney":          "vehicle",
    "Jeepney":          "vehicle",
    "bus":              "vehicle",
    "Bus":              "vehicle",
    "truck":            "vehicle",
    "Truck":            "vehicle",
    "van":              "vehicle",
    "Van":              "vehicle",
    "suv":              "vehicle",
    "SUV":              "vehicle",
    "pickup":           "vehicle",
    "minivan":          "vehicle",
    "4wheel":           "vehicle",

    # ── Motor (motorcycle, tricycle) ─────────────────────────────
    "motor":            "motor",
    "motorcycle":       "motor",
    "Motorcycle":       "motor",
    "motorbike":        "motor",
    "Motorbike":        "motor",
    "tricycle":         "motor",
    "Tricycle":         "motor",
    "trike":            "motor",
    "bike":             "motor",
    "Bike":             "motor",
    "bicycle":          "motor",   # existing bicycle labels map to motor
    "Bicycle":          "motor",

    # ── E-bike ───────────────────────────────────────────────────
    "ebike":            "ebike",
    "e_bike":           "ebike",
    "EBike":            "ebike",
    "E-Bike":           "ebike",
    "Electric Bike":    "ebike",
    "electric_bike":    "ebike",
    "electric bike":    "ebike",

    # ── Electric scooter ─────────────────────────────────────────
    "escooter":              "escooter",
    "electric_scooter":      "escooter",
    "E-Scooter":             "escooter",
    "E-scooter":             "escooter",
    "Electric Scooter":      "escooter",
    "ElectricScooter":       "escooter",
    "Electric scooter":      "escooter",
    "scooter":               "escooter",
    "Scooter":               "escooter",
    "e-scooter":             "escooter",
    "motorized_scooter":     "escooter",
    "electricscooter":       "escooter",
    "kickboard":             "escooter",
}

# Labels found in datasets that are deliberately excluded
EXCLUDED_LABELS = {
    "person",
    "helmet",
    "no_helmet",
    "rickshaw",
    "auto rickshaw",
    "Skateboard",
    "skateboard",
    "object",
}

# Reverse mapping: canonical name → all raw aliases (for reporting)
REVERSE_CLASS_MAPPING: dict[str, list[str]] = {
    "license_plate": ["license_plate", "plate", "lp"],
    "vehicle": [
        "vehicle", "car", "Car", "automobile", "jeep", "jeepney",
        "bus", "Bus", "truck", "Truck", "van", "Van", "suv", "SUV",
        "pickup", "minivan", "4wheel",
    ],
    "motor": [
        "motor", "motorcycle", "Motorcycle", "motorbike", "Motorbike",
        "tricycle", "Tricycle", "trike", "bike", "Bike", "bicycle", "Bicycle",
    ],
    "ebike": ["ebike", "e_bike", "EBike", "E-Bike", "Electric Bike", "electric_bike"],
    "escooter": [
        "escooter", "electric_scooter", "E-Scooter", "E-scooter",
        "Electric Scooter", "ElectricScooter", "scooter", "Scooter",
        "e-scooter", "motorized_scooter", "electricscooter", "kickboard",
    ],
}

# Single source-of-truth for per-class pipeline behavior
VEHICLE_TYPE_BEHAVIOR = {
    "license_plate": {"requires_digital_id": False, "triggers_ocr": True},
    "vehicle":       {"requires_digital_id": False, "triggers_ocr": True},
    "motor":         {"requires_digital_id": False, "triggers_ocr": True},
    "ebike":         {"requires_digital_id": True,  "triggers_ocr": False},
    "escooter":      {"requires_digital_id": True,  "triggers_ocr": False},
}


def get_standard_class_name(raw_label: str) -> Optional[str]:
    """Return the canonical class name for a raw dataset label, or None if unknown."""
    return CLASS_MAPPING.get(raw_label)


def is_excluded_label(raw_label: str) -> bool:
    """Return True if this label should be dropped from training data."""
    return raw_label in EXCLUDED_LABELS

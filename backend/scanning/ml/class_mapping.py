"""
Class mapping configuration for merging unplated vehicle datasets.

This module defines how to map various label names from different datasets
into our standardized class names: bicycle, e_bike, electric_scooter.
"""
from __future__ import annotations
from typing import Optional

VEHICLE_TYPE_CLASSES = ["bicycle", "e_bike", "electric_scooter"]
LICENSE_PLATE_CLASS = "license_plate"

CLASS_MAPPING = {
    # ── Bicycle labels ──────────────────────────────────────────
    "bicycle":       "bicycle",
    "bike":          "bicycle",
    "Bike":          "bicycle",
    "Bicycle":       "bicycle",
    "Person-Bike":   "bicycle",   # review: paired detection label — included
    "Car-Bike":      "bicycle",   # review: paired detection label — included
    "MC-Bike":       "bicycle",   # review: paired detection label — included
    "Truck-Bike":    "bicycle",   # review: paired detection label — included
    "Van-Bike":      "bicycle",   # review: paired detection label — included
    "bicycles-escooters-skateboards": "bicycle",


    # ── E-bike labels ────────────────────────────────────────────
    "e_bike":        "e_bike",
    "ebike":         "e_bike",
    "EBike":         "e_bike",
    "E-Bike":        "e_bike",
    "Electric Bike": "e_bike",
    "electric_bike": "e_bike",

    # ── Electric scooter labels ───────────────────────────────────
    "electric_scooter":  "electric_scooter",
    "escooter":          "electric_scooter",
    "E-Scooter":         "electric_scooter",
    "E-scooter":         "electric_scooter",
    "Electric Scooter":  "electric_scooter",
    "ElectricScooter":   "electric_scooter",
    "Electric scooter":  "electric_scooter",
    "scooter":           "electric_scooter",
    "Scooter":           "electric_scooter",
    "e-scooter":         "electric_scooter",
    "motorized_scooter": "electric_scooter",
    "electricscooter":   "electric_scooter",
    "kickboard":         "electric_scooter",
}

# Labels found in datasets that are deliberately excluded — logged for review
EXCLUDED_LABELS = {
    "person",
    "car",
    "motorcycle",
    "truck",
    "van",
    "bus",
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
    "bicycle": [
        "bicycle", "bike", "Bike", "Bicycle",
        "Person-Bike", "Car-Bike", "MC-Bike", "Truck-Bike", "Van-Bike",
    ],
    "e_bike": [
        "e_bike", "ebike", "EBike", "E-Bike", "Electric Bike", "electric_bike",
    ],
    "electric_scooter": [
        "electric_scooter", "escooter", "E-Scooter", "E-scooter", "Electric Scooter",
        "ElectricScooter", "Electric scooter", "scooter", "Scooter",
        "e-scooter", "motorized_scooter", "electricscooter", "kickboard",
    ],
}

# ── Single source-of-truth for per-class behavior ─────────────────────────────
# All pipeline code reads from this dict — never scatter if/else across files.
VEHICLE_TYPE_BEHAVIOR = {
    "bicycle":          {"requires_digital_id": True,  "triggers_ocr": False},
    "e_bike":           {"requires_digital_id": True,  "triggers_ocr": False},
    "electric_scooter": {"requires_digital_id": True,  "triggers_ocr": False},
    "license_plate":    {"requires_digital_id": False, "triggers_ocr": True},
    "vehicle":          {"requires_digital_id": False, "triggers_ocr": True},
    "motorcycle":       {"requires_digital_id": False, "triggers_ocr": True},
}


def get_standard_class_name(raw_label: str) -> Optional[str]:
    """Return the canonical class name for a raw dataset label, or None if unknown."""
    return CLASS_MAPPING.get(raw_label)


def is_excluded_label(raw_label: str) -> bool:
    """Return True if this label should be dropped from training data."""
    return raw_label in EXCLUDED_LABELS
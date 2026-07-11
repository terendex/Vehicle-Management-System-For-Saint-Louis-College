"""
Class mapping configuration for vehicle detection datasets.

Canonical classes:
  license_plate — the plate itself (triggers OCR)
  vehicle       — ALL motorized vehicles unified into one class:
                  car, jeep, jeepney, bus, truck, van, SUV, pickup,
                  motorcycle, motorbike, tricycle
"""
from __future__ import annotations
from typing import Optional

LICENSE_PLATE_CLASS = "license_plate"
VEHICLE_CLASS       = "vehicle"

# Classes that need a digital ID instead of a license plate (none supported)
VEHICLE_TYPE_CLASSES: list[str] = []

CLASS_MAPPING = {
    # ── License plate ────────────────────────────────────────────
    "license_plate":    "license_plate",
    "plate":            "license_plate",
    "lp":               "license_plate",

    # ── Vehicle (everything motorized, unified) ──────────────────
    "vehicle":          "vehicle",
    "vehicles":         "vehicle",
    "Vehicle":          "vehicle",
    "Vehicles":         "vehicle",
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
    "motor":            "vehicle",
    "motorcycle":       "vehicle",
    "Motorcycle":       "vehicle",
    "motorbike":        "vehicle",
    "Motorbike":        "vehicle",
    "tricycle":         "vehicle",
    "Tricycle":         "vehicle",
    "trike":            "vehicle",
    "bike":             "vehicle",
    "Bike":             "vehicle",
    "bicycle":          "vehicle",
    "Bicycle":          "vehicle",
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
    # Unplated vehicle types — not supported
    "ebike", "e_bike", "EBike", "E-Bike", "Electric Bike", "electric_bike", "electric bike",
    "escooter", "electric_scooter", "E-Scooter", "E-scooter", "Electric Scooter",
    "ElectricScooter", "scooter", "Scooter", "e-scooter", "motorized_scooter",
    "electricscooter", "kickboard",
}

# Reverse mapping: canonical name → all raw aliases (for reporting)
REVERSE_CLASS_MAPPING: dict[str, list[str]] = {
    "license_plate": ["license_plate", "plate", "lp"],
    "vehicle": [
        "vehicle", "vehicles", "Vehicle", "Vehicles", "car", "Car",
        "automobile", "jeep", "jeepney", "Jeepney",
        "bus", "Bus", "truck", "Truck", "van", "Van", "suv", "SUV",
        "pickup", "minivan", "4wheel",
        "motor", "motorcycle", "Motorcycle", "motorbike", "Motorbike",
        "tricycle", "Tricycle", "trike", "bike", "Bike", "bicycle", "Bicycle",
    ],
}

# Single source-of-truth for per-class pipeline behavior
VEHICLE_TYPE_BEHAVIOR = {
    "license_plate": {"triggers_ocr": True},
    "vehicle":       {"triggers_ocr": True},
}


def get_standard_class_name(raw_label: str) -> Optional[str]:
    """Return the canonical class name for a raw dataset label, or None if unknown."""
    return CLASS_MAPPING.get(raw_label)


def is_excluded_label(raw_label: str) -> bool:
    """Return True if this label should be dropped from training data."""
    return raw_label in EXCLUDED_LABELS

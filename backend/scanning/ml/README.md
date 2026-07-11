# 🚗 Vehicle & License Plate Detection — ML Guide

## Overview

The pipeline uses **two independent YOLOv8 models**:

| Model | Weights (canonical path) | Classes | Status |
|-------|--------------------------|---------|--------|
| **Plate detector** | `runs/plate_detector/weights/best.pt` | `license_plate` | Trained — required, do not touch |
| **Vehicle detector** | `runs/vehicle_detector/weights/best.pt` | `vehicle` (single unified class) | To be trained from Roboflow dataset |

**Policy:** every motorized vehicle — car, motorcycle, bus, truck, jeepney,
tricycle… — is ONE class: `vehicle`. No per-type distinction anywhere in
datasets, labels, or detection. License plates are handled exclusively by the
separate plate detector and its own dataset.

Until the vehicle model is trained, `detection.py` runs in plate-only mode and
picks up the weights automatically once they exist.

```
scanning/ml/
├── detection.py           # Runtime: loads both models, detect_plates()
├── reader.py / ocr.py     # Plate OCR (PaddleOCR)
├── validator.py           # Philippine plate format validation
├── class_mapping.py       # Any raw label alias → license_plate | vehicle
├── train.py               # Vehicle-model trainer (single class)
├── download_datasets.py   # Merge extra Roboflow/local datasets → vehicle_ds
├── auto_label.py          # Auto-label frames: 0=license_plate, 1=vehicle
├── video_to_labeled.py    # Video → frames → auto-labels
├── prepare_training.py    # Auto-labeled frames → vehicle_ds (vehicle-only)
├── vehicle_ds/            # Vehicle dataset (Roboflow YOLOv8 export lands here)
├── dataset/               # Plate dataset (separate — plate model only)
└── runs/
    ├── plate_detector/    # Trained plate model (KEEP)
    └── vehicle_detector/  # Vehicle model training output (auto-created)
```

---

## Training the vehicle model

### 1. From a Roboflow export (primary path)

Export the dataset from Roboflow in **YOLOv8 format**, then from `backend/`:

```bash
# From a zip (extracts into vehicle_ds/, then trains):
python -m scanning.ml.train --zip path/to/roboflow-export.zip

# Or unzip into scanning/ml/vehicle_ds/ yourself and run:
python -m scanning.ml.train --epochs 100 --batch 16 --model-size s
```

Whatever class names the dataset uses (car, motorcycle, bus…), labels are
remapped automatically to the single class `0 = vehicle` via
`class_mapping.py`. Non-vehicle labels (person, e-scooters, plates) are
dropped. Output goes straight to `runs/vehicle_detector/weights/best.pt` —
live on the next server start, no copy step.

> ⚠️ `--zip` wipes `vehicle_ds/` first. Ingest the main export **before**
> merging extras (steps below).

### 2. Merging extra datasets (optional)

```bash
# Additional Roboflow datasets (repeatable):
python -m scanning.ml.download_datasets --api-key KEY --dataset workspace/project/1

# A local YOLOv8-format dataset folder from anywhere:
python -m scanning.ml.download_datasets --local path/to/dataset
```

### 3. From campus camera footage (optional)

```bash
# Video → frames → auto-labels (plates via plate_detector, vehicles via COCO):
python -m scanning.ml.video_to_labeled --video-dir media/ml_video

# Auto-labeled frames → vehicle-only dataset merged into vehicle_ds/:
python -m scanning.ml.prepare_training

# Then (re)train:
python -m scanning.ml.train
```

### Model sizes

| Size | Flag | Speed | Accuracy | GPU VRAM |
|------|------|-------|----------|----------|
| Nano | `--model-size n` | ⚡ Fastest | Good | ~2 GB |
| Small | `--model-size s` | Fast | Better | ~4 GB |
| Medium | `--model-size m` | Moderate | Best | ~6 GB |

> 💡 No GPU? Training works on CPU — use `--model-size n --batch 8`.

---

## The plate detector (do not touch)

The plate model is already trained and lives at
`runs/plate_detector/weights/best.pt`. Its dataset (`dataset/`) uses the
single class `0 = license_plate`. Nothing in the vehicle workflow reads or
writes either of them.

---

## How the runtime pipeline works

```
Camera frame
     │
     ├──────────────────────────────┐
     ▼                              ▼
┌──────────────────┐      ┌──────────────────────┐
│  Plate detector  │      │  Vehicle detector    │
│  license_plate   │      │  vehicle (1 class)   │
│  (required)      │      │  (optional until     │
└────────┬─────────┘      │   trained)           │
         │  crop           └──────────┬───────────┘
         ▼                            ▼
┌──────────────────┐        tracking bboxes
│  PaddleOCR       │
└────────┬─────────┘
         ▼
┌──────────────────┐
│  PH Validator    │
└────────┬─────────┘
         ▼
   (plate_text, bbox)
```

---

## Sharing weights (Cloudflare R2)

```bash
python manage.py sync_ml_weights --push   # upload local weights
python manage.py sync_ml_weights --pull   # download weights
```

R2 keys: `ml-weights/plate_detector/best.pt`,
`ml-weights/vehicle_detector/best.pt`, `ml-weights/vehicle_detector/last.pt`.

---

## Installation for training

```bash
pip install ultralytics roboflow
```

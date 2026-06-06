# 🚗 License Plate Detection — ML Training Guide

## Overview

This directory contains everything needed to train and run the ALPR
(Automatic License Plate Recognition) pipeline for Saint Louis College.

```
scanning/ml/
├── reader.py          # Two-stage detection + OCR pipeline
├── validator.py       # Philippine plate format validation
├── train.py           # YOLOv8 training script
├── weights/           # Trained model weights (auto-populated after training)
│   ├── best.pt        # Best checkpoint (used in production)
│   └── last.pt        # Latest checkpoint (for resuming training)
├── dataset/
│   ├── data.yaml      # Dataset config for YOLO
│   ├── images/
│   │   ├── train/     # Training images (~80%)
│   │   └── val/       # Validation images (~20%)
│   └── labels/
│       ├── train/     # Training annotations
│       └── val/       # Validation annotations
└── runs/              # Training logs & metrics (auto-generated)
```

---

## Step 1: Collect Images

Gather **200–500 photos** of vehicles with visible Philippine license plates.
More diverse data = better model. Try to include:

- ✅ Different **vehicle types**: cars, motorcycles, tricycles, trucks
- ✅ Different **angles**: front, rear, slight side angles
- ✅ Different **lighting**: daylight, overcast, dusk, night with headlights
- ✅ Different **distances**: close-up, mid-range, far
- ✅ **Dirty / worn plates** — the model should handle real conditions
- ✅ Images taken **at the actual SLC campus gates** for best accuracy

Save images as `.jpg` or `.png` files.

---

## Step 2: Label Images

Use one of these free tools to draw bounding boxes around each plate:

| Tool | Type | Link |
|------|------|------|
| **Roboflow** (recommended) | Web-based | https://roboflow.com |
| **LabelImg** | Desktop | `pip install labelImg` |
| **CVAT** | Web-based | https://cvat.ai |

### Labeling Rules
1. Draw a **tight bounding box** around the license plate only.
2. Use one class: `license_plate` (class ID `0`).
3. Export in **YOLO format** — each image gets a matching `.txt` file.

### YOLO Label Format
```
<class_id> <x_center> <y_center> <width> <height>
```
All values are **normalized (0–1)** relative to the image dimensions.

Example (`img_001.txt`):
```
0 0.52 0.78 0.18 0.06
```

---

## Step 3: Organize Dataset

Split your labeled data roughly **80% train / 20% val**:

```
dataset/images/train/   ← 160–400 images
dataset/images/val/     ← 40–100 images
dataset/labels/train/   ← matching .txt files
dataset/labels/val/     ← matching .txt files
```

> ⚠️ Each label `.txt` filename must exactly match its image filename
> (e.g., `img_001.jpg` → `img_001.txt`).

---

## Step 4: Train

From the `backend/` directory:

```bash
# Default: 100 epochs, batch 16, nano model
python -m scanning.ml.train

# Custom settings
python -m scanning.ml.train --epochs 200 --batch 8 --model-size s

# Resume interrupted training
python -m scanning.ml.train --resume
```

### Model Sizes

| Size | Flag | Speed | Accuracy | GPU VRAM |
|------|------|-------|----------|----------|
| Nano | `--model-size n` | ⚡ Fastest | Good | ~2 GB |
| Small | `--model-size s` | Fast | Better | ~4 GB |
| Medium | `--model-size m` | Moderate | Best | ~6 GB |

> 💡 **No GPU?** Training still works on CPU — it just takes longer.
> Use `--model-size n --batch 8` for CPU-friendly settings.

---

## Step 5: Verify

After training completes, `weights/best.pt` is created automatically.
The next time Django starts, `reader.py` will detect and load the model.

**No code changes needed** — the pipeline automatically switches from
the EasyOCR-only fallback to the full YOLO → crop → EasyOCR pipeline.

Check the training metrics in `runs/plate_detector/`:
- `results.png` — loss & mAP curves
- `confusion_matrix.png` — prediction accuracy breakdown
- `val_batch0_pred.jpg` — sample predictions on validation images

---

## How the Pipeline Works

```
Image Input
     │
     ▼
┌──────────────────┐
│  YOLO Detection  │  ← Finds plate bounding box(es)
│  (best.pt)       │
└────────┬─────────┘
         │  crop each detection
         ▼
┌──────────────────┐
│  Preprocessing   │  ← Grayscale → Blur → Threshold
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  EasyOCR         │  ← Reads text from the clean crop
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  PH Validator    │  ← Confirms it matches a Philippine plate format
└────────┬─────────┘
         │
         ▼
   (plate_text, bbox)
```

If no YOLO model is present, it falls back to running EasyOCR on the
full image (the original behaviour before training).

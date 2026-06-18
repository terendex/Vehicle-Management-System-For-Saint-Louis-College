"""
video_train.py — Full video training pipeline.

Pipeline:
1. Load model
2. Load video
3. Read frames
4. Detect vehicles
5. Track vehicles
6. Detect license plates
7. Assign license plates to vehicles
8. Crop license plates
9. Process via B&W filter for OCR
10. Train
11. Results
"""

from __future__ import annotations

import argparse
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
DATASET_DIR = BASE_DIR / "dataset"
WEIGHTS_DIR = BASE_DIR / "weights"
OUTPUT_DIR = BASE_DIR / "output"

VEHICLE_MODEL_PATH = WEIGHTS_DIR / "vehicle_detector.pt"
PLATE_MODEL_PATH = WEIGHTS_DIR / "best.pt"

_ocr_reader = None


def _get_ocr_reader():
    global _ocr_reader
    if _ocr_reader is None:
        try:
            import easyocr
            try:
                import torch as _torch
                _use_gpu = _torch.cuda.is_available()
            except ImportError:
                _use_gpu = False
            _ocr_reader = easyocr.Reader(["en"], gpu=_use_gpu)
        except ImportError:
            pass
    return _ocr_reader


@dataclass
class VideoResult:
    frame_idx: int
    vehicle_id: int
    plate_text: str
    confidence: float
    crop_path: Optional[str] = None


def setup_directories():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "crops").mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "results").mkdir(parents=True, exist_ok=True)


def load_models():
    try:
        from ultralytics import YOLO
        
        vehicle_model = None
        if VEHICLE_MODEL_PATH.exists():
            vehicle_model = YOLO(str(VEHICLE_MODEL_PATH))
            log.info("[MODEL] Vehicle detector loaded")
        
        plate_model = None
        if PLATE_MODEL_PATH.exists():
            plate_model = YOLO(str(PLATE_MODEL_PATH))
            log.info("[MODEL] Plate detector loaded")
        
        return vehicle_model, plate_model
    except ImportError:
        log.error("[MODEL] ultralytics not installed")
        return None, None


def preprocess_for_ocr(crop: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=4.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    _, binary = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return binary


def run_ocr(crop: np.ndarray) -> tuple[Optional[str], float]:
    try:
        ocr = _get_ocr_reader()
        if ocr is None:
            return None, 0.0
        gray = preprocess_for_ocr(crop)
        if gray.shape[1] < 400:
            scale = 400 / max(gray.shape[1], 1)
            gray = cv2.resize(gray, (400, int(gray.shape[0] * scale)), interpolation=cv2.INTER_CUBIC)
        results = ocr.readtext(gray, text_threshold=0.3)
        if not results:
            return None, 0.0
        best_text, best_conf = None, 0.0
        for (_, text, conf) in results:
            if conf > best_conf:
                best_text, best_conf = text, conf
        return best_text, best_conf
    except Exception as e:
        log.error("[OCR] Error: %s", e)
        return None, 0.0


def compute_iou(boxA, boxB) -> float:
    xA = max(boxA[0], boxB[0])
    yA = max(boxA[1], boxB[1])
    xB = min(boxA[0] + boxA[2], boxB[0] + boxB[2])
    yB = min(boxA[1] + boxA[3], boxB[1] + boxB[3])
    interArea = max(0, xB - xA) * max(0, yB - yA)
    boxAArea = boxA[2] * boxA[3]
    boxBArea = boxB[2] * boxB[3]
    if boxAArea == 0 or boxBArea == 0:
        return 0.0
    return interArea / float(boxAArea + boxBArea - interArea)


class SimpleTracker:
    def __init__(self):
        self._tracks = {}
        self._next_id = 1
    
    def update(self, detections):
        if not self._tracks:
            for i, (bbox, conf) in enumerate(detections):
                self._tracks[self._next_id] = {"bbox": bbox, "detections": [detections[i]]}
                self._next_id += 1
            return
        
        for bbox, conf in detections:
            matched = False
            for tid, track in self._tracks.items():
                if compute_iou(track["bbox"], bbox) > 0.3:
                    track["bbox"] = bbox
                    track["detections"].append((bbox, conf))
                    matched = True
                    break
            if not matched:
                self._tracks[self._next_id] = {"bbox": bbox, "detections": [(bbox, conf)]}
                self._next_id += 1
    
    def get_tracks(self):
        return self._tracks


def process_video(video_path: str, vehicle_model, plate_model, output_path: Optional[str] = None, process_every: int = 1):
    setup_directories()
    
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        log.error("[VIDEO] Cannot open video: %s", video_path)
        return []
    
    fps = cap.get(cv2.CAP_PROP_FPS)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    
    out = None
    if output_path:
        out = cv2.VideoWriter(output_path, cv2.VideoWriter_fourcc(*'mp4v'), fps, (width, height))
    
    tracker = SimpleTracker()
    frame_idx = 0
    results = []
    
    log.info("[VIDEO] Processing: %s (%dx%d @ %.2f fps)", video_path, width, height, fps)
    
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        
        frame_idx += 1
        if frame_idx % process_every != 0:
            continue
        
        h, w = frame.shape[:2]
        
        plate_detections = []
        if plate_model is not None:
            detections = plate_model.predict(frame, conf=0.25, verbose=False, max_det=100)
            for r in detections:
                for box in r.boxes:
                    x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                    score = float(box.conf[0])
                    x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
                    crop = frame[y1:y2, x1:x2]
                    plate_detections.append(((x1, y1, x2 - x1, y2 - y1), score, crop))
        
        detections_for_tracker = [(d[0], d[1]) for d in plate_detections]
        if not detections_for_tracker:
            detections_for_tracker = [(0, 0, w, h), 1.0]
        
        tracker.update(detections_for_tracker)
        
        for tid, track in tracker.get_tracks().items():
            x, y, ww, hh = track["bbox"]
            cv2.rectangle(frame, (x, y), (x + ww, y + hh), (0, 255, 0), 2)
            cv2.putText(frame, f"ID:{tid}", (x, y - 40), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        
        for tid, track in tracker.get_tracks().items():
            for bbox, conf, crop in plate_detections:
                if compute_iou(track["bbox"], bbox) > 0.3:
                    plate_text, ocr_conf = run_ocr(crop)
                    if plate_text:
                        result = VideoResult(
                            frame_idx=frame_idx,
                            vehicle_id=tid,
                            plate_text=plate_text,
                            confidence=ocr_conf,
                        )
                        results.append(result)
                        
                        crop_path = OUTPUT_DIR / "crops" / f"frame{frame_idx}_track{tid}.jpg"
                        cv2.imwrite(str(crop_path), crop)
                        result.crop_path = str(crop_path)
                        
                        log.info("[FRAME %d] Track %d: %s (conf=%.2f)", frame_idx, tid, plate_text, ocr_conf)
                        break
        
        if out:
            out.write(frame)
    
    cap.release()
    if out:
        out.release()
    
    log.info("[VIDEO] Processing complete")
    return results


def train_model(epochs: int = 100, batch: int = 16, imgsz: int = 640):
    try:
        from ultralytics import YOLO
        
        train_imgs = DATASET_DIR / "images" / "train"
        train_count = len(list(train_imgs.glob("*.jpg"))) if train_imgs.exists() else 0
        
        if train_count == 0:
            log.warning("[TRAIN] No training images found in %s", train_imgs)
            return None
        
        log.info("[TRAIN] Dataset: %d training images", train_count)
        
        model = YOLO("yolov8n.pt")
        results = model.train(
            data=str(DATASET_DIR / "data.yaml"),
            epochs=epochs,
            imgsz=imgsz,
            batch=batch,
            name="plate_detector",
            project=str(BASE_DIR / "runs"),
            exist_ok=True,
            patience=20,
        )
        
        best_src = Path(results.save_dir) / "weights" / "best.pt"
        if best_src.exists():
            WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(best_src, WEIGHTS_DIR / "best.pt")
            log.info("[TRAIN] Best weights saved")
        
        return results
    except ImportError:
        log.error("[TRAIN] ultralytics not installed")
        return None


def save_results(results: list[VideoResult], output_csv: str):
    with open(output_csv, "w") as f:
        f.write("frame,vehicle_id,plate_text,confidence\n")
        for r in results:
            f.write(f"{r.frame_idx},{r.vehicle_id},{r.plate_text},{r.confidence:.3f}\n")
    log.info("[RESULTS] Saved to %s", output_csv)


def main():
    parser = argparse.ArgumentParser(description="Video training pipeline for vehicle/license plate detection")
    parser.add_argument("--source", type=str, required=True, help="Video file or camera index")
    parser.add_argument("--output", type=str, default=None, help="Output video path")
    parser.add_argument("--process-every", type=int, default=1, help="Process every N frames")
    parser.add_argument("--train", action="store_true", help="Run training after processing")
    parser.add_argument("--epochs", type=int, default=100, help="Training epochs")
    parser.add_argument("--batch", type=int, default=16, help="Batch size")
    parser.add_argument("--imgsz", type=int, default=640, help="Image size")
    parser.add_argument("--results-csv", type=str, default=None, help="Save results to CSV")
    args = parser.parse_args()
    
    logging.basicConfig(level=logging.INFO)
    
    video_source = args.source
    if video_source.isdigit():
        video_source = int(video_source)
    
    vehicle_model, plate_model = load_models()
    
    results = process_video(
        video_path=video_source,
        vehicle_model=vehicle_model,
        plate_model=plate_model,
        output_path=args.output,
        process_every=args.process_every,
    )
    
    if args.train:
        train_model(epochs=args.epochs, batch=args.batch, imgsz=args.imgsz)
    
    if args.results_csv:
        save_results(results, args.results_csv)
    
    if results:
        total_plates = len(results)
        unique_vehicles = len(set(r.vehicle_id for r in results))
        print(f"\nResults:")
        print(f"  Total frames processed with results: {len(set(r.frame_idx for r in results))}")
        print(f"  Unique vehicles tracked: {unique_vehicles}")
        print(f"  Total plates recognized: {total_plates}")
        plates = [r.plate_text for r in results]
        print(f"  Plates found: {plates}")


if __name__ == "__main__":
    main()
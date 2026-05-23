"""
YOLO inference loop — detects prescription regions and stores crops in state.

Stability tracker:
  Waits for the best-confidence bounding box to remain still (IoU ≥ STABILITY_IOU_THRESHOLD)
  for STABILITY_WINDOW_S seconds before setting state.should_submit = True.
  This prevents submitting blurry mid-motion frames and avoids re-submitting the same
  prescription on consecutive cycles.

CLAHE preprocessing:
  Each crop is enhanced with preprocess.preprocess_crop() before base64-encoding.
  Improves OCR accuracy for handwritten text and variable lighting.
"""
from ultralytics import YOLO
import cv2
import base64
import state
import time
from preprocess import preprocess_crop

model = YOLO("best.onnx", task="detect")

# ---------------------------------------------------------------------------
# Stability tracker parameters
# ---------------------------------------------------------------------------
STABILITY_IOU_THRESHOLD = 0.85    # min overlap between consecutive frames
STABILITY_FPS_ESTIMATE = 10       # approx inference FPS on Pi 5
STABILITY_WINDOW_S = 3.0          # seconds to hold steady before triggering
STABILITY_FRAMES_NEEDED = int(STABILITY_FPS_ESTIMATE * STABILITY_WINDOW_S)  # 30

_stable_frames: int = 0
_prev_best_box: list | None = None   # normalized [x1, y1, x2, y2] of best box last frame


def _iou(box_a: list, box_b: list) -> float:
    """Compute IoU of two normalized [x1, y1, x2, y2] boxes."""
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b
    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter == 0.0:
        return 0.0
    area_a = (ax2 - ax1) * (ay2 - ay1)
    area_b = (bx2 - bx1) * (by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0.0 else 0.0


def yolo_loop():
    global _stable_frames, _prev_best_box

    while state.running:
        frame = state.latest_frame
        if frame is None:
            time.sleep(0.01)
            continue

        # Resize for ONNX inference
        resized = cv2.resize(frame, (640, 640))

        # Run YOLO
        results = model(resized, conf=0.4, verbose=False)

        # Draw annotated frame for live preview (written before any sleep)
        state.annotated_frame = results[0].plot()

        # ----------------------------------------------------------------
        # Extract crops from detections
        # ----------------------------------------------------------------
        crops: list[dict] = []
        best_box: list | None = None   # norm [x1, y1, x2, y2] of highest-conf box
        best_conf: float = 0.0

        boxes = results[0].boxes
        if boxes is not None and len(boxes) > 0:
            h, w = resized.shape[:2]
            for i, (box, conf) in enumerate(zip(boxes.xyxy.tolist(), boxes.conf.tolist())):
                x1, y1, x2, y2 = [int(v) for v in box]
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(w, x2), min(h, y2)
                if x2 - x1 < 20 or y2 - y1 < 20:
                    continue  # skip degenerate boxes

                # CLAHE preprocessing before encoding
                crop_bgr = preprocess_crop(resized[y1:y2, x1:x2])
                _, buf = cv2.imencode(".jpg", crop_bgr, [cv2.IMWRITE_JPEG_QUALITY, 90])
                b64 = base64.b64encode(buf).decode()

                norm_box = [
                    float(x1) / w, float(y1) / h,
                    float(x2) / w, float(y2) / h,
                ]
                crops.append({
                    "image_base64": b64,
                    "bbox": norm_box,
                    "confidence": float(conf),
                    "track_id": i,
                })
                if float(conf) > best_conf:
                    best_conf = float(conf)
                    best_box = norm_box

        # ----------------------------------------------------------------
        # Stability tracker
        # ----------------------------------------------------------------
        if best_box is None:
            # No detection this frame — reset counter
            _stable_frames = 0
            _prev_best_box = None
            state.stable_progress = 0.0
            # Do NOT clear should_submit here; let cloud_submit_loop consume it
        else:
            if _prev_best_box is not None:
                iou = _iou(_prev_best_box, best_box)
                if iou >= STABILITY_IOU_THRESHOLD:
                    _stable_frames = min(_stable_frames + 1, STABILITY_FRAMES_NEEDED)
                else:
                    # Box moved — restart countdown
                    _stable_frames = 0
            else:
                _stable_frames = 1

            _prev_best_box = best_box
            state.stable_progress = _stable_frames / STABILITY_FRAMES_NEEDED

            # Trigger once when stability threshold reached (and previous submit consumed)
            if _stable_frames >= STABILITY_FRAMES_NEEDED and not state.should_submit:
                state.detected_crops = list(crops)  # snapshot
                state.should_submit = True
                # Reset so countdown restarts after submission
                _stable_frames = 0
                _prev_best_box = None
                print(f"[yolo] ✅ Stable — {len(crops)} crop(s) queued for submission")

        # Always keep detected_crops fresh for fallback use
        if crops:
            state.detected_crops = crops

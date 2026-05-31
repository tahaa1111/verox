"""
YOLO inference loop with full segmentation pipeline.

Per-frame pipeline:
  1. Run YOLO at conf=0.25 → raw text-region boxes
  2. Filter degenerate boxes (too small / bad aspect ratio)
  3. Merge nearby boxes into text-line groups
  4. Fallback: if only 1 large box detected (prescription-detector model),
     split it into overlapping horizontal strips automatically
  5. CLAHE-preprocess each line crop and encode to base64
  6. Up to 9 crops queued for cloud submission

Stability tracker:
  Time-based (wall-clock), independent of YOLO FPS.
  Uses union of all detected boxes as the stability region.

Visualisation:
  - Raw YOLO boxes drawn in green (via results.plot())
  - Merged line boxes drawn in blue on top
"""
from ultralytics import YOLO
import cv2
import base64
import numpy as np
import state
import time
from preprocess import preprocess_crop

model = YOLO("best.onnx", task="detect")

# ---------------------------------------------------------------------------
# Stability tracker
# ---------------------------------------------------------------------------
STABILITY_IOU_THRESHOLD = 0.78
STABILITY_WINDOW_S      = 3.0

# ---------------------------------------------------------------------------
# Segmentation parameters
# ---------------------------------------------------------------------------
CONF_THRESHOLD   = 0.25   # lower than before → more text-region boxes
MIN_BOX_W        = 20
MIN_BOX_H        = 10
MAX_ASPECT       = 10.0
MIN_ASPECT       = 0.1
MERGE_X_GAP      = 60     # px gap allowed between boxes on same line
MERGE_Y_GAP      = 22     # px vertical gap allowed between boxes on same line

# Fallback strip split: if model returns 1 large box, split into this many strips
STRIP_COUNT      = 3
STRIP_OVERLAP    = 0.12   # 12% overlap so no text line is cut at border

_stable_start_time: float | None = None
_prev_best_box:     list  | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iou(a: list, b: list) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter == 0.0:
        return 0.0
    union = (ax2-ax1)*(ay2-ay1) + (bx2-bx1)*(by2-by1) - inter
    return inter / union if union > 0 else 0.0


def _filter(raw: list) -> list:
    out = []
    for b in raw:
        x1, y1, x2, y2 = b["bbox"]
        w, h = x2 - x1, y2 - y1
        if w < MIN_BOX_W or h < MIN_BOX_H:
            continue
        ar = w / h
        if ar > MAX_ASPECT or ar < MIN_ASPECT:
            continue
        out.append(b)
    return out


def _same_line(b1: dict, b2: dict) -> bool:
    x1, y1, x2, _ = b1["bbox"]
    x3, y3, x4, _ = b2["bbox"]
    return abs(y1 - y3) <= MERGE_Y_GAP and (x3 - x2) <= MERGE_X_GAP


def _merge_line(line: list) -> dict:
    min_x = min(b["bbox"][0] for b in line)
    min_y = min(b["bbox"][1] for b in line)
    max_x = max(b["bbox"][2] for b in line)
    max_y = max(b["bbox"][3] for b in line)
    conf  = sum(b["confidence"] for b in line) / len(line)
    return {"bbox": [min_x, min_y, max_x, max_y], "confidence": conf}


def _merge_boxes(filtered: list) -> list:
    if not filtered:
        return []
    sorted_b = sorted(filtered, key=lambda b: (b["bbox"][1], b["bbox"][0]))
    lines, cur = [], [sorted_b[0]]
    for b in sorted_b[1:]:
        if _same_line(cur[-1], b):
            cur.append(b)
        else:
            lines.append(_merge_line(cur))
            cur = [b]
    lines.append(_merge_line(cur))
    return lines


def _split_strips(box: dict, frame_h: int, frame_w: int) -> list:
    """Split one large bounding box into STRIP_COUNT overlapping horizontal strips."""
    x1, y1, x2, y2 = box["bbox"]
    bh = y2 - y1
    strip_h = bh / STRIP_COUNT
    strips = []
    for i in range(STRIP_COUNT):
        sy1 = max(0,        int(y1 + i * strip_h - STRIP_OVERLAP * strip_h))
        sy2 = min(frame_h,  int(y1 + (i + 1) * strip_h + STRIP_OVERLAP * strip_h))
        if sy2 - sy1 < MIN_BOX_H:
            continue
        strips.append({
            "bbox": [x1, sy1, x2, sy2],
            "confidence": box["confidence"],
        })
    return strips


def _union_box(boxes: list) -> list:
    return [
        min(b["bbox"][0] for b in boxes),
        min(b["bbox"][1] for b in boxes),
        max(b["bbox"][2] for b in boxes),
        max(b["bbox"][3] for b in boxes),
    ]


def _encode_crop(frame: np.ndarray, box: dict) -> str:
    x1, y1, x2, y2 = box["bbox"]
    crop = preprocess_crop(frame[y1:y2, x1:x2])
    _, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return base64.b64encode(buf).decode()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def yolo_loop():
    global _stable_start_time, _prev_best_box

    while True:
        if not state.running:
            time.sleep(0.1)
            continue

        frame = state.latest_frame
        if frame is None:
            time.sleep(0.01)
            continue

        resized = cv2.resize(frame, (640, 640))
        h, w   = resized.shape[:2]

        results = model(resized, conf=CONF_THRESHOLD, verbose=False)

        # Base annotated frame — green raw YOLO boxes drawn by ultralytics
        annotated = results[0].plot()

        # ----------------------------------------------------------------
        # 1. Collect raw boxes
        # ----------------------------------------------------------------
        raw_boxes = []
        yolo_boxes = results[0].boxes
        if yolo_boxes is not None and len(yolo_boxes) > 0:
            for box, conf in zip(yolo_boxes.xyxy.tolist(), yolo_boxes.conf.tolist()):
                x1, y1, x2, y2 = [int(v) for v in box]
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(w, x2), min(h, y2)
                raw_boxes.append({"bbox": [x1, y1, x2, y2], "confidence": float(conf)})

        # ----------------------------------------------------------------
        # 2. Filter + merge into text lines
        # ----------------------------------------------------------------
        filtered     = _filter(raw_boxes)
        merged_lines = _merge_boxes(filtered)

        # ----------------------------------------------------------------
        # 3. Fallback: if only 1 large box → split into horizontal strips
        #    (handles prescription-detection model that returns one bbox)
        # ----------------------------------------------------------------
        if len(merged_lines) == 1:
            bx1, by1, bx2, by2 = merged_lines[0]["bbox"]
            if (by2 - by1) > 120:          # tall enough to be worth splitting
                merged_lines = _split_strips(merged_lines[0], h, w)

        # ----------------------------------------------------------------
        # 4. Draw merged line boxes in BLUE on the annotated frame
        # ----------------------------------------------------------------
        for i, line in enumerate(merged_lines):
            lx1, ly1, lx2, ly2 = line["bbox"]
            cv2.rectangle(annotated, (lx1, ly1), (lx2, ly2), (255, 80, 0), 2)
            label = f"#{i+1}  {line['confidence']:.2f}"
            cv2.putText(annotated, label, (lx1 + 3, max(12, ly1 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 80, 0), 1)

        state.annotated_frame = annotated

        # ----------------------------------------------------------------
        # 5. Build crops list (one per merged line, max 9)
        # ----------------------------------------------------------------
        crops: list[dict] = []
        for i, line in enumerate(merged_lines[:9]):
            b64      = _encode_crop(resized, line)
            x1, y1, x2, y2 = line["bbox"]
            norm_box = [x1/w, y1/h, x2/w, y2/h]
            crops.append({
                "image_base64": b64,
                "bbox":         norm_box,
                "confidence":   line["confidence"],
                "track_id":     i,
            })

        # ----------------------------------------------------------------
        # 6. Stability tracker — time-based, uses union of all boxes
        # ----------------------------------------------------------------
        best_box = _union_box(merged_lines) if merged_lines else None

        if best_box is None:
            _stable_start_time = None
            _prev_best_box     = None
            state.stable_progress = 0.0
        else:
            iou = _iou(_prev_best_box, best_box) if _prev_best_box is not None else 0.0

            if _prev_best_box is None or iou >= STABILITY_IOU_THRESHOLD:
                if _stable_start_time is None:
                    _stable_start_time = time.time()
                elapsed = time.time() - _stable_start_time
                state.stable_progress = min(elapsed / STABILITY_WINDOW_S, 1.0)
            else:
                _stable_start_time    = time.time()
                state.stable_progress = 0.0

            _prev_best_box = best_box
            elapsed = (time.time() - _stable_start_time) if _stable_start_time else 0.0

            if elapsed >= STABILITY_WINDOW_S and not state.should_submit:
                state.detected_crops  = list(crops)
                state.should_submit   = True
                _stable_start_time    = None
                _prev_best_box        = None
                print(f"[yolo] ✅ Stable {elapsed:.1f}s — {len(crops)} crop(s) → cloud")

        if crops:
            state.detected_crops = crops

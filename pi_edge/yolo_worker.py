"""
YOLO inference loop — detects prescription regions and stores crops in state.
Crops are picked up by the cloud_submit_loop in app.py for automatic OCR submission.
"""
from ultralytics import YOLO
import cv2
import base64
import state
import time

model = YOLO("best.onnx", task="detect")


def yolo_loop():
    while state.running:
        frame = state.latest_frame
        if frame is None:
            time.sleep(0.01)
            continue

        # Resize for ONNX inference
        resized = cv2.resize(frame, (640, 640))

        # Run YOLO
        results = model(resized, conf=0.4, verbose=False)

        # Draw annotated frame for live preview
        state.annotated_frame = results[0].plot()

        # Extract crops from detections
        crops = []
        boxes = results[0].boxes
        if boxes is not None and len(boxes) > 0:
            h, w = resized.shape[:2]
            for i, (box, conf) in enumerate(zip(boxes.xyxy.tolist(), boxes.conf.tolist())):
                x1, y1, x2, y2 = [int(v) for v in box]
                # Clamp to frame bounds
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(w, x2), min(h, y2)
                if x2 - x1 < 20 or y2 - y1 < 20:
                    continue  # skip tiny boxes
                crop = resized[y1:y2, x1:x2]
                _, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
                b64 = base64.b64encode(buf).decode()
                crops.append({
                    "image_base64": b64,
                    "bbox": [float(x1) / w, float(y1) / h,
                             float(x2) / w, float(y2) / h],
                    "confidence": float(conf),
                    "track_id": i,
                })

        # Store detected crops for cloud_submit_loop to pick up
        if crops:
            state.detected_crops = crops

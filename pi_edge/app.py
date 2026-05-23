"""
Medibox Pi Camera App — YOLO edge detection + automatic cloud OCR submission.

Pipeline (fully automatic after pressing "Start Camera" in the frontend):
  1. Camera captures frames (camera_loop)
  2. YOLO detects prescription regions (yolo_loop)
  3. Detected crops are submitted to Cloud API /v1/submit (cloud_submit_loop)
  4. Raw frames pushed to /v1/camera/push for live preview in frontend (cloud_push_loop)
  5. Frontend polls /camera/command every 2 s for start/stop signals (cloud_command_loop)
"""

from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
import uvicorn
import threading
import base64
import uuid
import cv2
import asyncio
import httpx
import time
import sys
from pathlib import Path
from datetime import datetime, timezone

import state
from camera import camera_loop
from yolo_worker import yolo_loop

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CLOUD_API = "https://medibox-api-5w7o5tyr2q-uc.a.run.app"
CAMERA_SECRET = "medibox-camera-prod-2026"
DEVICE_ID = "pi-0001"
EDGE_CONFIG = "/etc/medibox/edge.toml"

# Minimum seconds between submissions (avoid re-submitting the same prescription)
SUBMIT_COOLDOWN_S = 10.0

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
_camera_threads_started = False
_last_submit_time = 0.0
_firebase_token: str | None = None
_firebase_token_expiry: float = 0.0

app = FastAPI()


# ---------------------------------------------------------------------------
# Firebase auth helpers
# ---------------------------------------------------------------------------

def _get_firebase_token() -> str | None:
    """Get a cached Firebase ID token, refreshing if expired."""
    global _firebase_token, _firebase_token_expiry
    now = time.time()
    if _firebase_token and now < _firebase_token_expiry - 60:
        return _firebase_token
    try:
        sys.path.insert(0, "/opt/medibox-edge/src")
        from medibox_edge.config import EdgeConfig
        from medibox_edge.auth import FirebaseAuthManager
        cfg = EdgeConfig.load(EDGE_CONFIG)
        auth = FirebaseAuthManager(
            sa_key_path=cfg.auth.sa_key_path,
            device_id=cfg.device.device_id,
            firebase_api_key=cfg.cloud.firebase_api_key,
        )
        import asyncio as _aio
        loop = _aio.new_event_loop()
        token = loop.run_until_complete(auth.get_id_token())
        loop.close()
        auth.shutdown()
        _firebase_token = token
        _firebase_token_expiry = time.time() + 3500  # tokens expire in ~1h
        print(f"[auth] Firebase token refreshed ({token[:8]}...)")
        return token
    except Exception as e:
        print(f"[auth] ERROR getting token: {e}")
        return None


# ---------------------------------------------------------------------------
# Threads
# ---------------------------------------------------------------------------

def _do_start():
    """Start camera + YOLO threads if not already running."""
    global _camera_threads_started
    if not state.running:
        state.running = True
        if not _camera_threads_started:
            threading.Thread(target=camera_loop, daemon=True).start()
            threading.Thread(target=yolo_loop, daemon=True).start()
            _camera_threads_started = True


def cloud_push_loop():
    """Push raw frames to Cloud API for live preview at ~10 fps.
    Includes stable_progress so the frontend can display the countdown."""
    client = httpx.Client(timeout=2.0)
    while True:
        if state.running and state.latest_frame is not None:
            frame = state.latest_frame.copy()
            _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
            b64 = base64.b64encode(buf).decode()
            try:
                client.post(
                    f"{CLOUD_API}/v1/camera/push",
                    json={
                        "frame": b64,
                        "device_id": DEVICE_ID,
                        "stable_progress": float(state.stable_progress),
                    },
                    headers={"X-Camera-Secret": CAMERA_SECRET},
                )
            except Exception:
                pass
        time.sleep(0.1)


def cloud_submit_loop():
    """
    Stability-triggered auto-submit: dispatched when yolo_worker sets state.should_submit.

    The stability tracker (yolo_worker.py) holds the bounding box steady for 3 s at
    IoU ≥ 0.85 before setting should_submit = True.  This loop consumes the flag,
    submits the snapshot crops to /v1/submit, then pushes the resulting job_id back
    to /v1/camera/push-job so the frontend can navigate to the results page.

    Cooldown (SUBMIT_COOLDOWN_S) prevents duplicate submissions if the prescription
    lingers in the frame after the first successful submit.
    """
    global _last_submit_time
    client = httpx.Client(timeout=15.0)

    while True:
        time.sleep(0.25)  # check 4× per second

        if not state.running:
            continue

        # Wait for stability tracker signal
        if not state.should_submit:
            continue

        # Consume the flag immediately so concurrent loops don't double-fire
        state.should_submit = False

        # Cooldown guard
        if time.time() - _last_submit_time < SUBMIT_COOLDOWN_S:
            print("[submit] ⏳ Cooldown active — skipping this trigger")
            continue

        crops = list(state.detected_crops)  # snapshot
        if not crops:
            print("[submit] No crops available — skipping")
            continue

        # Authenticate
        token = _get_firebase_token()
        if not token:
            print("[submit] No auth token — skipping")
            continue

        # Build submission payload
        session_id = str(uuid.uuid4())
        payload = {
            "device_id": DEVICE_ID,
            "session_id": session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "crops": crops[:9],   # max 9 crops per grid
        }

        try:
            resp = client.post(
                f"{CLOUD_API}/v1/submit",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 202:
                job_id = resp.json().get("job_id", "?")
                print(f"[submit] ✅ Job queued: {job_id} (session={session_id[:8]})")
                _last_submit_time = time.time()
                state.detected_crops = []  # clear after successful submit

                # Push job_id to API so the frontend can auto-navigate to results
                try:
                    client.post(
                        f"{CLOUD_API}/v1/camera/push-job",
                        json={"job_id": job_id, "device_id": DEVICE_ID},
                        headers={"X-Camera-Secret": CAMERA_SECRET},
                    )
                except Exception as push_err:
                    print(f"[submit] push-job error (non-fatal): {push_err}")
            else:
                print(f"[submit] ⚠ API returned {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            print(f"[submit] ERROR: {e}")


def cloud_command_loop():
    """Poll Cloud API every 2 s for start/stop commands from the frontend."""
    client = httpx.Client(timeout=5.0)
    while True:
        try:
            resp = client.get(
                f"{CLOUD_API}/v1/camera/command",
                params={"device_id": DEVICE_ID},
                headers={"X-Camera-Secret": CAMERA_SECRET},
            )
            if resp.status_code == 200:
                cmd = resp.json().get("command", "idle")
                if cmd == "start":
                    print("[cmd] received start — starting camera + YOLO")
                    _do_start()
                elif cmd == "stop":
                    print("[cmd] received stop — stopping camera")
                    state.running = False
        except Exception:
            pass
        time.sleep(2)


# Start background threads on app startup
threading.Thread(target=cloud_push_loop, daemon=True).start()
threading.Thread(target=cloud_submit_loop, daemon=True).start()
threading.Thread(target=cloud_command_loop, daemon=True).start()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <html>
    <body style="background:black;text-align:center;">
        <h1 style="color:white;">YOLO LIVE — Auto OCR</h1>
        <img id="stream" width="900"/>
        <br><br>
        <button onclick="start()">START</button>
        <button onclick="stop()">STOP</button>
        <script>
            let ws;
            function start(){
                fetch('/start')
                ws = new WebSocket("ws://" + location.host + "/ws")
                ws.onmessage = (event) => {
                    document.getElementById('stream').src = "data:image/jpeg;base64," + event.data
                }
            }
            function stop(){
                fetch('/stop')
                if(ws){ ws.close() }
            }
        </script>
    </body>
    </html>
    """


@app.get("/start")
def start():
    _do_start()
    return {"status": "started"}


@app.get("/stop")
def stop():
    state.running = False
    return {"status": "stopped"}


@app.get("/snapshot")
def snapshot():
    """Return a single clean frame as base64 JPEG."""
    frame = state.latest_frame
    if frame is None:
        return {"error": "no frame"}, 503
    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return {"frame": base64.b64encode(buf).decode(), "device_id": DEVICE_ID}


@app.websocket("/ws")
async def websocket_stream(ws: WebSocket):
    await ws.accept()
    while True:
        frame = state.annotated_frame
        if frame is None:
            await asyncio.sleep(0.01)
            continue
        _, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        await ws.send_text(base64.b64encode(buffer).decode())
        await asyncio.sleep(0.03)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

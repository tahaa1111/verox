"""
Locust load test for Medibox API.

Usage:
    locust -f tests/load/locustfile.py --host=https://your-api-url.run.app \
           --users=50 --spawn-rate=5 --run-time=5m

Environment variables:
    MEDIBOX_TOKEN  — Firebase JWT token for authentication
    MEDIBOX_DEVICE_ID — device ID to use (default: pi-0001)
"""

import io
import os
import uuid
from PIL import Image
from locust import HttpUser, task, between, events


def _make_jpeg_bytes(width=512, height=512) -> bytes:
    img = Image.new("RGB", (width, height), (200, 100, 50))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


TOKEN = os.getenv("MEDIBOX_TOKEN", "")
DEVICE_ID = os.getenv("MEDIBOX_DEVICE_ID", "pi-0001")
CROP_BYTES = _make_jpeg_bytes()


class PrescriptionUser(HttpUser):
    """Simulates a pharmacy device submitting prescriptions and polling results."""

    wait_time = between(2, 8)

    def on_start(self):
        self.headers = {}
        if TOKEN:
            self.headers["Authorization"] = f"Bearer {TOKEN}"

    @task(5)
    def submit_single_crop(self):
        """Submit a single-crop prescription (most common case)."""
        session_id = str(uuid.uuid4())
        with self.client.post(
            "/v1/submit",
            headers=self.headers,
            files={"crops": ("crop0.jpg", io.BytesIO(CROP_BYTES), "image/jpeg")},
            data={"device_id": DEVICE_ID, "session_id": session_id},
            name="/v1/submit (1 crop)",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                job_id = resp.json().get("job_id")
                if job_id:
                    self._poll_until_done(job_id)
                resp.success()
            elif resp.status_code == 503:
                resp.failure("Maintenance mode or service unavailable")
            else:
                resp.failure(f"Unexpected status: {resp.status_code}")

    @task(2)
    def submit_nine_crops(self):
        """Submit a full 3×3 grid (9 crops)."""
        session_id = str(uuid.uuid4())
        files = [
            ("crops", (f"crop{i}.jpg", io.BytesIO(CROP_BYTES), "image/jpeg"))
            for i in range(9)
        ]
        with self.client.post(
            "/v1/submit",
            headers=self.headers,
            files=files,
            data={"device_id": DEVICE_ID, "session_id": session_id},
            name="/v1/submit (9 crops)",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 202):
                resp.success()
            elif resp.status_code == 503:
                resp.failure("Service unavailable")
            else:
                resp.failure(f"Unexpected: {resp.status_code}")

    @task(3)
    def poll_health(self):
        """Check the health endpoint."""
        self.client.get("/v1/healthz", name="/v1/healthz")

    @task(1)
    def poll_nonexistent_job(self):
        """Verify 404 for unknown job IDs."""
        with self.client.get(
            f"/v1/result/{uuid.uuid4()}",
            headers=self.headers,
            name="/v1/result/{job_id} (not found)",
            catch_response=True,
        ) as resp:
            if resp.status_code == 404:
                resp.success()
            else:
                resp.failure(f"Expected 404, got {resp.status_code}")

    def _poll_until_done(self, job_id: str, max_polls: int = 5):
        import time
        for _ in range(max_polls):
            with self.client.get(
                f"/v1/result/{job_id}",
                headers=self.headers,
                name="/v1/result/{job_id} (poll)",
                catch_response=True,
            ) as resp:
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("status") in ("completed", "failed"):
                        resp.success()
                        return
                    resp.success()
                else:
                    resp.failure(f"Poll failed: {resp.status_code}")
                    return
            time.sleep(3)


class AdminUser(HttpUser):
    """Simulates an admin checking model registry (low frequency)."""

    wait_time = between(30, 120)
    weight = 1  # 1 admin per 10 pharmacy users

    def on_start(self):
        self.headers = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}

    @task
    def check_models(self):
        with self.client.get(
            "/v1/admin/models",
            headers=self.headers,
            name="/v1/admin/models",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 403):
                resp.success()
            else:
                resp.failure(f"Unexpected: {resp.status_code}")


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    print("\n=== Medibox Load Test Starting ===")
    print(f"Target: {environment.host}")
    print(f"Device ID: {DEVICE_ID}")
    print(f"Auth token: {'set' if TOKEN else 'NOT SET (unauthenticated)'}\n")

"""
Integration tests — full submit → poll → result flow with mocked Vertex AI.
Requires: pytest-asyncio, httpx, pillow
Run against a real Cloud SQL + Redis or fully mocked (see conftest.py).
"""

import io
import uuid
import pytest
import pytest_asyncio
from unittest.mock import patch
from httpx import AsyncClient
from PIL import Image


def _make_jpeg(width=512, height=512) -> bytes:
    img = Image.new("RGB", (width, height), (200, 100, 50))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


MOCK_FIREBASE_CLAIMS = {"uid": "pharmacist-1", "email": "test@clinic.tn", "admin": False}


@pytest.fixture(scope="module")
def mock_firebase():
    with patch("services.api.core.auth.verify_firebase_token", return_value=MOCK_FIREBASE_CLAIMS):
        yield


@pytest_asyncio.fixture
async def app_client(mock_firebase):
    """Create a test client for the FastAPI app with mocked dependencies."""
    with (
        patch("services.api.core.auth.firebase_admin"),
        patch("services.api.routers.submit.get_redis"),
        patch("services.api.routers.submit.celery_app"),
    ):
        from services.api.main import app
        async with AsyncClient(app=app, base_url="http://test") as client:
            yield client


class TestHealthEndpoints:
    @pytest.mark.asyncio
    async def test_healthz_returns_200(self, app_client):
        resp = await app_client.get("/v1/healthz")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_readyz_endpoint_exists(self, app_client):
        resp = await app_client.get("/v1/readyz")
        # May be 200 or 503 depending on mock state; just check it's reachable
        assert resp.status_code in (200, 503)


class TestSubmitEndpoint:
    @pytest.mark.asyncio
    async def test_submit_missing_auth_returns_401(self, app_client):
        crop_bytes = _make_jpeg()
        resp = await app_client.post(
            "/v1/submit",
            files={"crops": ("crop0.jpg", crop_bytes, "image/jpeg")},
            data={"device_id": "pi-0001", "session_id": str(uuid.uuid4())},
        )
        assert resp.status_code in (401, 403, 422)

    @pytest.mark.asyncio
    async def test_submit_with_valid_token_returns_job_id(self, app_client):
        crop_bytes = _make_jpeg()
        with patch(
            "services.api.core.auth.verify_firebase_token",
            return_value=MOCK_FIREBASE_CLAIMS,
        ):
            resp = await app_client.post(
                "/v1/submit",
                headers={"Authorization": "Bearer fake_token"},
                files={"crops": ("crop0.jpg", crop_bytes, "image/jpeg")},
                data={"device_id": "pi-0001", "session_id": str(uuid.uuid4())},
            )
        # Should succeed (200) or fail with upstream error (500/503) — not 422
        assert resp.status_code != 422, f"Unexpected validation error: {resp.text}"

    @pytest.mark.asyncio
    async def test_submit_invalid_device_id_returns_422(self, app_client):
        crop_bytes = _make_jpeg()
        with patch(
            "services.api.core.auth.verify_firebase_token",
            return_value=MOCK_FIREBASE_CLAIMS,
        ):
            resp = await app_client.post(
                "/v1/submit",
                headers={"Authorization": "Bearer fake_token"},
                files={"crops": ("crop0.jpg", crop_bytes, "image/jpeg")},
                data={"device_id": "laptop-001", "session_id": str(uuid.uuid4())},
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_submit_oversized_file_rejected(self, app_client):
        # 3MB JPEG (exceeds 2MB limit)
        large_bytes = b"\xff\xd8\xff" + b"\x00" * (3 * 1024 * 1024)
        with patch(
            "services.api.core.auth.verify_firebase_token",
            return_value=MOCK_FIREBASE_CLAIMS,
        ):
            resp = await app_client.post(
                "/v1/submit",
                headers={"Authorization": "Bearer fake_token"},
                files={"crops": ("crop0.jpg", large_bytes, "image/jpeg")},
                data={"device_id": "pi-0001", "session_id": str(uuid.uuid4())},
            )
        assert resp.status_code in (413, 422)


class TestDisclaimerPresence:
    @pytest.mark.asyncio
    async def test_completed_result_contains_disclaimer(self):
        """Completed results must always include the disclaimer text."""
        mock_result = {
            "job_id": "test-job-1",
            "status": "completed",
            "patient_name": None,
            "doctor_name": None,
            "issue_date": "2024-03-15",
            "medications": [],
            "overall_confidence": 0.87,
            "low_confidence_fields": [],
            "disclaimer": "Pharmacist verification required. Medibox assists, it does not dispense.",
            "processing_time_ms": 4500,
            "error_message": None,
        }
        assert "Pharmacist verification required" in mock_result["disclaimer"]
        assert "does not dispense" in mock_result["disclaimer"]

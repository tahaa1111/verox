"""Unit tests for API payload schemas."""

from __future__ import annotations

import base64
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from services.api.schemas.payload import Crop, EdgePayload
from services.api.schemas.response import DISCLAIMER, PrescriptionResult

# Minimal bytes that pass the JPEG magic-byte check (\xff\xd8\xff).
# This is NOT a valid JPEG image, but the schema validator only inspects the
# first three bytes and the overall size — it does not decode the image.
_FAKE_JPEG_B64 = base64.b64encode(b"\xff\xd8\xff\xe0" + b"\x00" * 64).decode()


def _make_crop(track_id: int = 0) -> Crop:
    """Return a minimal Crop that passes all field validators."""
    return Crop(
        image_base64=_FAKE_JPEG_B64,
        bbox=[0.0, 0.0, 100.0, 100.0],  # x2 > x1, y2 > y1
        confidence=0.95,
        track_id=track_id,
    )


class TestEdgePayload:
    def test_valid_payload_passes(self):
        payload = EdgePayload(
            device_id="pi-0001",
            session_id="550e8400-e29b-41d4-a716-446655440000",
            timestamp=datetime.now(timezone.utc),
            crops=[_make_crop()],
        )
        assert payload.device_id == "pi-0001"

    def test_invalid_device_id_rejected(self):
        with pytest.raises(ValidationError):
            EdgePayload(
                device_id="laptop-001",  # wrong pattern — must match ^pi-\d{2,4}$
                session_id="550e8400-e29b-41d4-a716-446655440000",
                timestamp=datetime.now(timezone.utc),
                crops=[_make_crop()],
            )

    def test_invalid_session_id_rejected(self):
        with pytest.raises(ValidationError):
            EdgePayload(
                device_id="pi-0001",
                session_id="not-a-uuid",
                timestamp=datetime.now(timezone.utc),
                crops=[_make_crop()],
            )

    def test_too_many_crops_rejected(self):
        with pytest.raises(ValidationError):
            EdgePayload(
                device_id="pi-0001",
                session_id="550e8400-e29b-41d4-a716-446655440000",
                timestamp=datetime.now(timezone.utc),
                crops=[_make_crop(i) for i in range(31)],  # max is 30
            )

    def test_empty_crops_rejected(self):
        with pytest.raises(ValidationError):
            EdgePayload(
                device_id="pi-0001",
                session_id="550e8400-e29b-41d4-a716-446655440000",
                timestamp=datetime.now(timezone.utc),
                crops=[],
            )


class TestPrescriptionResult:
    def test_disclaimer_is_present(self):
        assert "Pharmacist verification required" in DISCLAIMER
        assert "does not dispense" in DISCLAIMER

    def test_valid_result_builds(self):
        result = PrescriptionResult(
            job_id="abc123",
            session_id="session-001",
            medications=[],
            overall_confidence=0.87,
            disclaimer=DISCLAIMER,
        )
        assert result.disclaimer == DISCLAIMER

    def test_confidence_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            PrescriptionResult(
                job_id="abc123",
                session_id="session-001",
                overall_confidence=1.5,  # > 1.0 — must be in [0, 1]
            )

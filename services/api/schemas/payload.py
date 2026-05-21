"""
Inbound payload schema — validated on every POST /v1/submit.
Pydantic v2. Mirrors the §3.1 API contract exactly.
"""

from __future__ import annotations

import base64
import hashlib
import re
from datetime import datetime, timezone, timedelta
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Crop
# ---------------------------------------------------------------------------
_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG"
_MAX_CROP_BYTES = 2 * 1024 * 1024          # 2 MB decoded
_MAX_TOTAL_BYTES = 20 * 1024 * 1024        # 20 MB combined


class Crop(BaseModel):
    image_base64: str = Field(..., description="Base64-encoded JPEG or PNG, max 2 MB")
    bbox: list[float] = Field(..., min_length=4, max_length=4)
    confidence: float = Field(..., ge=0.0, le=1.0)
    track_id: int = Field(..., ge=0)

    @field_validator("image_base64")
    @classmethod
    def validate_image(cls, v: str) -> str:
        try:
            raw = base64.b64decode(v, validate=True)
        except Exception:
            raise ValueError("image_base64 is not valid base64")
        if len(raw) > _MAX_CROP_BYTES:
            raise ValueError(f"Decoded image exceeds 2 MB ({len(raw)} bytes)")
        if not (raw.startswith(_JPEG_MAGIC) or raw.startswith(_PNG_MAGIC)):
            raise ValueError("Image must be JPEG or PNG")
        return v

    @field_validator("bbox")
    @classmethod
    def validate_bbox(cls, v: list[float]) -> list[float]:
        if v[2] <= v[0] or v[3] <= v[1]:
            raise ValueError("bbox must satisfy x2 > x1 and y2 > y1")
        return v


# ---------------------------------------------------------------------------
# EdgePayload
# ---------------------------------------------------------------------------
_DEVICE_ID_RE = re.compile(r"^pi-\d{2,4}$")
_CLOCK_SKEW = timedelta(minutes=5)


class EdgePayload(BaseModel):
    device_id: Annotated[str, Field(pattern=r"^pi-\d{2,4}$")]
    session_id: UUID
    timestamp: datetime
    crops: list[Crop] = Field(..., min_length=1, max_length=30)

    @field_validator("session_id", mode="before")
    @classmethod
    def validate_uuid_v4(cls, v: object) -> object:
        uid = UUID(str(v)) if not isinstance(v, UUID) else v
        if uid.version != 4:
            raise ValueError("session_id must be a UUID v4")
        return uid

    @field_validator("timestamp")
    @classmethod
    def validate_clock_skew(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if abs(now - v) > _CLOCK_SKEW:
            raise ValueError("timestamp is outside ±5 min clock-skew window")
        return v

    @model_validator(mode="after")
    def validate_total_size(self) -> "EdgePayload":
        total = sum(
            len(base64.b64decode(c.image_base64, validate=True))
            for c in self.crops
        )
        if total > _MAX_TOTAL_BYTES:
            raise ValueError(f"Combined payload exceeds 20 MB ({total} bytes)")
        return self

    def payload_hash(self) -> str:
        """SHA-256 fingerprint for audit tamper-evidence."""
        parts = (
            str(self.device_id)
            + str(self.session_id)
            + self.timestamp.isoformat()
            + "".join(str(c.track_id) for c in self.crops)
        )
        return hashlib.sha256(parts.encode()).hexdigest()

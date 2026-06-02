"""
Outbound response schemas — §6 output contract + WebSocket events.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

DISCLAIMER = "Pharmacist verification required. Medibox assists, it does not dispense."


# ---------------------------------------------------------------------------
# §6 Output schema
# ---------------------------------------------------------------------------
class DosageNormalized(BaseModel):
    value: float | None = None
    unit: str | None = None  # mg | g | ml | UI | cp | None


class FieldConfidences(BaseModel):
    drug_name: float = 0.0
    dosage: float = 0.0
    frequency: float = 0.0


class MedicationItem(BaseModel):
    drug_name: str
    drug_name_normalized: str | None = None
    formulary_match_score: float = 0.0
    dosage: str | None = None
    dosage_normalized: DosageNormalized = Field(default_factory=DosageNormalized)
    frequency: str | None = None
    duration: str | None = None
    instructions: str | None = None
    warnings: str | None = None
    track_id: int = 0
    field_confidences: FieldConfidences = Field(default_factory=FieldConfidences)
    requires_review: bool = False


class TimingsMs(BaseModel):
    queue_wait: int = 0
    preprocessing: int = 0
    inference: int = 0
    postprocessing: int = 0
    total: int = 0


class PrescriptionResult(BaseModel):
    model_config = {"protected_namespaces": ()}

    job_id: str
    session_id: str
    prescription_id: str | None = None
    patient_name: str | None = None
    doctor_name: str | None = None
    issue_date: str | None = None
    medications: list[MedicationItem] = Field(default_factory=list)
    additional_notes: str | None = None
    extracted_raw_text: str = ""
    overall_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    requires_human_review: bool = False
    review_reasons: list[str] = Field(default_factory=list)
    timings_ms: TimingsMs = Field(default_factory=TimingsMs)
    model_version: str = ""
    disclaimer: str = DISCLAIMER


# ---------------------------------------------------------------------------
# Submit response
# ---------------------------------------------------------------------------
class SubmitResponse(BaseModel):
    job_id: str
    status: Literal["queued"] = "queued"
    created_at: datetime
    estimated_completion_seconds: int
    disclaimer: str = DISCLAIMER


# ---------------------------------------------------------------------------
# Poll response (still processing)
# ---------------------------------------------------------------------------
class JobPollResponse(BaseModel):
    job_id: str
    status: str
    progress: float = 0.0
    message: str | None = None
    disclaimer: str = DISCLAIMER


# ---------------------------------------------------------------------------
# WebSocket event schemas
# ---------------------------------------------------------------------------
class WsQueuedEvent(BaseModel):
    event: Literal["queued"] = "queued"
    job_id: str
    ts: str


class WsProgressEvent(BaseModel):
    event: Literal["inference_started", "inference_progress"] = "inference_progress"
    job_id: str
    stage: str
    progress: float = 0.0
    ts: str


class WsCompletedEvent(BaseModel):
    event: Literal["completed"] = "completed"
    job_id: str
    result: dict[str, Any]
    ts: str


class WsFailedEvent(BaseModel):
    event: Literal["failed"] = "failed"
    job_id: str
    error: str
    retry_in_seconds: int | None = None
    ts: str


class WsHeartbeatEvent(BaseModel):
    event: Literal["heartbeat"] = "heartbeat"
    ts: str


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------
class MaintenanceModeResponse(BaseModel):
    maintenance: bool
    toggled_by: str
    timestamp: datetime


class ModelRollbackResponse(BaseModel):
    previous_version: str
    rolled_back_to: str
    vertex_traffic_split: dict[str, int]
    timestamp: datetime
    disclaimer: str = DISCLAIMER

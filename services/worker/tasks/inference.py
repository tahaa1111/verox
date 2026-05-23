"""
Core inference Celery task — full pipeline orchestration for Cloud Run + Vertex AI.
run_pipeline: safety filter → GCS upload → compose grids → Vertex AI → postprocess → Postgres → WS notify
"""

from __future__ import annotations

import base64
import json
import os
import time
from datetime import datetime, timezone

import psycopg2
import redis
import structlog
from celery.exceptions import SoftTimeLimitExceeded

from services.worker.celery_app import app as celery_app
from services.worker.utils.grid_composer import compose_grids
from services.worker.utils.gcs_client import upload_crop
from services.worker.utils.safety_filter import SafetyFilterError, validate_all_crops
from services.worker.utils.vertex_client import infer_grid
from services.worker.tasks.postprocessing import run_postprocessing

logger = structlog.get_logger(__name__)

_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
_REDIS_AUTH = os.getenv("REDIS_AUTH_STRING", "")
_DB_URL = os.getenv("DATABASE_URL_SYNC", "")
_WS_PREFIX = "ws:job:"


@celery_app.task(
    bind=True,
    name="services.worker.tasks.inference.run_pipeline",
    max_retries=3,
    default_retry_delay=5,
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=300,
    time_limit=360,
)
def run_pipeline(self, job_id: str, payload: dict, gcs_prefix: str) -> dict:
    log = logger.bind(job_id=job_id, task_id=self.request.id)
    t_global = time.perf_counter()

    try:
        _update_job(job_id, "preprocessing")
        _ws_publish(job_id, {"event": "inference_started", "job_id": job_id, "ts": _now()})

        # 1. Input safety filter
        crops = payload.get("crops", [])
        try:
            validate_all_crops(crops)
        except SafetyFilterError as exc:
            _update_job(job_id, "failed", error=f"Safety filter: {exc}")
            _ws_publish(job_id, {"event": "failed", "job_id": job_id, "error": str(exc), "ts": _now()})
            return {"job_id": job_id, "status": "failed", "reason": "safety_filter"}

        t0 = time.perf_counter()

        # 2. Upload crops to GCS (best-effort — VPC network may block public egress)
        for crop in crops:
            track_id = crop.get("track_id", 0)
            raw_bytes = base64.b64decode(crop["image_base64"])
            try:
                upload_crop(job_id, track_id, raw_bytes)
            except Exception as gcs_exc:
                log.warning("gcs_upload_skipped", track_id=track_id, reason=str(gcs_exc)[:120])

        # 3. Compose grids
        grids = compose_grids(crops)
        preprocessing_ms = int((time.perf_counter() - t0) * 1000)
        log.info("preprocessing_done", crop_count=len(crops), grid_count=len(grids),
                 ms=preprocessing_ms)
        _ws_publish(job_id, {"event": "inference_progress", "job_id": job_id,
                             "stage": "ocr", "progress": 0.3, "ts": _now()})

        # 4. Vertex AI inference
        _update_job(job_id, "inferring")
        t1 = time.perf_counter()
        active_model = _get_active_model_version()
        all_raw_outputs: list[dict] = []

        for i, grid in enumerate(grids):
            result = infer_grid(
                grid_b64=grid.grid_b64,
                job_id=job_id,
                cell_count=len(grid.slots),
                model_version=active_model,
                crop_slots=grid.slots,   # pass bbox data for spatial prompt hints
            )
            all_raw_outputs.append({"result": result, "slots": grid.slots})
            _ws_publish(job_id, {
                "event": "inference_progress", "job_id": job_id, "stage": "ocr",
                "progress": 0.3 + 0.5 * (i + 1) / len(grids), "ts": _now(),
            })

        inference_ms = int((time.perf_counter() - t1) * 1000)
        log.info("inference_done", grids=len(grids), ms=inference_ms)

        # 5. Postprocessing
        _update_job(job_id, "postprocessing")
        t2 = time.perf_counter()
        merged = _merge_grid_outputs(all_raw_outputs)
        queue_wait_ms = _compute_queue_wait(job_id)

        final_result = run_postprocessing(
            raw_output=merged["raw_output"],
            logprobs=merged["logprobs"],
            job_id=job_id,
            session_id=payload.get("session_id", ""),
            model_version=active_model,
            timings_ms={},
        )

        # 6. Encrypt PII
        from services.api.core.pii import encrypt_pii
        if final_result.get("patient_name"):
            final_result["patient_name"] = encrypt_pii(final_result["patient_name"])
        if final_result.get("doctor_name"):
            final_result["doctor_name"] = encrypt_pii(final_result["doctor_name"])

        postprocessing_ms = int((time.perf_counter() - t2) * 1000)
        total_ms = int((time.perf_counter() - t_global) * 1000)
        final_result["timings_ms"] = {
            "queue_wait": queue_wait_ms,
            "preprocessing": preprocessing_ms,
            "inference": inference_ms,
            "postprocessing": postprocessing_ms,
            "total": total_ms,
        }

        # 7. Write to Postgres + stream to BigQuery
        _update_job(job_id, "completed", result=final_result,
                    timings=final_result["timings_ms"], model_version=active_model,
                    requires_review=final_result.get("requires_human_review", False),
                    review_reasons=final_result.get("review_reasons", []))
        _stream_to_bigquery(job_id, payload, final_result, active_model)

        _ws_publish(job_id, {"event": "completed", "job_id": job_id,
                             "result": final_result, "ts": _now()})
        log.info("pipeline_done", total_ms=total_ms)
        return {"job_id": job_id, "status": "completed"}

    except SoftTimeLimitExceeded:
        log.error("pipeline_soft_timeout")
        _update_job(job_id, "failed", error="Task timed out after 120s")
        raise self.retry(countdown=10, exc=SoftTimeLimitExceeded("soft time limit"))

    except Exception as exc:
        log.error("pipeline_error", exc=str(exc), exc_info=True)
        status = "retrying" if self.request.retries < self.max_retries else "failed"
        _update_job(job_id, status, error=str(exc))
        _ws_publish(job_id, {"event": "failed", "job_id": job_id, "error": str(exc),
                             "retry_in_seconds": 5 if self.request.retries < self.max_retries else None,
                             "ts": _now()})
        raise self.retry(countdown=5, exc=exc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _update_job(job_id: str, status: str, result: dict | None = None,
                timings: dict | None = None, error: str | None = None,
                model_version: str | None = None,
                requires_review: bool = False, review_reasons: list | None = None) -> None:
    if not _DB_URL:
        return
    now = datetime.now(timezone.utc)
    updates: dict = {"status": status, "updated_at": now}
    if result is not None:
        updates["result"] = json.dumps(result)
    if timings:
        updates.update({k + "_ms": v for k, v in timings.items() if k in
                        ("queue_wait", "preprocessing", "inference", "postprocessing", "total")})
    if error:
        updates["error_message"] = error
    if model_version:
        updates["model_version"] = model_version
    if status == "completed":
        updates["completed_at"] = now
    if requires_review:
        updates["requires_human_review"] = True
    if review_reasons:
        updates["review_reasons"] = review_reasons
    set_clause = ", ".join(f"{k} = %({k})s" for k in updates)
    updates["job_id"] = job_id
    try:
        conn = psycopg2.connect(_DB_URL)
        with conn.cursor() as cur:
            cur.execute(f"UPDATE jobs SET {set_clause} WHERE id = %(job_id)s::uuid", updates)
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.error("db_update_failed", job_id=job_id, exc=str(exc))


def _ws_publish(job_id: str, message: dict) -> None:
    try:
        kwargs: dict = {"decode_responses": True}
        if _REDIS_AUTH:
            kwargs["password"] = _REDIS_AUTH
        r = redis.from_url(_REDIS_URL, **kwargs)
        r.publish(f"{_WS_PREFIX}{job_id}", json.dumps(message))
        r.close()
    except Exception as exc:
        logger.error("ws_publish_failed", job_id=job_id, exc=str(exc))


def _get_active_model_version() -> str:
    try:
        conn = psycopg2.connect(_DB_URL)
        with conn.cursor() as cur:
            cur.execute("SELECT version FROM model_registry WHERE is_active = TRUE LIMIT 1")
            row = cur.fetchone()
        conn.close()
        return row[0] if row else "qwen2.5-vl-7b-awq-base-v0"
    except Exception:
        return "qwen2.5-vl-7b-awq-base-v0"


def _merge_grid_outputs(all_raw: list[dict]) -> dict:
    if len(all_raw) == 1:
        return all_raw[0]["result"]
    combined_texts = [item["result"].get("raw_output", "") for item in all_raw]
    combined_lp = []
    for item in all_raw:
        combined_lp.extend(item["result"].get("logprobs", []))
    return {"raw_output": "\n---GRID_BREAK---\n".join(combined_texts), "logprobs": combined_lp}


def _compute_queue_wait(job_id: str) -> int:
    try:
        conn = psycopg2.connect(_DB_URL)
        with conn.cursor() as cur:
            cur.execute("SELECT created_at FROM jobs WHERE id = %s::uuid", (job_id,))
            row = cur.fetchone()
        conn.close()
        if row:
            created_at = row[0]
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            return int((datetime.now(timezone.utc) - created_at).total_seconds() * 1000)
    except Exception:
        pass
    return 0


def _stream_to_bigquery(job_id: str, payload: dict, result: dict, model_version: str) -> None:
    """Stream job result to BigQuery medibox.requests table (DD-015)."""
    try:
        from google.cloud import bigquery
        project = os.getenv("GCP_PROJECT_ID", "")
        if not project:
            return
        client = bigquery.Client(project=project)
        rows = [{
            "request_id": job_id,
            "ts": datetime.now(timezone.utc).isoformat(),
            "device_id": payload.get("device_id", ""),
            "latency_ms": result.get("timings_ms", {}).get("total", 0),
            "model_version": model_version,
            "ocr_text": result.get("extracted_raw_text", "")[:10000],
            "structured_json": result,
            "confidence": result.get("overall_confidence", 0.0),
        }]
        client.insert_rows_json(f"{project}.medibox.requests", rows)
    except Exception as exc:
        logger.error("bq_stream_failed", job_id=job_id, exc=str(exc))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

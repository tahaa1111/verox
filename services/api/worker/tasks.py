"""
arq inference task — async OCR pipeline.

Replaces the Celery run_pipeline task. Runs inside the FastAPI event loop
(no separate worker process required).

Pipeline:
  safety filter → R2 crop upload → grid compose → RunPod inference →
  postprocessing → PII encrypt → Neon write → WebSocket notify

Retries: max 3 attempts with exponential backoff.
Dead-letter: exhausted jobs written to failed_jobs table.
CPU-bound steps wrapped in asyncio.to_thread() to avoid blocking the loop.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis
import structlog
from sqlalchemy import text

logger = structlog.get_logger(__name__)

_MAX_TRIES = 3
_WS_PREFIX = "ws:job:"
_CANCEL_PREFIX = "cancel:"


# ---------------------------------------------------------------------------
# arq lifecycle hooks
# ---------------------------------------------------------------------------

async def startup_ctx(ctx: dict) -> None:
    """Called once when the arq worker starts. Initialise shared resources."""
    import os
    from services.api.core.database import AsyncSessionLocal

    ctx["db_session_factory"] = AsyncSessionLocal
    ctx["redis_url"] = os.getenv("REDIS_URL", "redis://localhost:6379")
    logger.info("arq_worker_started")


async def shutdown_ctx(ctx: dict) -> None:
    logger.info("arq_worker_stopped")


# ---------------------------------------------------------------------------
# Main task
# ---------------------------------------------------------------------------

async def run_pipeline(ctx: dict, job_id: str, payload: dict, prefix: str) -> dict:
    """
    Full OCR pipeline as an arq async task.

    ctx["job_try"] is incremented by arq on each retry (1-based).
    On the final retry (job_try == MAX_TRIES) and failure, we write to
    failed_jobs for the dead-letter path instead of re-raising.
    """
    log = logger.bind(job_id=job_id, attempt=ctx.get("job_try", 1))
    t_global = time.perf_counter()
    redis_url: str = ctx["redis_url"]

    try:
        await _update_job(ctx, job_id, "preprocessing")
        await _ws_publish(redis_url, job_id, {
            "event": "inference_started", "job_id": job_id, "ts": _now(),
        })

        # 1. Safety filter (CPU-bound — thread pool)
        crops = payload.get("crops", [])
        try:
            from services.worker.utils.safety_filter import SafetyFilterError, validate_all_crops
            await asyncio.to_thread(validate_all_crops, crops)
        except Exception as exc:
            from services.worker.utils.safety_filter import SafetyFilterError
            if isinstance(exc, SafetyFilterError):
                await _update_job(ctx, job_id, "failed", error=f"Safety filter: {exc}")
                await _ws_publish(redis_url, job_id, {
                    "event": "failed", "job_id": job_id,
                    "error": str(exc), "ts": _now(),
                })
                return {"job_id": job_id, "status": "failed", "reason": "safety_filter"}
            raise

        t0 = time.perf_counter()

        # 2. Upload crops to R2 (best-effort, thread pool for boto3)
        crop_keys: list[str] = []
        for crop in crops:
            track_id = crop.get("track_id", 0)
            raw_bytes = base64.b64decode(crop["image_base64"])
            try:
                from services.worker.utils.gcs_client import upload_crop
                r2_key = await asyncio.to_thread(upload_crop, job_id, track_id, raw_bytes)
                crop_keys.append(r2_key)
            except Exception as r2_exc:
                log.warning("r2_upload_skipped", track_id=track_id,
                           reason=str(r2_exc)[:120])

        preprocessing_ms = int((time.perf_counter() - t0) * 1000)
        log.info("preprocessing_done", crop_count=len(crops), ms=preprocessing_ms)
        await _ws_publish(redis_url, job_id, {
            "event": "inference_progress", "job_id": job_id,
            "stage": "ocr", "progress": 0.3, "ts": _now(),
        })

        crop_meta = {
            crop.get("track_id", i): {
                "yolo_confidence": float(crop.get("confidence", 1.0)),
                "bbox": crop.get("bbox"),
            }
            for i, crop in enumerate(crops)
        }
        # slot_map: strip number (1-based) → original track_id
        slot_map = {i + 1: crops[i].get("track_id", i) for i in range(len(crops))}

        # 3. RunPod inference — strips sent as individual images, no grid packing
        await _update_job(ctx, job_id, "inferring")
        t1 = time.perf_counter()
        active_model = await _get_active_model_version(ctx)

        if payload.get("_warmup"):
            log.info("warmup_job_short_circuit")
            return {"job_id": job_id, "status": "warmup_complete"}

        if await _is_cancelled(redis_url, job_id):
            log.info("pipeline_cancelled_by_user")
            await _update_job(ctx, job_id, "cancelled", error="Cancelled by user.")
            await _ws_publish(redis_url, job_id, {
                "event": "cancelled", "job_id": job_id, "ts": _now(),
            })
            return {"job_id": job_id, "status": "cancelled"}

        from services.worker.utils.circuit_breaker import (
            check_circuit, record_success, record_failure, CircuitOpenError,
        )
        await check_circuit(redis_url)

        from services.worker.utils.vllm_client import infer_strips_async
        strips_b64 = [c["image_base64"] for c in crops[:9]]
        try:
            infer_result = await infer_strips_async(
                strips_b64=strips_b64,
                job_id=job_id,
                model_version=active_model,
            )
            await record_success(redis_url)
        except CircuitOpenError:
            raise
        except Exception:
            await record_failure(redis_url)
            raise

        if not isinstance(infer_result, dict) or "raw_output" not in infer_result:
            raise ValueError(
                f"RunPod returned unexpected response shape: {str(infer_result)[:200]}"
            )
        all_raw_outputs = [{"result": infer_result, "slots": []}]
        await _ws_publish(redis_url, job_id, {
            "event": "inference_progress", "job_id": job_id,
            "stage": "ocr", "progress": 0.8, "ts": _now(),
        })

        inference_ms = int((time.perf_counter() - t1) * 1000)

        # 5. Postprocessing (CPU-bound — thread pool)
        await _update_job(ctx, job_id, "postprocessing")
        t2 = time.perf_counter()
        merged = _merge_grid_outputs(all_raw_outputs)
        queue_wait_ms = await _compute_queue_wait(ctx, job_id)

        from services.worker.tasks.postprocessing import run_postprocessing
        final_result = await asyncio.to_thread(
            run_postprocessing,
            raw_output=merged["raw_output"],
            logprobs=merged["logprobs"],
            job_id=job_id,
            session_id=payload.get("session_id", ""),
            model_version=active_model,
            timings_ms={},
            crop_meta=crop_meta,
            slot_map=slot_map,
        )

        # 6. Encrypt PII (in-thread since it's CPU-bound Fernet)
        from services.api.core.pii import encrypt_pii
        final_result = await asyncio.to_thread(_encrypt_result_pii, final_result, encrypt_pii)

        # Store R2 crop keys for presigned URL generation on result fetch
        if crop_keys:
            final_result["_r2_crop_keys"] = crop_keys

        postprocessing_ms = int((time.perf_counter() - t2) * 1000)
        total_ms = int((time.perf_counter() - t_global) * 1000)
        final_result["timings_ms"] = {
            "queue_wait": queue_wait_ms,
            "preprocessing": preprocessing_ms,
            "inference": inference_ms,
            "postprocessing": postprocessing_ms,
            "total": total_ms,
        }

        # 7. Write to Postgres
        await _update_job(ctx, job_id, "completed",
                         result=final_result,
                         timings=final_result["timings_ms"],
                         model_version=active_model,
                         requires_review=final_result.get("requires_human_review", False),
                         review_reasons=final_result.get("review_reasons", []))

        await _ws_publish(redis_url, job_id, {
            "event": "completed", "job_id": job_id,
            "result": final_result, "ts": _now(),
        })
        log.info("pipeline_done", total_ms=total_ms)
        return {"job_id": job_id, "status": "completed"}

    except Exception as exc:
        job_try = ctx.get("job_try", 1)
        is_final = job_try >= _MAX_TRIES

        log.error("pipeline_error", exc=str(exc), attempt=job_try,
                 is_final=is_final, exc_info=True)

        if is_final:
            # Dead-letter: write to failed_jobs and don't re-raise
            await _write_dead_letter(ctx, job_id, payload, str(exc), job_try)
            await _update_job(ctx, job_id, "failed", error=str(exc))
            await _ws_publish(redis_url, job_id, {
                "event": "failed", "job_id": job_id,
                "error": "Pipeline failed after all retries", "ts": _now(),
            })
            return {"job_id": job_id, "status": "failed"}
        else:
            # Retry with exponential backoff
            await _update_job(ctx, job_id, "retrying", error=str(exc))
            await _ws_publish(redis_url, job_id, {
                "event": "failed", "job_id": job_id,
                "error": str(exc),
                "retry_in_seconds": 5 * (2 ** (job_try - 1)),
                "ts": _now(),
            })
            raise  # arq re-enqueues on exception


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _encrypt_result_pii(result: dict, encrypt_fn: Any) -> dict:
    """Encrypt PII fields in-place. Called in thread pool."""
    if result.get("patient_name"):
        result["patient_name"] = encrypt_fn(result["patient_name"])
    if result.get("doctor_name"):
        result["doctor_name"] = encrypt_fn(result["doctor_name"])
    if result.get("patient") and isinstance(result["patient"], dict):
        p = result["patient"]
        if p.get("name"):
            p["name"] = encrypt_fn(p["name"])
        if p.get("last_name"):
            p["last_name"] = encrypt_fn(p["last_name"])
    if result.get("doctor") and isinstance(result["doctor"], dict):
        d = result["doctor"]
        if d.get("name"):
            d["name"] = encrypt_fn(d["name"])
    return result


async def _update_job(
    ctx: dict,
    job_id: str,
    status: str,
    result: dict | None = None,
    timings: dict | None = None,
    error: str | None = None,
    model_version: str | None = None,
    requires_review: bool = False,
    review_reasons: list | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    updates: dict[str, Any] = {"status": status, "updated_at": now}
    if result is not None:
        updates["result"] = json.dumps(result)
    if timings:
        updates.update({
            k + "_ms": v for k, v in timings.items()
            if k in ("queue_wait", "preprocessing", "inference", "postprocessing", "total")
        })
    if error:
        updates["error_message"] = error[:2000]
    if model_version:
        updates["model_version"] = model_version
    if status == "completed":
        updates["completed_at"] = now
    if requires_review:
        updates["requires_human_review"] = True
    if review_reasons:
        # asyncpg needs a real list for ARRAY columns via raw SQL
        updates["review_reasons"] = list(review_reasons)

    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    # Pass job_id as uuid.UUID so asyncpg binds the type correctly without
    # needing ::uuid cast (which confuses SQLAlchemy's text() parameter parser)
    import uuid as _uuid
    updates["job_id"] = _uuid.UUID(job_id) if isinstance(job_id, str) else job_id

    try:
        session_factory = ctx["db_session_factory"]
        async with session_factory() as session:
            await session.execute(
                text(f"UPDATE jobs SET {set_clause} WHERE id = :job_id"),
                updates,
            )
            await session.commit()
    except Exception as exc:
        logger.error("db_update_failed", job_id=job_id, exc=str(exc))


async def _ws_publish(redis_url: str, job_id: str, message: dict) -> None:
    try:
        r = aioredis.from_url(
            redis_url, decode_responses=True,
            socket_timeout=5, socket_connect_timeout=5,
        )
        await r.publish(f"{_WS_PREFIX}{job_id}", json.dumps(message))
        await r.aclose()
    except Exception as exc:
        logger.error("ws_publish_failed", job_id=job_id, exc=str(exc))


async def _is_cancelled(redis_url: str, job_id: str) -> bool:
    try:
        r = aioredis.from_url(
            redis_url, decode_responses=True,
            socket_timeout=5, socket_connect_timeout=5,
        )
        val = await r.get(f"{_CANCEL_PREFIX}{job_id}")
        await r.aclose()
        return val == "1"
    except Exception:
        return False


async def _get_active_model_version(ctx: dict) -> str:
    try:
        session_factory = ctx["db_session_factory"]
        async with session_factory() as session:
            result = await session.execute(
                text("SELECT version FROM model_registry WHERE is_active = TRUE LIMIT 1")
            )
            row = result.fetchone()
            return row[0] if row else "qwen2.5-vl-7b-instruct"
    except Exception:
        return "qwen2.5-vl-7b-instruct"


async def _compute_queue_wait(ctx: dict, job_id: str) -> int:
    try:
        session_factory = ctx["db_session_factory"]
        async with session_factory() as session:
            import uuid as _uuid
            result = await session.execute(
                text("SELECT created_at FROM jobs WHERE id = :job_id"),
                {"job_id": _uuid.UUID(job_id) if isinstance(job_id, str) else job_id},
            )
            row = result.fetchone()
            if row:
                created_at = row[0]
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                return int((datetime.now(timezone.utc) - created_at).total_seconds() * 1000)
    except Exception:
        pass
    return 0


async def _write_dead_letter(
    ctx: dict,
    job_id: str,
    payload: dict,
    error: str,
    attempts: int,
) -> None:
    """Write exhausted job to failed_jobs table for manual review / re-run."""
    import uuid as _uuid
    try:
        session_factory = ctx["db_session_factory"]
        async with session_factory() as session:
            await session.execute(
                text("""
                    INSERT INTO failed_jobs
                        (job_id, payload_snapshot, last_error, attempts, failed_at)
                    VALUES
                        (:job_id, :payload, :error, :attempts, NOW())
                    ON CONFLICT (job_id) DO UPDATE
                        SET last_error = EXCLUDED.last_error,
                            attempts   = EXCLUDED.attempts,
                            failed_at  = EXCLUDED.failed_at
                """),
                {
                    "job_id": _uuid.UUID(job_id) if isinstance(job_id, str) else job_id,
                    "payload": json.dumps({
                        "device_id": payload.get("device_id"),
                        "session_id": payload.get("session_id"),
                        "crop_count": len(payload.get("crops", [])),
                    }),
                    "error": error[:2000],
                    "attempts": attempts,
                },
            )
            await session.commit()
        logger.warning("job_dead_lettered", job_id=job_id, attempts=attempts)
    except Exception as exc:
        logger.error("dead_letter_write_failed", job_id=job_id, exc=str(exc))


def _merge_grid_outputs(all_raw: list[dict]) -> dict:
    if len(all_raw) == 1:
        return all_raw[0]["result"]
    texts = [item["result"].get("raw_output", "") for item in all_raw]
    lp: list = []
    for item in all_raw:
        lp.extend(item["result"].get("logprobs", []))
    return {"raw_output": "\n---GRID_BREAK---\n".join(texts), "logprobs": lp}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

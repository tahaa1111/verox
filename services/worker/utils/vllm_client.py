"""
vLLM inference client — async OpenAI-compatible endpoint (RunPod or local vLLM).

Environment variables:
  VLLM_URL      — full base URL  (e.g. https://api.runpod.ai/v2/{id}/openai)
  VLLM_API_KEY  — bearer token  (RunPod API key, or empty for unauthenticated)
  VLLM_MODEL    — model name    (default: qwen2.5-vl-7b-instruct)

Both sync (infer_grid) and async (infer_grid_async) variants are provided.
The arq worker calls infer_grid_async; legacy Celery path called infer_grid.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

logger = structlog.get_logger(__name__)

_VLLM_URL     = os.getenv("VLLM_URL", "http://localhost:8000")
_VLLM_API_KEY = os.getenv("VLLM_API_KEY", "")
_VLLM_MODEL   = os.getenv("VLLM_MODEL", "qwen2.5-vl-7b-instruct")

# Set VLLM_GUIDED_DECODING=false to fall back to soft json_object mode
# if the RunPod vLLM version doesn't support guided decoding with AWQ multimodal.
_GUIDED_DECODING = os.getenv("VLLM_GUIDED_DECODING", "true").lower() == "true"

_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=5.0)

# JSON schema enforced at the token level via vLLM guided decoding.
# The model cannot emit keys outside this schema or wrong types.
# Kept intentionally narrow — only fields the postprocessing pipeline reads.
_PRESCRIPTION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "image_type": {
            "type": "string",
            "enum": ["prescription", "drug_box", "unknown", "blank"],
        },
        "prescription_id": {"type": ["string", "null"]},
        "patient": {
            "type": "object",
            "properties": {
                "name":       {"type": ["string", "null"]},
                "last_name":  {"type": ["string", "null"]},
                "address":    {"type": ["string", "null"]},
                "profession": {"type": ["string", "null"]},
            },
        },
        "doctor": {
            "type": "object",
            "properties": {
                "name":  {"type": ["string", "null"]},
                "stamp": {"type": ["string", "null"]},
            },
        },
        "issue_date":         {"type": ["string", "null"]},
        "additional_notes":   {"type": ["string", "null"]},
        "extracted_raw_text": {"type": "string"},
        "overall_confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "medications": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "drug_name":    {"type": ["string", "null"]},
                    "dosage":       {"type": ["string", "null"]},
                    "frequency":    {"type": ["string", "null"]},
                    "duration":     {"type": ["string", "null"]},
                    "quantity":     {"type": ["string", "null"]},
                    "instructions": {"type": ["string", "null"]},
                    "warnings":     {"type": ["string", "null"]},
                    "cnam":         {"type": "boolean"},
                    "track_id":     {"type": "integer"},
                },
                "required": ["drug_name", "cnam", "track_id"],
            },
        },
        "cell_texts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "cell":             {"type": "integer"},
                    "text":             {"type": "string"},
                    "cell_type": {
                        "type": "string",
                        "enum": ["prescription", "drug_box", "blank"],
                    },
                    "model_confidence": {"type": "number"},
                },
                "required": ["cell", "text", "cell_type", "model_confidence"],
            },
        },
    },
    "required": [
        "image_type", "medications", "extracted_raw_text",
        "cell_texts", "overall_confidence",
    ],
}


def _load_system_prompt() -> str:
    prompt_path = (
        Path(__file__).parent.parent.parent
        / "vllm" / "prompts" / "system_prompt_v1.txt"
    )
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    return "Extract prescription data from the image and return JSON."


def _build_bbox_hints(crop_slots: list | None) -> str:
    if not crop_slots:
        return ""
    lines: list[str] = []
    for slot in crop_slots:
        bbox = getattr(slot, "bbox", None)
        if not bbox or len(bbox) < 4:
            continue
        x1, y1, x2, y2 = bbox
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        h = "left" if cx < 0.4 else ("right" if cx > 0.6 else "center")
        v = "top"  if cy < 0.4 else ("bottom" if cy > 0.6 else "middle")
        lines.append(
            f"Cell {slot.cell_number}: {v}-{h} "
            f"[x:{x1:.2f}–{x2:.2f}, y:{y1:.2f}–{y2:.2f}]"
        )
    if not lines:
        return ""
    return "Spatial context (original frame positions):\n" + "\n".join(lines) + "\n\n"


def _build_payload(grid_b64: str, cell_count: int, crop_slots: list | None) -> dict:
    system_prompt = _load_system_prompt()
    bbox_hints    = _build_bbox_hints(crop_slots)
    strip_note = (
        "These cells are HORIZONTAL STRIPS of the SAME prescription, ordered top→bottom.\n"
        "Top cells = doctor header + date. Middle = patient + medications. "
        "Bottom = remaining medications + CNAM + instructions.\n"
        "Drug BOX cells (packaging) → mark cell_type=drug_box, skip medications.\n"
        "Combine ALL cells into one unified JSON result.\n"
    )
    user_text = (
        f"{bbox_hints}{strip_note}"
        f"Extract the complete prescription from all {cell_count} cell(s). Return JSON."
    )
    payload: dict = {
        "model": _VLLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{grid_b64}"}},
                {"type": "text", "text": user_text},
            ]},
        ],
        "max_tokens": 1536,
        "temperature": 0.0,
        "seed": 42,
        "logprobs": True,
        "top_logprobs": 3,
    }
    if _GUIDED_DECODING:
        # Structural schema enforcement at token level — model cannot emit
        # keys outside the schema or wrong types. Replaces soft json_object.
        # If the RunPod vLLM build doesn't support this with AWQ multimodal,
        # set VLLM_GUIDED_DECODING=false to fall back to the soft mode below.
        payload["guided_json"] = _PRESCRIPTION_SCHEMA
    else:
        payload["response_format"] = {"type": "json_object"}
    return payload


def _parse_response(data: dict, t0: float, job_id: str, model_version: str,
                    prompt_version: str, cell_count: int) -> dict:
    choice = data["choices"][0]
    logprobs = choice.get("logprobs", {})
    inference_ms = int((time.perf_counter() - t0) * 1000)
    logger.info("vllm_infer_done", job_id=job_id,
               inference_ms=inference_ms, cell_count=cell_count)
    return {
        "raw_output":     choice["message"]["content"],
        "cells":          [],
        "logprobs":       logprobs.get("content", []) if logprobs else [],
        "inference_ms":   inference_ms,
        "prompt_version": prompt_version,
        "model_version":  model_version or _VLLM_MODEL,
    }


# ---------------------------------------------------------------------------
# Async variant (used by arq worker)
# ---------------------------------------------------------------------------

async def infer_grid_async(
    grid_b64: str,
    job_id: str,
    cell_count: int,
    model_version: str = "",
    prompt_version: str = "v1",
    crop_slots: list | None = None,
    _attempt: int = 1,
) -> dict[str, Any]:
    """Async inference with manual retry (tenacity doesn't support async well here)."""
    max_attempts = 40
    wait_min, wait_max = 4, 60

    payload = _build_payload(grid_b64, cell_count, crop_slots)
    headers: dict[str, str] = {}
    if _VLLM_API_KEY:
        headers["Authorization"] = f"Bearer {_VLLM_API_KEY}"

    import asyncio
    guided_active = _GUIDED_DECODING  # local flag — can be disabled mid-job
    for attempt in range(1, max_attempts + 1):
        t0 = time.perf_counter()
        if guided_active:
            payload["guided_json"] = _PRESCRIPTION_SCHEMA
            payload.pop("response_format", None)
        else:
            payload.pop("guided_json", None)
            payload["response_format"] = {"type": "json_object"}
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    f"{_VLLM_URL.rstrip('/')}/v1/chat/completions",
                    json=payload,
                    headers=headers,
                )
                # Guided decoding unsupported → fall back automatically once
                if resp.status_code == 400 and guided_active:
                    body = resp.text
                    if "guided" in body.lower() or "grammar" in body.lower() or "xgrammar" in body.lower():
                        logger.warning("guided_decoding_unsupported_falling_back",
                                       job_id=job_id, detail=body[:200])
                        guided_active = False
                        continue
                resp.raise_for_status()
            return _parse_response(
                resp.json(), t0, job_id, model_version, prompt_version, cell_count
            )
        except Exception as exc:
            if attempt >= max_attempts:
                logger.error("vllm_all_retries_exhausted", job_id=job_id,
                            attempts=attempt, exc=str(exc))
                raise
            wait = min(wait_min * (2 ** (attempt - 1)), wait_max)
            logger.warning("vllm_retry", job_id=job_id, attempt=attempt,
                          wait=wait, exc=str(exc)[:100])
            await asyncio.sleep(wait)

    raise RuntimeError("infer_grid_async: unreachable")


# ---------------------------------------------------------------------------
# Sync variant (kept for backward compatibility)
# ---------------------------------------------------------------------------

@retry(stop=stop_after_attempt(40), wait=wait_exponential(multiplier=1, min=4, max=60))
def infer_grid(
    grid_b64: str,
    job_id: str,
    cell_count: int,
    model_version: str = "",
    prompt_version: str = "v1",
    crop_slots: list | None = None,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    payload = _build_payload(grid_b64, cell_count, crop_slots)
    headers: dict[str, str] = {}
    if _VLLM_API_KEY:
        headers["Authorization"] = f"Bearer {_VLLM_API_KEY}"

    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.post(
            f"{_VLLM_URL.rstrip('/')}/v1/chat/completions",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
    return _parse_response(
        resp.json(), t0, job_id, model_version, prompt_version, cell_count
    )

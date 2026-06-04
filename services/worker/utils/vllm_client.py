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

_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=5.0)


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
    return {
        "model": _VLLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{grid_b64}"}},
                {"type": "text", "text": user_text},
            ]},
        ],
        "max_tokens": 700,    # ~2500 input tokens (image+prompt) + 700 output = ~3200, fits in 4096
        "temperature": 0.0,
        "logprobs": True,
        "top_logprobs": 3,
        "response_format": {"type": "json_object"},
    }


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
    for attempt in range(1, max_attempts + 1):
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    f"{_VLLM_URL.rstrip('/')}/v1/chat/completions",
                    json=payload,
                    headers=headers,
                )
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

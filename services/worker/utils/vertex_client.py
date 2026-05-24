"""
Vertex AI Endpoint client for prescription OCR inference.
Calls the custom-container predict endpoint (DD-011).
Falls back to local vLLM (OpenAI-compatible) when VERTEX_ENDPOINT_ID is empty.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

logger = structlog.get_logger(__name__)

_VERTEX_ENDPOINT_ID = os.getenv("VERTEX_ENDPOINT_ID", "")
_VERTEX_REGION = os.getenv("VERTEX_ENDPOINT_REGION", "us-central1")
_GCP_PROJECT = os.getenv("GCP_PROJECT_ID", "")
_LOCAL_VLLM_URL = os.getenv("LOCAL_VLLM_URL", "http://localhost:8000")
_VLLM_MODEL = "qwen2.5-vl-7b-awq"


def _load_system_prompt() -> str:
    prompt_path = Path(__file__).parent.parent.parent / "vllm" / "prompts" / "system_prompt_v1.txt"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    return "Extract prescription data from the image and return JSON."


@retry(stop=stop_after_attempt(20), wait=wait_exponential(multiplier=1, min=4, max=30))
def infer_grid(
    grid_b64: str,
    job_id: str,
    cell_count: int,
    model_version: str = "",
    prompt_version: str = "v1",
    crop_slots: list | None = None,
) -> dict[str, Any]:
    """
    Send a composed grid image to Vertex AI (or local vLLM fallback).

    Args:
        crop_slots: list of CropSlot objects carrying bbox info for spatial hints.
                    When provided, the user prompt includes qualitative position hints
                    ("Cell 1: top-left region [x:0.05–0.48, y:0.03–0.52]") to help
                    the model assign fields (patient block vs. medication list).

    Returns the raw prediction dict: {raw_output, cells, logprobs, inference_ms, ...}
    """
    t0 = time.perf_counter()
    if _VERTEX_ENDPOINT_ID:
        result = _call_vertex(grid_b64, cell_count, prompt_version, job_id, crop_slots)
    else:
        result = _call_local_vllm(grid_b64, cell_count, prompt_version, job_id, crop_slots)
    result["inference_ms"] = int((time.perf_counter() - t0) * 1000)
    result["prompt_version"] = prompt_version
    result["model_version"] = model_version or _VLLM_MODEL
    logger.info("vertex_infer_done", job_id=job_id, inference_ms=result["inference_ms"],
                cell_count=cell_count)
    return result


def _build_bbox_hints(crop_slots: list | None) -> str:
    """
    Build a natural-language spatial context string from CropSlot bbox data.

    Example output:
        Cell 1: top-left region [x:0.05–0.48, y:0.03–0.52] — likely patient block.
        Cell 2: top-right region [x:0.52–0.95, y:0.03–0.48].

    Returns an empty string when no slots are provided (graceful degradation).
    """
    if not crop_slots:
        return ""
    lines: list[str] = []
    for slot in crop_slots:
        bbox = getattr(slot, "bbox", None)
        if not bbox or len(bbox) < 4:
            continue
        x1, y1, x2, y2 = bbox
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2
        h_pos = "left" if cx < 0.4 else ("right" if cx > 0.6 else "center")
        v_pos = "top" if cy < 0.4 else ("bottom" if cy > 0.6 else "middle")
        hint = f"Cell {slot.cell_number}: {v_pos}-{h_pos} region [x:{x1:.2f}–{x2:.2f}, y:{y1:.2f}–{y2:.2f}]"
        lines.append(hint)
    if not lines:
        return ""
    return "Spatial context (original frame positions):\n" + "\n".join(lines) + "\n\n"


def _call_vertex(
    grid_b64: str, cell_count: int, prompt_version: str, job_id: str,
    crop_slots: list | None = None,
) -> dict:
    from google.cloud import aiplatform
    aiplatform.init(project=_GCP_PROJECT, location=_VERTEX_REGION)
    endpoint = aiplatform.Endpoint(endpoint_name=_VERTEX_ENDPOINT_ID)

    system_prompt = _load_system_prompt()
    bbox_hints = _build_bbox_hints(crop_slots)
    instances = [{
        "image_b64": grid_b64,
        "system_prompt": system_prompt,
        "prompt_version": prompt_version,
        "cell_count": cell_count,
        "job_id": job_id,
        "bbox_hints": bbox_hints,
    }]
    response = endpoint.predict(instances=instances)
    prediction = response.predictions[0]
    return {
        "raw_output": prediction.get("raw_output", ""),
        "cells": prediction.get("cells", []),
        "logprobs": prediction.get("logprobs", []),
    }


def _get_cloud_run_id_token(audience: str) -> str | None:
    """
    Fetch a Google-signed OIDC identity token for Cloud Run service-to-service auth.
    Returns None in local dev (no metadata server available).
    """
    try:
        import google.auth.transport.requests
        import google.oauth2.id_token
        auth_req = google.auth.transport.requests.Request()
        return google.oauth2.id_token.fetch_id_token(auth_req, audience)
    except Exception as exc:
        logger.debug("oidc_token_unavailable", reason=str(exc))
        return None


def _call_local_vllm(
    grid_b64: str, cell_count: int, prompt_version: str, job_id: str,
    crop_slots: list | None = None,
) -> dict:
    """Call Cloud Run vLLM service using OpenAI-compatible API with IAM auth."""
    import httpx
    system_prompt = _load_system_prompt()
    bbox_hints = _build_bbox_hints(crop_slots)
    user_text = (
        f"{bbox_hints}"
        f"Extract prescriptions from all {cell_count} cell(s). "
        f"Use the spatial context above to distinguish patient info (typically top) "
        f"from medication lists (typically center/bottom). Return JSON."
    )
    payload = {
        "model": _VLLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{grid_b64}"}},
                {"type": "text", "text": user_text},
            ]},
        ],
        "max_tokens": 4096,
        "temperature": 0.0,
        "logprobs": True,
        "top_logprobs": 3,
        "response_format": {"type": "json_object"},
    }
    # Cloud Run requires an OIDC identity token when --no-allow-unauthenticated is set.
    # fetch_id_token() uses the service account's metadata server on Cloud Run.
    # Falls back gracefully to no auth in local dev (metadata server not available).
    headers: dict = {}
    id_token = _get_cloud_run_id_token(_LOCAL_VLLM_URL)
    if id_token:
        headers["Authorization"] = f"Bearer {id_token}"

    with httpx.Client(timeout=60.0) as client:
        resp = client.post(
            f"{_LOCAL_VLLM_URL}/v1/chat/completions",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
    data = resp.json()
    choice = data["choices"][0]
    logprobs = choice.get("logprobs", {})
    return {
        "raw_output": choice["message"]["content"],
        "cells": [],
        "logprobs": logprobs.get("content", []) if logprobs else [],
    }

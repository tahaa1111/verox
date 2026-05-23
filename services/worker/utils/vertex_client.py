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


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def infer_grid(
    grid_b64: str,
    job_id: str,
    cell_count: int,
    model_version: str = "",
    prompt_version: str = "v1",
) -> dict[str, Any]:
    """
    Send a composed grid image to Vertex AI (or local vLLM fallback).
    Returns the raw prediction dict: {raw_output, cells, logprobs, inference_ms, ...}
    """
    t0 = time.perf_counter()
    if _VERTEX_ENDPOINT_ID:
        result = _call_vertex(grid_b64, cell_count, prompt_version, job_id)
    else:
        result = _call_local_vllm(grid_b64, cell_count, prompt_version, job_id)
    result["inference_ms"] = int((time.perf_counter() - t0) * 1000)
    result["prompt_version"] = prompt_version
    result["model_version"] = model_version or _VLLM_MODEL
    logger.info("vertex_infer_done", job_id=job_id, inference_ms=result["inference_ms"],
                cell_count=cell_count)
    return result


def _call_vertex(grid_b64: str, cell_count: int, prompt_version: str, job_id: str) -> dict:
    from google.cloud import aiplatform
    aiplatform.init(project=_GCP_PROJECT, location=_VERTEX_REGION)
    endpoint = aiplatform.Endpoint(endpoint_name=_VERTEX_ENDPOINT_ID)

    system_prompt = _load_system_prompt()
    instances = [{
        "image_b64": grid_b64,
        "system_prompt": system_prompt,
        "prompt_version": prompt_version,
        "cell_count": cell_count,
        "job_id": job_id,
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


def _call_local_vllm(grid_b64: str, cell_count: int, prompt_version: str, job_id: str) -> dict:
    """Call Cloud Run vLLM service using OpenAI-compatible API with IAM auth."""
    import httpx
    system_prompt = _load_system_prompt()
    payload = {
        "model": _VLLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{grid_b64}"}},
                {"type": "text",
                 "text": f"Extract prescriptions from all {cell_count} cells. Return JSON."},
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

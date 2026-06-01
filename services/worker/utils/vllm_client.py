"""
vLLM inference client — OpenAI-compatible endpoint (RunPod, local vLLM, or any host).

Environment variables:
  VLLM_URL      — full base URL of the vLLM server (e.g. https://api.runpod.ai/v2/{id}/openai)
  VLLM_API_KEY  — bearer token (RunPod API key, or empty for unauthenticated local server)
  VLLM_MODEL    — model name to pass in the request (default: qwen2.5-vl-7b-instruct)
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


def _load_system_prompt() -> str:
    prompt_path = Path(__file__).parent.parent.parent / "vllm" / "prompts" / "system_prompt_v1.txt"
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
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2
        h_pos = "left" if cx < 0.4 else ("right" if cx > 0.6 else "center")
        v_pos = "top"  if cy < 0.4 else ("bottom" if cy > 0.6 else "middle")
        lines.append(
            f"Cell {slot.cell_number}: {v_pos}-{h_pos} region "
            f"[x:{x1:.2f}–{x2:.2f}, y:{y1:.2f}–{y2:.2f}]"
        )
    if not lines:
        return ""
    return "Spatial context (original frame positions):\n" + "\n".join(lines) + "\n\n"


@retry(stop=stop_after_attempt(40), wait=wait_exponential(multiplier=1, min=4, max=60))
def infer_grid(
    grid_b64: str,
    job_id: str,
    cell_count: int,
    model_version: str = "",
    prompt_version: str = "v1",
    crop_slots: list | None = None,
) -> dict[str, Any]:
    """
    Send a composed grid image to the vLLM endpoint (RunPod or any OpenAI-compatible server).
    Returns: {raw_output, cells, logprobs, inference_ms, prompt_version, model_version}
    """
    t0 = time.perf_counter()

    system_prompt = _load_system_prompt()
    bbox_hints    = _build_bbox_hints(crop_slots)

    strip_note = (
        "These cells are HORIZONTAL STRIPS of the SAME prescription, ordered top→bottom.\n"
        "Top cells = doctor header + date. Middle cells = patient + medications. "
        "Bottom cells = remaining medications + CNAM + instructions.\n"
        "If a cell contains a drug BOX (packaging) instead of prescription text, "
        "mark it as cell_type=drug_box and do NOT extract medications from it.\n"
        "Combine ALL prescription cells into one unified JSON result.\n"
    )
    user_text = (
        f"{bbox_hints}"
        f"{strip_note}"
        f"Extract the complete prescription from all {cell_count} cell(s). Return JSON."
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

    headers: dict[str, str] = {}
    if _VLLM_API_KEY:
        headers["Authorization"] = f"Bearer {_VLLM_API_KEY}"

    with httpx.Client(timeout=120.0) as client:
        resp = client.post(
            f"{_VLLM_URL.rstrip('/')}/v1/chat/completions",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()

    data   = resp.json()
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

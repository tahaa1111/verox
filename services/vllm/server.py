"""
Vertex AI custom-container prediction server (DD-011).
Exposes:
  POST $AIP_PREDICT_ROUTE (/predict) — Vertex AI contract
  POST /v1/chat/completions       — OpenAI-compatible (local dev)
  GET  $AIP_HEALTH_ROUTE  (/health)
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time

import httpx
import structlog
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

logger = structlog.get_logger("medibox.vllm")

AIP_HTTP_PORT = int(os.getenv("AIP_HTTP_PORT", "8080"))
AIP_HEALTH_ROUTE = os.getenv("AIP_HEALTH_ROUTE", "/health")
AIP_PREDICT_ROUTE = os.getenv("AIP_PREDICT_ROUTE", "/predict")
AIP_STORAGE_URI = os.getenv("AIP_STORAGE_URI", "")  # GCS path to model artifacts

VLLM_PORT = 8000
VLLM_URL = f"http://localhost:{VLLM_PORT}"
MODEL_PATH = os.getenv("MODEL_PATH", "/models/qwen2.5-vl-7b-awq")
VLLM_MODEL_NAME = "qwen2.5-vl-7b-awq"

_vllm_proc: subprocess.Popen | None = None
_ready = False

app = FastAPI(title="Medibox vLLM Vertex Server", docs_url=None, redoc_url=None)


# ---------------------------------------------------------------------------
# Startup: download model from GCS, launch vLLM, warmup
# ---------------------------------------------------------------------------

async def _download_model_from_gcs(gcs_uri: str, local_path: str) -> None:
    if not gcs_uri:
        logger.info("no_gcs_uri_using_local_model")
        return
    logger.info("downloading_model", gcs_uri=gcs_uri, local_path=local_path)
    proc = await asyncio.create_subprocess_exec(
        "gsutil", "-m", "cp", "-r", gcs_uri, local_path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"gsutil failed: {stderr.decode()}")
    logger.info("model_downloaded", local_path=local_path)


def _detect_gpu_compute_capability() -> float:
    """Return the CUDA compute capability of the first visible GPU, or 0.0 if none."""
    try:
        import subprocess as sp
        out = sp.check_output(
            ["nvidia-smi", "--query-gpu=compute_cap", "--format=csv,noheader"],
            stderr=sp.DEVNULL, timeout=10,
        ).decode().strip().splitlines()[0]
        return float(out)
    except Exception:
        return 0.0


async def _launch_vllm() -> None:
    global _vllm_proc
    cc = _detect_gpu_compute_capability()
    logger.info("gpu_compute_capability", cc=cc)

    # AWQ CUDA kernels in vLLM require Ampere (sm80+).
    # P100=sm60, T4=sm75 do NOT support vLLM's AWQ kernels.
    # On those GPUs skip --quantization awq and use fp16 dequantised weights.
    ampere_plus = cc >= 8.0

    if ampere_plus:
        cmd = [
            sys.executable, "-m", "vllm.entrypoints.openai.api_server",
            "--model", MODEL_PATH,
            "--quantization", "awq",
            "--dtype", "float16",
            "--gpu-memory-utilization", "0.90",
            "--max-model-len", "8192",
            "--max-num-seqs", "16",
            "--limit-mm-per-prompt", "image=1",
            "--disable-log-requests",
            "--served-model-name", VLLM_MODEL_NAME,
            "--host", "0.0.0.0",
            "--port", str(VLLM_PORT),
        ]
    else:
        # Older GPU (P100/T4): no AWQ kernels, enforce-eager, smaller context window
        cmd = [
            sys.executable, "-m", "vllm.entrypoints.openai.api_server",
            "--model", MODEL_PATH,
            "--dtype", "float16",
            "--gpu-memory-utilization", "0.90",
            "--max-model-len", "4096",
            "--max-num-seqs", "4",
            "--limit-mm-per-prompt", "image=1",
            "--enforce-eager",
            "--disable-log-requests",
            "--served-model-name", VLLM_MODEL_NAME,
            "--host", "0.0.0.0",
            "--port", str(VLLM_PORT),
        ]

    lora_path = os.getenv("LORA_ADAPTER_PATH", "")
    if lora_path:
        cmd += ["--enable-lora", "--lora-modules", f"medibox-adapter={lora_path}"]
    logger.info("launching_vllm", cmd=" ".join(cmd))
    _vllm_proc = subprocess.Popen(cmd)


async def _wait_for_vllm(timeout: int = 300) -> None:
    deadline = time.time() + timeout
    async with httpx.AsyncClient() as client:
        while time.time() < deadline:
            # Abort early if vLLM process has already crashed
            if _vllm_proc and _vllm_proc.poll() is not None:
                raise RuntimeError(
                    f"vLLM subprocess exited with code {_vllm_proc.returncode} "
                    "before becoming healthy"
                )
            try:
                r = await client.get(f"{VLLM_URL}/health", timeout=5.0)
                if r.status_code == 200:
                    logger.info("vllm_ready")
                    return
            except Exception:
                pass
            await asyncio.sleep(5)
    raise TimeoutError("vLLM did not become ready within timeout")


async def _run_warmup() -> None:
    from services.vllm.warmup import warmup
    try:
        await warmup(vllm_url=VLLM_URL, model=VLLM_MODEL_NAME)
    except Exception as exc:
        logger.warning("warmup_failed_non_fatal", exc=str(exc))


@app.on_event("startup")
async def startup():
    global _ready
    if AIP_STORAGE_URI:
        await _download_model_from_gcs(AIP_STORAGE_URI, MODEL_PATH)
    await _launch_vllm()
    await _wait_for_vllm(timeout=600)  # 10 min: model download + GPU load
    await _run_warmup()
    _ready = True
    logger.info("server_ready", port=AIP_HTTP_PORT)


@app.on_event("shutdown")
async def shutdown():
    if _vllm_proc:
        _vllm_proc.terminate()


# ---------------------------------------------------------------------------
# Health route
# ---------------------------------------------------------------------------

@app.get(AIP_HEALTH_ROUTE)
async def health():
    # Always return 200 so Vertex AI startup probe does not kill the container
    # while the model is loading.  Include state in the body for observability.
    return {"status": "ok" if _ready else "initializing"}


# ---------------------------------------------------------------------------
# Vertex AI predict route
# ---------------------------------------------------------------------------

@app.post(AIP_PREDICT_ROUTE)
async def predict(request: Request):
    if not _ready:
        raise HTTPException(status_code=503, detail="Model not ready")

    body = await request.json()
    instances = body.get("instances", [])
    if not instances:
        raise HTTPException(status_code=400, detail="No instances provided")

    predictions = []
    for instance in instances:
        result = await _run_inference(instance)
        predictions.append(result)

    return {"predictions": predictions}


async def _run_inference(instance: dict) -> dict:
    image_b64: str = instance["image_b64"]
    system_prompt: str = instance.get("system_prompt", "Extract prescription JSON.")
    cell_count: int = instance.get("cell_count", 9)
    prompt_version: str = instance.get("prompt_version", "v1")

    payload = {
        "model": VLLM_MODEL_NAME,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                {"type": "text",
                 "text": f"Extract all {cell_count} prescriptions from the grid. Return JSON."},
            ]},
        ],
        "max_tokens": 4096,
        "temperature": 0.0,
        "logprobs": True,
        "top_logprobs": 3,
        "response_format": {"type": "json_object"},
    }

    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(f"{VLLM_URL}/v1/chat/completions", json=payload)
        resp.raise_for_status()
    inference_ms = int((time.perf_counter() - t0) * 1000)

    data = resp.json()
    choice = data["choices"][0]
    raw_output = choice["message"]["content"]
    logprobs_raw = choice.get("logprobs", {}) or {}

    return {
        "raw_output": raw_output,
        "cells": [],          # postprocessing maps cells from raw_output
        "logprobs": logprobs_raw.get("content", []),
        "model_version": VLLM_MODEL_NAME,
        "inference_ms": inference_ms,
        "prompt_version": prompt_version,
        "prompt_tokens": data.get("usage", {}).get("prompt_tokens", 0),
        "completion_tokens": data.get("usage", {}).get("completion_tokens", 0),
    }


# ---------------------------------------------------------------------------
# OpenAI-compatible route (local dev / direct testing)
# ---------------------------------------------------------------------------

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """Proxy to local vLLM OpenAI API (for direct testing without Vertex wrapper)."""
    if not _ready:
        raise HTTPException(status_code=503, detail="Model not ready")
    body = await request.json()
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(f"{VLLM_URL}/v1/chat/completions", json=body)
    return JSONResponse(status_code=resp.status_code, content=resp.json())


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=AIP_HTTP_PORT, log_level="warning")

"""
Vertex AI custom-container prediction server (DD-011).
Exposes:
  POST $AIP_PREDICT_ROUTE (/predict) — Vertex AI contract
  POST /v1/chat/completions       — OpenAI-compatible (local dev)
  GET  $AIP_HEALTH_ROUTE  (/health)

Startup strategy (critical for Vertex AI):
  Model loading runs in a background asyncio.Task so the HTTP server
  is alive and returning 200 on /health from the very first request.
  This prevents Vertex AI's startup health probe from timing out and
  killing the container before the GPU model is loaded.
  /predict returns 503 until _ready=True.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from contextlib import asynccontextmanager

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
# MODEL_PATH can be:
#   - A local filesystem path: /models/qwen2.5-vl-7b-awq
#   - A HuggingFace Hub repo ID: Qwen/Qwen2.5-VL-7B-Instruct-AWQ
#     (vLLM auto-downloads from HF Hub if the path doesn't exist locally)
MODEL_PATH = os.getenv("MODEL_PATH", "Qwen/Qwen2.5-VL-7B-Instruct-AWQ")
VLLM_MODEL_NAME = "qwen2.5-vl-7b-awq"

_vllm_proc: subprocess.Popen | None = None
_ready = False
_load_error: str | None = None


# ---------------------------------------------------------------------------
# Model loading helpers
# ---------------------------------------------------------------------------

async def _download_model_from_gcs(gcs_uri: str, local_path: str) -> None:
    if not gcs_uri:
        logger.info("no_gcs_uri_skipping_download")
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

    # vLLM AWQ support by compute capability:
    #   sm70+ (Volta/Turing/Ampere/Ada): AutoAWQ kernels work → pass --quantization awq
    #   sm60  (Pascal / P100): AWQ kernels NOT supported — fail early with clear message
    if cc < 7.0:
        raise RuntimeError(
            f"GPU compute capability {cc} (Pascal or older) does not support AWQ. "
            "Redeploy on T4 (sm7.5) or newer."
        )

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
    # T4 (sm75): smaller context + eager mode (no CUDA graphs) to fit in 16 GB
    if cc < 8.0:
        cmd[cmd.index("8192")] = "4096"
        cmd[cmd.index("16")] = "8"
        cmd.append("--enforce-eager")

    lora_path = os.getenv("LORA_ADAPTER_PATH", "")
    if lora_path:
        cmd += ["--enable-lora", "--lora-modules", f"medibox-adapter={lora_path}"]

    logger.info("launching_vllm", cmd=" ".join(cmd))
    _vllm_proc = subprocess.Popen(cmd)


async def _wait_for_vllm(timeout: int = 1800) -> None:
    """Poll vLLM /health until it responds 200. Timeout: 30 min (HF download + load)."""
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
            await asyncio.sleep(10)
    raise TimeoutError(f"vLLM did not become ready within {timeout}s timeout")


async def _run_warmup() -> None:
    from services.vllm.warmup import warmup
    try:
        await warmup(vllm_url=VLLM_URL, model=VLLM_MODEL_NAME)
    except Exception as exc:
        logger.warning("warmup_failed_non_fatal", exc=str(exc))


async def _load_model_background() -> None:
    """
    Background task: download model (if GCS URI set), launch vLLM, wait for
    it to be ready, then run warmup. Sets _ready=True on success.
    Any exception is caught and stored in _load_error for observability.
    The HTTP server (including /health) stays alive throughout.
    """
    global _ready, _load_error
    try:
        if AIP_STORAGE_URI:
            await _download_model_from_gcs(AIP_STORAGE_URI, MODEL_PATH)
        await _launch_vllm()
        await _wait_for_vllm(timeout=1800)  # 30 min: HF download + GPU load
        await _run_warmup()
        _ready = True
        logger.info("server_ready", port=AIP_HTTP_PORT, model=MODEL_PATH)
    except Exception as exc:
        _load_error = str(exc)
        logger.error("model_load_failed", error=_load_error)
        # Do NOT exit — keep the server alive so /health keeps returning 200
        # with error info; operators can inspect and redeploy.


# ---------------------------------------------------------------------------
# Application lifespan (replaces deprecated on_event)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Launch model loading in background — server is immediately alive
    asyncio.create_task(_load_model_background())
    logger.info("http_server_started", port=AIP_HTTP_PORT)
    yield
    # Shutdown
    if _vllm_proc:
        _vllm_proc.terminate()


app = FastAPI(title="Medibox vLLM Vertex Server", docs_url=None, redoc_url=None,
              lifespan=lifespan)


# ---------------------------------------------------------------------------
# Health route — always returns 200
# ---------------------------------------------------------------------------

@app.get(AIP_HEALTH_ROUTE)
async def health():
    """
    Always returns HTTP 200 so Vertex AI startup probes don't kill the container
    while the GPU model is loading. The 'status' body field indicates true readiness.
    """
    if _load_error:
        return {"status": "error", "detail": _load_error}
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
    async with httpx.AsyncClient(timeout=120.0) as client:
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
    uvicorn.run(app, host="0.0.0.0", port=AIP_HTTP_PORT, log_level="info")

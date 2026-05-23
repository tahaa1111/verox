"""
Mock vLLM inference server — returns realistic fake prescription data.
Used for end-to-end pipeline testing without real GPU.
Replace with real Vertex AI endpoint once GPU is available.
"""
import json
import time
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="Mock vLLM", docs_url=None)

MOCK_PREDICTION = {
    "raw_output": json.dumps({
        "prescription_id": "RX-MOCK-001",
        "patient_name": "Mohamed Ben Salem",
        "doctor_name": "Dr. Salah Mzoughi",
        "issue_date": "2026-05-22",
        "medications": [
            {
                "drug_name": "Amoxicilline",
                "dosage": "500mg",
                "frequency": "3 fois par jour",
                "duration": "7 jours",
                "instructions": "Prendre avec de l'eau"
            },
            {
                "drug_name": "Ibuprofene",
                "dosage": "400mg",
                "frequency": "2 fois par jour",
                "duration": "5 jours",
                "instructions": "Prendre pendant les repas"
            }
        ],
        "additional_notes": "Repos conseillé"
    }),
    "cells": [],
    "logprobs": [{"token": "mock", "logprob": -0.1}],
    "model_version": "qwen2.5-vl-7b-awq-mock",
    "inference_ms": 120,
    "prompt_version": "v1",
    "prompt_tokens": 256,
    "completion_tokens": 128,
}

@app.get("/health")
async def health():
    return {"status": "ok", "mode": "mock"}

@app.post("/predict")
async def predict(request: Request):
    body = await request.json()
    instances = body.get("instances", [{}])
    t0 = time.perf_counter()
    # Simulate inference time (200ms)
    time.sleep(0.2)
    predictions = [MOCK_PREDICTION.copy() for _ in instances]
    for p in predictions:
        p["inference_ms"] = int((time.perf_counter() - t0) * 1000)
    return {"predictions": predictions}

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """OpenAI-compatible mock for direct vLLM fallback path."""
    time.sleep(0.2)
    return {
        "choices": [{
            "message": {
                "content": MOCK_PREDICTION["raw_output"]
            },
            "logprobs": {"content": MOCK_PREDICTION["logprobs"]}
        }],
        "usage": {"prompt_tokens": 256, "completion_tokens": 128}
    }

if __name__ == "__main__":
    import os
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)

"""3-run warmup with synthetic grid images (spec §5.1)."""

from __future__ import annotations

import base64
import io
import time

import httpx
from PIL import Image, ImageDraw


def _make_synthetic_grid() -> str:
    img = Image.new("RGB", (1536, 1536), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    for row in range(3):
        for col in range(3):
            x, y = col * 512, row * 512
            draw.rectangle([x, y, x + 511, y + 511], outline=(220, 50, 50), width=2)
            cell_num = row * 3 + col + 1
            draw.text((x + 20, y + 20), str(cell_num), fill=(220, 50, 50))
            draw.text((x + 50, y + 80), "Paracétamol 500mg 3x/j", fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


async def warmup(vllm_url: str, model: str) -> None:
    grid_b64 = _make_synthetic_grid()
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Extract prescription JSON."},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{grid_b64}"}},
                {"type": "text", "text": "Extract and return JSON."},
            ]},
        ],
        "max_tokens": 256,
        "temperature": 0.0,
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        for run in range(1, 4):
            t0 = time.perf_counter()
            resp = await client.post(f"{vllm_url}/v1/chat/completions", json=payload)
            resp.raise_for_status()
            ms = int((time.perf_counter() - t0) * 1000)
            print(f"[warmup] Run {run}/3 completed in {ms}ms", flush=True)
    print("[warmup] All 3 warmup runs complete. Model is hot.", flush=True)

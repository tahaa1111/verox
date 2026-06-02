"""
POST /v1/warmup — trigger RunPod model load before first real inference request.

The Pi or frontend calls this when a clinician opens the app, so the first
real prescription scan doesn't pay the full cold-start cost (~15–30s on RunPod
Serverless when the worker is scaled to zero).

The warmup request sends a minimal 1×1 white JPEG to RunPod — enough to load
the model weights into GPU memory without billing significant GPU time.

Rate-limited: 1 warmup per user per 5 minutes (prevents abuse).
Auth required: Firebase JWT.
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status

from services.api.core.auth import verify_firebase_token
from services.api.core.config import get_settings

logger = structlog.get_logger(__name__)
router = APIRouter()
settings = get_settings()

# 1×1 white JPEG — minimal valid image, ~600 bytes
_WARMUP_IMAGE_B64 = (
    "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkS"
    "Ew8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJ"
    "CQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy"
    "MjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAFgABAQEAAAAAAAAAAAAA"
    "AAAAAAf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFAEBAAAAAAAAAAAAAAAAAAAAAP/"
    "xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIRAxEAPwCwABmX/9k="
)

_WARMUP_RATE_KEY = "warmup:ratelimit:{uid}"
_WARMUP_RATE_TTL = 300  # 5 minutes


@router.post("/warmup", status_code=202)
async def warmup(
    request: Request,
    claims: dict = Depends(verify_firebase_token),
) -> dict:
    """Pre-warm the RunPod endpoint before the first scan of the session."""
    uid = claims.get("uid", claims.get("user_id", ""))

    # Rate limit: 1 warmup per user per 5 min
    import redis.asyncio as aioredis
    r = aioredis.from_url(
        settings.redis_url, decode_responses=True,
        socket_timeout=5, socket_connect_timeout=3,
    )
    try:
        key = _WARMUP_RATE_KEY.format(uid=uid)
        count = await r.incr(key)
        if count == 1:
            await r.expire(key, _WARMUP_RATE_TTL)
        if count > 1:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Warmup already requested. Wait 5 minutes before retrying.",
                headers={"Retry-After": str(_WARMUP_RATE_TTL)},
            )
    finally:
        await r.aclose()

    # Enqueue warmup job via arq (non-blocking — returns immediately)
    arq_pool = request.app.state.arq_pool
    await arq_pool.enqueue_job(
        "run_pipeline",
        f"warmup-{uid[:8]}",
        {
            "crops": [{
                "image_base64": _WARMUP_IMAGE_B64,
                "bbox": [0.0, 0.0, 1.0, 1.0],
                "confidence": 1.0,
                "track_id": 0,
            }],
            "device_id": "web-warmup",
            "session_id": "00000000-0000-4000-8000-000000000000",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "_warmup": True,
        },
        "warmup/noop",
    )

    logger.info("warmup_queued", uid=uid)
    return {"status": "warming_up", "message": "RunPod endpoint is loading model weights"}

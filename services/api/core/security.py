"""
Multi-layer rate limiting + abuse detection.

Layers (applied in order — first breach raises 429):
  1. Per-IP        200 req/min  — DDoS / scraper protection
  2. Per-user UID   60 req/min  — fair-use enforcement
  3. Per-device     30 req/min  — Pi / client throttle
  4. Global burst   50 req/5s   — spike absorption

Failure policy:
  Redis DOWN → FAIL CLOSED (503 Service Unavailable).
  Rationale: silently bypassing rate limits on Redis failure is a security
  vulnerability — an attacker can exploit the failure window. A 503 is
  operationally acceptable; a brute-force bypass is not.

Abuse detection:
  Auth failures per IP tracked in Redis (TTL 10 min).
  After ABUSE_THRESHOLD consecutive failures, that IP is soft-blocked for
  ABUSE_BLOCK_SECONDS. Events are recorded as structured log entries and
  incremented in Prometheus counters so Grafana alerts fire.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import structlog
from fastapi import HTTPException, status

if TYPE_CHECKING:
    import redis.asyncio as aioredis

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Configuration constants (tunable without code changes)
# ---------------------------------------------------------------------------

_WINDOW_SECS      = 60       # sliding window duration
_LIMIT_IP         = 200      # requests per IP per window
_LIMIT_USER       = 60       # requests per authenticated user per window
_LIMIT_DEVICE     = 30       # requests per device_id per window
_BURST_WINDOW     = 5        # burst window in seconds
_BURST_LIMIT      = 50       # max requests in burst window (any source)

_ABUSE_THRESHOLD  = 10       # consecutive auth failures before soft-block
_ABUSE_WINDOW     = 600      # track failures for 10 min
_ABUSE_BLOCK_SECS = 900      # soft-block duration: 15 min


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

async def check_rate_limits(
    r: "aioredis.Redis",
    ip: str,
    user_id: str,
    device_id: str,
) -> None:
    """
    Multi-layer sliding window rate limit check.
    Raises HTTP 429 if any layer is breached.
    Raises HTTP 503 if Redis is unreachable (fail-closed).
    """
    now = int(time.time())
    burst_bucket = now // _BURST_WINDOW  # 5-second bucket key

    layers = [
        (f"rl:ip:{ip}",            _LIMIT_IP,     "ip",     _WINDOW_SECS),
        (f"rl:user:{user_id}",     _LIMIT_USER,   "user",   _WINDOW_SECS),
        (f"rl:device:{device_id}", _LIMIT_DEVICE, "device", _WINDOW_SECS),
        (f"rl:burst:{burst_bucket}", _BURST_LIMIT, "burst", _BURST_WINDOW + 2),
    ]

    try:
        pipe = r.pipeline()
        for key, _, _, ttl in layers:
            pipe.incr(key)
            pipe.expire(key, ttl)
        results = await pipe.execute()
    except Exception as exc:
        logger.error("rate_limit_redis_unavailable", exc=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Rate limiter unavailable — try again shortly",
        )

    counts = results[::2]  # incr results (every other item — expire returns bool)
    for (key, limit, layer, _), count in zip(layers, counts):
        if count > limit:
            try:
                # Import lazily to avoid circular import
                from services.api.core.metrics import RATE_LIMIT_HITS
                RATE_LIMIT_HITS.labels(layer=layer).inc()
            except Exception:
                pass
            logger.warning(
                "rate_limit_exceeded",
                layer=layer,
                ip=ip,
                user_id=user_id,
                device_id=device_id,
                count=count,
                limit=limit,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded ({layer}): {limit} requests per "
                       f"{'minute' if layer != 'burst' else '5 seconds'}",
                headers={"Retry-After": str(_WINDOW_SECS)},
            )


# ---------------------------------------------------------------------------
# Abuse detection — auth failure tracking
# ---------------------------------------------------------------------------

async def record_auth_failure(r: "aioredis.Redis", ip: str, reason: str) -> None:
    """
    Increment auth failure counter for this IP.
    If threshold crossed, log a security event (Prometheus counter + structured log).
    Does NOT raise — called from exception handlers where we want to log then re-raise.
    """
    try:
        from services.api.core.metrics import AUTH_FAILURES, SUSPICIOUS_REQUESTS
        AUTH_FAILURES.labels(reason=reason).inc()
    except Exception:
        pass

    try:
        key = f"abuse:auth:{ip}"
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.expire(key, _ABUSE_WINDOW)
        results = await pipe.execute()
        count = results[0]

        if count >= _ABUSE_THRESHOLD:
            try:
                from services.api.core.metrics import SUSPICIOUS_REQUESTS
                SUSPICIOUS_REQUESTS.labels(type="brute_force").inc()
            except Exception:
                pass
            logger.warning(
                "security_brute_force_detected",
                ip=ip,
                failure_count=count,
                block_seconds=_ABUSE_BLOCK_SECS,
            )
            # Soft-block: set a separate block key
            await r.set(f"abuse:block:{ip}", "1", ex=_ABUSE_BLOCK_SECS)

    except Exception as exc:
        logger.error("abuse_tracking_failed", exc=str(exc))


async def check_ip_blocked(r: "aioredis.Redis", ip: str) -> None:
    """Raise 403 if this IP is currently soft-blocked for abuse."""
    try:
        blocked = await r.get(f"abuse:block:{ip}")
        if blocked:
            try:
                from services.api.core.metrics import SUSPICIOUS_REQUESTS
                SUSPICIOUS_REQUESTS.labels(type="blocked_ip_attempt").inc()
            except Exception:
                pass
            logger.warning("security_blocked_ip_attempt", ip=ip)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Temporarily blocked due to repeated authentication failures",
                headers={"Retry-After": str(_ABUSE_BLOCK_SECS)},
            )
    except HTTPException:
        raise
    except Exception:
        pass  # Redis failure: don't block — fail open here (block check only)

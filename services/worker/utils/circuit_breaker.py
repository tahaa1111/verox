"""
Circuit breaker for RunPod/vLLM inference calls.

States:
  CLOSED   — normal operation; failures counted
  OPEN     — endpoint presumed down; calls rejected immediately (fail-fast)
  HALF_OPEN — probe state; one trial request allowed

Thresholds (env-configurable):
  CB_FAILURE_THRESHOLD   = 3   failures within the window → OPEN
  CB_WINDOW_SECONDS      = 60  failure counting window
  CB_OPEN_SECONDS        = 120 how long to stay OPEN before trying HALF_OPEN
  CB_SUCCESS_THRESHOLD   = 1   successes in HALF_OPEN → back to CLOSED

State is stored in Redis so multiple arq tasks share the same breaker state.
All Redis operations are best-effort — if Redis is unavailable the circuit
defaults to CLOSED (allow through) to avoid cascading failures.
"""

from __future__ import annotations

import os
import time
from enum import Enum

import redis.asyncio as aioredis
import structlog

logger = structlog.get_logger(__name__)

_CB_KEY_STATE     = "cb:runpod:state"
_CB_KEY_FAILURES  = "cb:runpod:failures"
_CB_KEY_OPENED_AT = "cb:runpod:opened_at"

_FAILURE_THRESHOLD = int(os.getenv("CB_FAILURE_THRESHOLD", "3"))
_WINDOW_SECONDS    = int(os.getenv("CB_WINDOW_SECONDS",    "60"))
_OPEN_SECONDS      = int(os.getenv("CB_OPEN_SECONDS",      "120"))


class CircuitState(str, Enum):
    CLOSED    = "closed"
    OPEN      = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when the circuit is OPEN and the call is rejected."""


async def _get_state(r: aioredis.Redis) -> CircuitState:
    state = await r.get(_CB_KEY_STATE)
    if not state:
        return CircuitState.CLOSED
    return CircuitState(state)


async def check_circuit(redis_url: str) -> None:
    """
    Raises CircuitOpenError if the circuit is OPEN.
    Transitions OPEN → HALF_OPEN when the cool-off period expires.
    Must be called before every RunPod request.
    """
    try:
        r = aioredis.from_url(
            redis_url, decode_responses=True,
            socket_timeout=3, socket_connect_timeout=3,
        )
        try:
            state = await _get_state(r)

            if state == CircuitState.OPEN:
                opened_at = await r.get(_CB_KEY_OPENED_AT)
                if opened_at and (time.time() - float(opened_at)) >= _OPEN_SECONDS:
                    await r.set(_CB_KEY_STATE, CircuitState.HALF_OPEN)
                    logger.info("circuit_half_open")
                else:
                    raise CircuitOpenError(
                        "RunPod circuit breaker is OPEN — endpoint presumed down. "
                        f"Retrying after {_OPEN_SECONDS}s cool-off."
                    )
        finally:
            await r.aclose()
    except CircuitOpenError:
        raise
    except Exception:
        pass  # Redis unavailable — default to CLOSED (allow through)


async def record_success(redis_url: str) -> None:
    """Call after a successful RunPod response. Resets the breaker to CLOSED."""
    try:
        r = aioredis.from_url(
            redis_url, decode_responses=True,
            socket_timeout=3, socket_connect_timeout=3,
        )
        try:
            state = await _get_state(r)
            if state in (CircuitState.HALF_OPEN, CircuitState.OPEN):
                logger.info("circuit_closed_after_success", previous_state=state)
            await r.delete(_CB_KEY_STATE, _CB_KEY_FAILURES, _CB_KEY_OPENED_AT)
        finally:
            await r.aclose()
    except Exception:
        pass


async def record_failure(redis_url: str) -> None:
    """
    Call after a RunPod failure.
    Increments failure counter; opens circuit if threshold exceeded.
    """
    try:
        r = aioredis.from_url(
            redis_url, decode_responses=True,
            socket_timeout=3, socket_connect_timeout=3,
        )
        try:
            pipe = r.pipeline()
            pipe.incr(_CB_KEY_FAILURES)
            pipe.expire(_CB_KEY_FAILURES, _WINDOW_SECONDS)
            results = await pipe.execute()
            failures = results[0]

            if failures >= _FAILURE_THRESHOLD:
                state = await _get_state(r)
                if state != CircuitState.OPEN:
                    await r.set(_CB_KEY_STATE, CircuitState.OPEN)
                    await r.set(_CB_KEY_OPENED_AT, str(time.time()))
                    logger.warning(
                        "circuit_opened",
                        failures=failures,
                        threshold=_FAILURE_THRESHOLD,
                        open_seconds=_OPEN_SECONDS,
                    )
        finally:
            await r.aclose()
    except Exception:
        pass

"""
Firebase JWT verification + admin_role_grants second-factor check.
Abuse detection: auth failures tracked per-IP in Redis.
"""

from __future__ import annotations

import os
from typing import Any

import structlog
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from services.api.core.config import get_settings
from services.api.core.metrics import AUTH_FAILURES

logger = structlog.get_logger(__name__)
settings = get_settings()

_bearer = HTTPBearer(auto_error=False)
_firebase_app = None


def _get_firebase_app():
    global _firebase_app
    if _firebase_app is not None:
        return _firebase_app
    import json as _json
    import firebase_admin
    from firebase_admin import credentials

    if not firebase_admin._apps:
        cred_path = settings.firebase_credentials_path
        firebase_admin_json_str = os.getenv("FIREBASE_ADMIN_JSON", "")
        if cred_path and os.path.exists(cred_path):
            cred = credentials.Certificate(cred_path)
        elif firebase_admin_json_str:
            try:
                json_data = _json.loads(firebase_admin_json_str)
                cred = credentials.Certificate(json_data)
            except Exception as exc:
                logger.warning("firebase_admin_json_parse_error", error=str(exc))
                cred = credentials.ApplicationDefault()
        else:
            cred = credentials.ApplicationDefault()

        project_id = settings.firebase_project_id
        if not project_id and firebase_admin_json_str:
            try:
                project_id = _json.loads(firebase_admin_json_str).get("project_id", "")
            except Exception:
                pass

        _firebase_app = firebase_admin.initialize_app(
            cred, {"projectId": project_id} if project_id else {}
        )
    else:
        _firebase_app = firebase_admin.get_app()
    return _firebase_app


async def verify_firebase_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict[str, Any]:
    ip = request.client.host if request.client else "unknown"

    if credentials is None:
        AUTH_FAILURES.labels(reason="missing_token").inc()
        # Track for abuse detection
        await _track_auth_failure(ip, "missing_token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    try:
        from firebase_admin import auth as fb_auth
        _get_firebase_app()
        decoded = fb_auth.verify_id_token(token, check_revoked=True)
        return decoded
    except Exception as exc:
        reason = _classify_firebase_error(exc)
        AUTH_FAILURES.labels(reason=reason).inc()
        await _track_auth_failure(ip, reason)
        logger.warning("firebase_jwt_invalid", reason=reason, ip=ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _classify_firebase_error(exc: Exception) -> str:
    msg = str(exc).lower()
    if "revoked" in msg:
        return "revoked"
    if "expired" in msg:
        return "expired"
    if "invalid" in msg:
        return "invalid_token"
    return "unknown"


async def _track_auth_failure(ip: str, reason: str) -> None:
    try:
        import redis.asyncio as aioredis
        from services.api.core.security import record_auth_failure
        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            await record_auth_failure(r, ip, reason)
        finally:
            await r.aclose()
    except Exception:
        pass  # tracking failure must never block auth response


def verify_device_claim(claims: dict[str, Any], device_id: str) -> None:
    """Ensure JWT device_id claim matches payload device_id."""
    jwt_device_id = claims.get("device_id")
    if jwt_device_id is None:
        if not device_id.startswith("web-") and not device_id.startswith("upload-"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Browser sessions must use a 'web-' or 'upload-' prefixed device_id",
            )
        return
    if jwt_device_id != device_id:
        AUTH_FAILURES.labels(reason="device_mismatch").inc()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="device_id claim does not match payload",
        )


async def require_admin(
    request: Request,
    claims: dict[str, Any] = Depends(verify_firebase_token),
) -> dict[str, Any]:
    """
    Admin endpoints require:
    1. admin: true in Firebase JWT custom claims
    2. A non-revoked row in admin_role_grants table
    """
    if not claims.get("admin"):
        AUTH_FAILURES.labels(reason="insufficient_role").inc()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    uid = claims.get("uid", claims.get("user_id", ""))
    from sqlalchemy import text
    from services.api.core.database import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text(
                "SELECT id FROM admin_role_grants "
                "WHERE user_id = :uid AND revoked_at IS NULL LIMIT 1"
            ),
            {"uid": uid},
        )
        row = result.fetchone()

    if row is None:
        AUTH_FAILURES.labels(reason="grant_missing").inc()
        logger.warning("admin_grant_missing", uid=uid)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin grant not found — contact system administrator",
        )

    try:
        from services.api.core.metrics import ADMIN_ACTIONS
        ADMIN_ACTIONS.labels(action=request.url.path).inc()
    except Exception:
        pass

    return claims


async def get_current_user_ws(token: str) -> dict[str, Any]:
    """Authenticate WebSocket connections via ?token= query param."""
    try:
        from firebase_admin import auth as fb_auth
        _get_firebase_app()
        return fb_auth.verify_id_token(token, check_revoked=True)
    except Exception as exc:
        reason = _classify_firebase_error(exc)
        AUTH_FAILURES.labels(reason=reason).inc()
        logger.warning("ws_jwt_invalid", reason=reason)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid WebSocket token")

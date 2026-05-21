"""
Firebase JWT verification + admin_role_grants second-factor check (DD-007, DD-013).
"""

from __future__ import annotations

import os
from typing import Any

import structlog
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from services.api.core.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

_bearer = HTTPBearer(auto_error=False)

# Lazy Firebase app initialization (avoids import-time credential requirement)
_firebase_app = None


def _get_firebase_app():
    global _firebase_app
    if _firebase_app is not None:
        return _firebase_app
    import firebase_admin
    from firebase_admin import credentials
    if not firebase_admin._apps:
        cred_path = settings.firebase_credentials_path
        if cred_path and os.path.exists(cred_path):
            cred = credentials.Certificate(cred_path)
        else:
            # Cloud Run: use Application Default Credentials
            cred = credentials.ApplicationDefault()
        _firebase_app = firebase_admin.initialize_app(cred, {"projectId": settings.firebase_project_id})
    else:
        _firebase_app = firebase_admin.get_app()
    return _firebase_app


async def verify_firebase_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict[str, Any]:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Bearer token")
    token = credentials.credentials
    try:
        from firebase_admin import auth as fb_auth
        _get_firebase_app()
        decoded = fb_auth.verify_id_token(token, check_revoked=True)
        return decoded
    except Exception as exc:
        logger.warning("firebase_jwt_invalid", error=str(exc))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")


def verify_device_claim(claims: dict[str, Any], device_id: str) -> None:
    """Ensure JWT device_id claim matches payload device_id."""
    if claims.get("device_id") != device_id:
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
    2. A non-revoked row in admin_role_grants table (DD-013)
    """
    if not claims.get("admin"):
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
        logger.warning("admin_grant_missing", uid=uid)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin grant not found — contact system administrator",
        )
    return claims


async def get_current_user_ws(token: str) -> dict[str, Any]:
    """Authenticate WebSocket connections via ?token= query param."""
    try:
        from firebase_admin import auth as fb_auth
        _get_firebase_app()
        return fb_auth.verify_id_token(token, check_revoked=True)
    except Exception as exc:
        logger.warning("ws_jwt_invalid", error=str(exc))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid WebSocket token")

"""Unit tests for auth module — Firebase JWT verification and require_admin check."""

import sys
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials


def _firebase_modules() -> dict:
    """
    Return sys.modules patches for firebase_admin (not installed in test env).
    Keeps the mock stable across the with-block so re-imports get the same object.
    """
    mock_fb = MagicMock()
    return {
        "firebase_admin": mock_fb,
        "firebase_admin.auth": mock_fb.auth,
        "firebase_admin.credentials": mock_fb.credentials,
    }


class TestVerifyFirebaseToken:
    @pytest.mark.asyncio
    async def test_valid_token_returns_claims(self):
        mock_claims = {"uid": "user123", "email": "pharmacist@clinic.tn", "admin": False}
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="fake_token")

        mods = _firebase_modules()
        mods["firebase_admin"].auth.verify_id_token.return_value = mock_claims
        with patch.dict(sys.modules, mods), \
             patch("services.api.core.auth._get_firebase_app", return_value=MagicMock()):
            from services.api.core.auth import verify_firebase_token
            result = await verify_firebase_token(credentials=creds)
            assert result["uid"] == "user123"

    @pytest.mark.asyncio
    async def test_missing_credentials_raises_401(self):
        from services.api.core.auth import verify_firebase_token
        with pytest.raises(HTTPException) as exc_info:
            await verify_firebase_token(credentials=None)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_expired_token_raises_401(self):
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="expired_token")
        mods = _firebase_modules()
        mods["firebase_admin"].auth.verify_id_token.side_effect = Exception("Token expired")
        with patch.dict(sys.modules, mods), \
             patch("services.api.core.auth._get_firebase_app", return_value=MagicMock()):
            from services.api.core.auth import verify_firebase_token
            with pytest.raises(HTTPException) as exc_info:
                await verify_firebase_token(credentials=creds)
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_token_raises_401(self):
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="invalid_token")
        mods = _firebase_modules()
        mods["firebase_admin"].auth.verify_id_token.side_effect = ValueError("Invalid token")
        with patch.dict(sys.modules, mods), \
             patch("services.api.core.auth._get_firebase_app", return_value=MagicMock()):
            from services.api.core.auth import verify_firebase_token
            with pytest.raises(HTTPException) as exc_info:
                await verify_firebase_token(credentials=creds)
            assert exc_info.value.status_code == 401


def _make_db_module(fetchone_return) -> MagicMock:
    """
    Build a fake services.api.core.database module with a working AsyncSessionLocal.
    require_admin does: `from services.api.core.database import AsyncSessionLocal`
    so we register the fake module in sys.modules before calling it.
    """
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.fetchone.return_value = fetchone_return
    mock_session.execute.return_value = mock_result

    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_db_module = MagicMock()
    mock_db_module.AsyncSessionLocal.return_value = mock_ctx
    return mock_db_module


class TestRequireAdmin:
    """require_admin(request, claims) — FastAPI dependency called with resolved args."""

    @pytest.mark.asyncio
    async def test_admin_claim_and_db_row_pass(self):
        mock_claims = {"uid": "admin1", "admin": True}
        mock_request = MagicMock()
        # row exists
        db_mod = _make_db_module(fetchone_return=MagicMock())
        # require_admin also does `from sqlalchemy import text` locally
        mock_sqla = MagicMock()
        with patch.dict(sys.modules, {
            "services.api.core.database": db_mod,
            "sqlalchemy": mock_sqla,
        }):
            from services.api.core.auth import require_admin
            result = await require_admin(request=mock_request, claims=mock_claims)
            assert result["uid"] == "admin1"

    @pytest.mark.asyncio
    async def test_no_admin_claim_raises_403(self):
        mock_claims = {"uid": "user1", "admin": False}
        mock_request = MagicMock()

        from services.api.core.auth import require_admin
        with pytest.raises(HTTPException) as exc_info:
            await require_admin(request=mock_request, claims=mock_claims)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_claim_without_db_row_raises_403(self):
        """DD-013: Both Firebase admin claim AND admin_role_grants row required."""
        mock_claims = {"uid": "admin1", "admin": True}
        mock_request = MagicMock()
        # no DB row
        db_mod = _make_db_module(fetchone_return=None)
        mock_sqla = MagicMock()
        with patch.dict(sys.modules, {
            "services.api.core.database": db_mod,
            "sqlalchemy": mock_sqla,
        }):
            from services.api.core.auth import require_admin
            with pytest.raises(HTTPException) as exc_info:
                await require_admin(request=mock_request, claims=mock_claims)
            assert exc_info.value.status_code == 403

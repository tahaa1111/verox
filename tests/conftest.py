"""Shared pytest configuration and fixtures."""

import os
import sys
import pytest

# Add project root to path so imports work without installing packages
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch):
    """Ensure tests never accidentally hit real GCP services."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("VERTEX_ENDPOINT_ID", "")
    monkeypatch.setenv("GCS_CROPS_BUCKET", "test-crops-bucket")
    monkeypatch.setenv("GCS_MODELS_BUCKET", "test-models-bucket")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/testdb")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "test-firebase-project")
    monkeypatch.setenv("KMS_KEY_NAME", "")
    monkeypatch.setenv("ENVIRONMENT", "test")

"""
Google Cloud Storage client for crop upload / model artifact access.
Replaces MinIO from the on-prem spec.
"""

from __future__ import annotations

import os
from functools import lru_cache

from google.cloud import storage

_CROPS_BUCKET = os.getenv("GCS_CROPS_BUCKET", "")
_MODELS_BUCKET = os.getenv("GCS_MODELS_BUCKET", "")
_RAW_BUCKET = os.getenv("GCS_RAW_BUCKET", "")


@lru_cache(maxsize=1)
def _client() -> storage.Client:
    return storage.Client()


def upload_crop(job_id: str, track_id: int, raw_bytes: bytes, content_type: str = "image/jpeg") -> str:
    """Upload a crop image to the crops bucket. Returns the GCS URI."""
    blob_name = f"{job_id}/crop_{track_id:04d}.jpg"
    bucket = _client().bucket(_CROPS_BUCKET)
    blob = bucket.blob(blob_name)
    blob.upload_from_string(raw_bytes, content_type=content_type)
    return f"gs://{_CROPS_BUCKET}/{blob_name}"


def upload_bytes(bucket_name: str, blob_name: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    """Generic upload. Returns GCS URI."""
    bucket = _client().bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_string(data, content_type=content_type)
    return f"gs://{bucket_name}/{blob_name}"


def download_bytes(gcs_uri: str) -> bytes:
    """Download from a gs:// URI. Returns bytes."""
    if not gcs_uri.startswith("gs://"):
        raise ValueError(f"Invalid GCS URI: {gcs_uri}")
    path = gcs_uri[5:]
    bucket_name, _, blob_name = path.partition("/")
    bucket = _client().bucket(bucket_name)
    return bucket.blob(blob_name).download_as_bytes()

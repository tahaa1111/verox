"""
Cloudflare R2 crop storage client (S3-compatible via boto3).

All R2 objects are PRIVATE. Content is served exclusively via presigned URLs
(TTL 300s by default) generated per-request after authentication.
No object is ever made public.

Environment variables:
  R2_ENDPOINT       — https://<account>.eu.r2.cloudflarestorage.com
  R2_ACCESS_KEY_ID  — R2 API token access key
  R2_SECRET_KEY     — R2 API token secret key
  R2_BUCKET         — bucket name (medibox-crops)
"""

from __future__ import annotations

import os
from functools import lru_cache

import boto3
from botocore.config import Config

_ENDPOINT   = os.getenv("R2_ENDPOINT", "")
_ACCESS_KEY = os.getenv("R2_ACCESS_KEY_ID", "")
_SECRET_KEY = os.getenv("R2_SECRET_KEY", "")
_BUCKET     = os.getenv("R2_BUCKET", "medibox-crops")


@lru_cache(maxsize=1)
def _client():
    return boto3.client(
        "s3",
        endpoint_url=_ENDPOINT,
        aws_access_key_id=_ACCESS_KEY,
        aws_secret_access_key=_SECRET_KEY,
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 2},
        ),
    )


def upload_crop(
    job_id: str,
    track_id: int,
    raw_bytes: bytes,
    content_type: str = "image/jpeg",
) -> str:
    """Upload a crop image to R2. Returns the R2 object key (not a public URL)."""
    key = f"{job_id}/crop_{track_id:04d}.jpg"
    _client().put_object(
        Bucket=_BUCKET,
        Key=key,
        Body=raw_bytes,
        ContentType=content_type,
    )
    return key  # return key, not URI — caller generates presigned URL on demand


def generate_presigned_url(key: str, expires_in: int = 300) -> str:
    """
    Generate a presigned GET URL for a private R2 object.
    Default TTL: 5 minutes. Never store this URL — generate fresh per request.
    """
    return _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": _BUCKET, "Key": key},
        ExpiresIn=expires_in,
    )


def upload_bytes(
    bucket_name: str,
    blob_name: str,
    data: bytes,
    content_type: str = "application/octet-stream",
) -> str:
    """Generic upload. Returns object key."""
    _client().put_object(
        Bucket=bucket_name,
        Key=blob_name,
        Body=data,
        ContentType=content_type,
    )
    return blob_name

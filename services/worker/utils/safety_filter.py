"""
Input safety filter for crop images (§5.4 step 1).
Validates before sending to Vertex AI to prevent decompression bombs,
oversized images, and malformed inputs.
"""

from __future__ import annotations

import base64
import io

from PIL import Image, UnidentifiedImageError

MAX_PIXELS = 25_000_000    # 25 MP
MAX_DIMENSION = 4096       # pixels per side
MAX_BYTES_DECODED = 2 * 1024 * 1024


class SafetyFilterError(ValueError):
    pass


def validate_crop_image(image_base64: str, track_id: int = 0) -> bytes:
    """
    Decode and validate a single crop image.
    Returns raw bytes if safe. Raises SafetyFilterError otherwise.
    """
    try:
        raw = base64.b64decode(image_base64, validate=True)
    except Exception:
        raise SafetyFilterError(f"track_id={track_id}: invalid base64")

    if len(raw) > MAX_BYTES_DECODED:
        raise SafetyFilterError(
            f"track_id={track_id}: decoded image {len(raw)} bytes exceeds 2 MB limit"
        )

    try:
        # PIL decompression bomb protection — raises DecompressionBombError if > Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = MAX_PIXELS
        with Image.open(io.BytesIO(raw)) as img:
            w, h = img.size
            if w > MAX_DIMENSION or h > MAX_DIMENSION:
                raise SafetyFilterError(
                    f"track_id={track_id}: image dimensions {w}×{h} exceed {MAX_DIMENSION}px limit"
                )
            total_pixels = w * h
            if total_pixels > MAX_PIXELS:
                raise SafetyFilterError(
                    f"track_id={track_id}: image has {total_pixels} pixels, exceeds {MAX_PIXELS}"
                )
            fmt = img.format
            if fmt not in ("JPEG", "PNG"):
                raise SafetyFilterError(
                    f"track_id={track_id}: unsupported format {fmt!r}; must be JPEG or PNG"
                )
    except Image.DecompressionBombError:
        raise SafetyFilterError(f"track_id={track_id}: decompression bomb detected")
    except SafetyFilterError:
        raise
    except UnidentifiedImageError:
        raise SafetyFilterError(f"track_id={track_id}: cannot identify image format")
    except Exception as exc:
        raise SafetyFilterError(f"track_id={track_id}: image validation error: {exc}")

    return raw


def validate_all_crops(crops: list[dict]) -> list[bytes]:
    """Validate all crops in a payload. Returns list of raw bytes in same order."""
    results = []
    for crop in crops:
        raw = validate_crop_image(crop["image_base64"], crop.get("track_id", 0))
        results.append(raw)
    return results

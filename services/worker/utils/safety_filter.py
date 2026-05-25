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

# Blank-image detection thresholds.
# A pixel is "dark" if its luminance < DARK_PIXEL_THRESHOLD (0-255).
# If fewer than DARK_PIXEL_MIN_FRACTION of pixels are dark, the image
# is considered blank (empty sheet, all-white, accidental blank photo).
DARK_PIXEL_THRESHOLD = 200     # pixels darker than this are "content"
DARK_PIXEL_MIN_FRACTION = 0.002  # at least 0.2% of pixels must be dark


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

    # Blank / empty image detection — reject before sending to vLLM.
    # Convert to grayscale and check what fraction of pixels carry real content.
    # A real prescription has text on white paper → mixed dark + bright pixels.
    # Two failure modes:
    #   • All-white / blank sheet  → almost NO dark pixels (dark_fraction < 0.2%)
    #   • All-black / lens cap     → almost NO bright pixels (bright_fraction < 0.2%)
    try:
        with Image.open(io.BytesIO(raw)) as img:
            gray = img.convert("L")
            pixels = list(gray.getdata())
            n = len(pixels)
            if n == 0:
                raise SafetyFilterError(f"track_id={track_id}: image has no pixels")
            dark_count = sum(1 for p in pixels if p < DARK_PIXEL_THRESHOLD)
            dark_fraction = dark_count / n
            bright_fraction = 1.0 - dark_fraction
            # All-white / blank check
            if dark_fraction < DARK_PIXEL_MIN_FRACTION:
                raise SafetyFilterError(
                    f"track_id={track_id}: image appears blank or empty "
                    f"(only {dark_fraction*100:.3f}% dark pixels — "
                    "please upload a clear photo of the prescription)"
                )
            # All-black / dark frame check
            if bright_fraction < DARK_PIXEL_MIN_FRACTION:
                raise SafetyFilterError(
                    f"track_id={track_id}: image appears all-dark or black "
                    f"(only {bright_fraction*100:.3f}% bright pixels — "
                    "please ensure the image is properly lit and in focus)"
                )
    except SafetyFilterError:
        raise
    except Exception:
        pass  # if blank-check itself fails, let the image through

    return raw


def validate_all_crops(crops: list[dict]) -> list[bytes]:
    """Validate all crops in a payload. Returns list of raw bytes in same order."""
    results = []
    for crop in crops:
        raw = validate_crop_image(crop["image_base64"], crop.get("track_id", 0))
        results.append(raw)
    return results

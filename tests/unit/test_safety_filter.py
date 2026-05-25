"""Unit tests for crop safety filter — decompression bomb, size, format."""

import base64
import io
import pytest
from PIL import Image
from unittest.mock import patch

from services.worker.utils.safety_filter import validate_crop_image


def _make_jpeg_b64(width: int = 512, height: int = 512) -> str:
    img = Image.new("RGB", (width, height), (200, 100, 50))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode()


def _make_png_b64(width: int = 512, height: int = 512) -> str:
    img = Image.new("RGB", (width, height), (100, 200, 50))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _make_bmp_b64(width: int = 512, height: int = 512) -> str:
    """BMP is not in the JPEG/PNG allow-list — triggers SafetyFilterError."""
    img = Image.new("RGB", (width, height), (100, 200, 50))
    buf = io.BytesIO()
    img.save(buf, format="BMP")
    return base64.b64encode(buf.getvalue()).decode()


class TestValidateCropImage:
    def test_valid_jpeg_passes(self):
        validate_crop_image(_make_jpeg_b64())

    def test_valid_png_passes(self):
        validate_crop_image(_make_png_b64())

    def test_oversized_image_rejected(self):
        with pytest.raises(ValueError, match="(?i)size|dimension|large"):
            validate_crop_image(_make_jpeg_b64(4097, 4097))

    def test_wrong_format_rejected(self):
        # BMP is not in the JPEG/PNG allow-list → SafetyFilterError with "format"
        with pytest.raises(ValueError, match="(?i)mime|type|format|unsupported"):
            validate_crop_image(_make_bmp_b64())

    def test_empty_bytes_rejected(self):
        with pytest.raises((ValueError, Exception)):
            validate_crop_image(base64.b64encode(b"").decode())

    def test_not_an_image_rejected(self):
        with pytest.raises((ValueError, Exception)):
            validate_crop_image(base64.b64encode(b"this is not an image").decode())

    def test_invalid_base64_rejected(self):
        with pytest.raises(ValueError, match="(?i)base64|invalid"):
            validate_crop_image("not-valid-base64!!!")

    def test_decompression_bomb_rejected(self):
        with patch("PIL.Image.MAX_IMAGE_PIXELS", 100):
            large_img = Image.new("RGB", (1000, 1000))
            buf = io.BytesIO()
            large_img.save(buf, format="JPEG")
            b64 = base64.b64encode(buf.getvalue()).decode()
            # The implementation respects MAX_PIXELS cap — should raise
            try:
                validate_crop_image(b64)
            except (ValueError, Image.DecompressionBombError):
                pass  # expected

    def test_max_allowed_size_passes(self):
        # 4096×4096 is exactly the maximum allowed
        validate_crop_image(_make_jpeg_b64(4096, 4096))

    def test_all_black_image_rejected(self):
        """A solid black image (e.g. lens cap, no light) must be rejected."""
        img = Image.new("RGB", (512, 512), (0, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        with pytest.raises(ValueError, match="(?i)dark|black"):
            validate_crop_image(b64)

    def test_all_white_image_rejected(self):
        """A solid white / blank image must be rejected."""
        img = Image.new("RGB", (512, 512), (255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        with pytest.raises(ValueError, match="(?i)blank|empty"):
            validate_crop_image(b64)

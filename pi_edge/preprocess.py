"""
CLAHE-based image preprocessing for prescription crops.

Applied on the Pi before base64-encoding crops to send to the cloud OCR pipeline.
Improves contrast and sharpness of handwritten/printed text on varied backgrounds.
"""
import cv2
import numpy as np


def preprocess_crop(img_bgr: np.ndarray) -> np.ndarray:
    """
    Enhance a prescription crop for OCR:
      1. Grayscale conversion
      2. CLAHE — contrast-limited adaptive histogram equalization
      3. Unsharp-mask sharpening (5-tap kernel)
      4. Non-local means denoising (conservative h=10, preserves text edges)
      5. Convert back to BGR so the rest of the pipeline can re-encode as JPEG

    Args:
        img_bgr: BGR numpy array (H, W, 3) — e.g. direct YOLO crop from cv2 frame.

    Returns:
        BGR numpy array of the same spatial dimensions, enhanced for OCR.
    """
    # 1. Grayscale
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)

    # 2. CLAHE (clipLimit=2.0, 8×8 tiles — good default for A5/A6 prescription paper)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)

    # 3. Unsharp-mask sharpening (Laplacian-based 3×3 kernel)
    kernel = np.array([[0, -1,  0],
                       [-1,  5, -1],
                       [0, -1,  0]], dtype=np.float32)
    sharpened = cv2.filter2D(enhanced, -1, kernel)
    sharpened = np.clip(sharpened, 0, 255).astype(np.uint8)

    # 4. Non-local means denoising (h=10 — low strength, preserves pen strokes)
    denoised = cv2.fastNlMeansDenoising(sharpened, h=10)

    # 5. Back to BGR so imencode(".jpg", ...) works unchanged in the caller
    return cv2.cvtColor(denoised, cv2.COLOR_GRAY2BGR)

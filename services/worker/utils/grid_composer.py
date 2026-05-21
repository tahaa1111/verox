"""
Grid Composition — the core inference optimization (spec §5.2, DD-005).

Composes ≤9 prescription crops into a single 1536×1536 3×3 grid image.
One vision-encoder forward pass amortizes across all crops.

Layout:
 ┌───┬───┬───┐
 │ 1 │ 2 │ 3 │
 ├───┼───┼───┤
 │ 4 │ 5 │ 6 │
 ├───┼───┼───┤
 │ 7 │ 8 │ 9 │
 └───┴───┴───┘

Each cell is 512×512 with 16px internal padding and a thin red border.
Cell number is drawn in the top-left corner for model reference.
Jobs with >9 crops are split into sequential grids; results are merged by
track_id after postprocessing.
"""

import base64
import io
from dataclasses import dataclass

from PIL import Image, ImageDraw

# Grid constants
GRID_SIZE = 1536           # total canvas
CELLS_PER_ROW = 3
CELL_SIZE = GRID_SIZE // CELLS_PER_ROW   # 512px
CELL_PADDING = 16          # whitespace inside each cell
CONTENT_SIZE = CELL_SIZE - 2 * CELL_PADDING  # 480px usable
BORDER_COLOR = (220, 50, 50)   # red border
BORDER_WIDTH = 2
LABEL_COLOR = (220, 50, 50)
LABEL_BG = (255, 255, 255)
BACKGROUND_COLOR = (255, 255, 255)

MAX_CELLS = CELLS_PER_ROW * CELLS_PER_ROW  # 9


@dataclass
class CropSlot:
    """Maps a cell position (1-based) to the original track_id."""
    cell_number: int    # 1–9
    track_id: int
    original_index: int  # index in the original crops list


@dataclass
class GridResult:
    grid_b64: str               # base64-encoded JPEG grid image
    slots: list[CropSlot]       # maps cell_number → track_id
    grid_index: int             # which grid (0-based) for multi-grid jobs


def compose_grids(crops: list[dict]) -> list[GridResult]:
    """
    Split crops into groups of MAX_CELLS, compose each group into a grid image.
    Returns one GridResult per grid (most jobs produce exactly one).
    """
    grids: list[GridResult] = []
    for grid_idx, chunk_start in enumerate(range(0, len(crops), MAX_CELLS)):
        chunk = crops[chunk_start: chunk_start + MAX_CELLS]
        grid_result = _compose_single_grid(chunk, grid_idx, chunk_start)
        grids.append(grid_result)
    return grids


def _compose_single_grid(crops: list[dict], grid_index: int, offset: int) -> GridResult:
    canvas = Image.new("RGB", (GRID_SIZE, GRID_SIZE), BACKGROUND_COLOR)
    draw = ImageDraw.Draw(canvas)
    slots: list[CropSlot] = []

    for i, crop in enumerate(crops):
        cell_number = i + 1
        row = i // CELLS_PER_ROW
        col = i % CELLS_PER_ROW
        x0 = col * CELL_SIZE
        y0 = row * CELL_SIZE

        # Decode and resize crop image
        crop_img = _decode_crop(crop["image_base64"])
        crop_img = _fit_to_cell(crop_img, CONTENT_SIZE, CONTENT_SIZE)

        # Paste into canvas with padding
        paste_x = x0 + CELL_PADDING + (CONTENT_SIZE - crop_img.width) // 2
        paste_y = y0 + CELL_PADDING + (CONTENT_SIZE - crop_img.height) // 2
        canvas.paste(crop_img, (paste_x, paste_y))

        # Red cell border
        draw.rectangle(
            [x0 + 1, y0 + 1, x0 + CELL_SIZE - 2, y0 + CELL_SIZE - 2],
            outline=BORDER_COLOR,
            width=BORDER_WIDTH,
        )

        # Cell number label (top-left corner)
        label = str(cell_number)
        lx, ly = x0 + CELL_PADDING, y0 + CELL_PADDING
        # White background behind label for readability
        draw.rectangle([lx - 2, ly - 2, lx + 18, ly + 18], fill=LABEL_BG)
        draw.text((lx, ly), label, fill=LABEL_COLOR)

        slots.append(CropSlot(
            cell_number=cell_number,
            track_id=crop.get("track_id", offset + i),
            original_index=offset + i,
        ))

    # Encode to JPEG
    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=90, optimize=True)
    grid_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

    return GridResult(grid_b64=grid_b64, slots=slots, grid_index=grid_index)


def _decode_crop(b64: str) -> Image.Image:
    """Decode base64 image; handle data-URI prefix."""
    if "," in b64:
        b64 = b64.split(",", 1)[1]
    return Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")


def _fit_to_cell(img: Image.Image, max_w: int, max_h: int) -> Image.Image:
    """Resize image to fit within (max_w, max_h) while preserving aspect ratio."""
    w, h = img.size
    if w <= max_w and h <= max_h:
        return img
    ratio = min(max_w / w, max_h / h)
    new_w = max(1, int(w * ratio))
    new_h = max(1, int(h * ratio))
    return img.resize((new_w, new_h), Image.Resampling.LANCZOS)


def remap_cell_results_to_track_ids(
    cell_results: list[dict],
    slots: list[CropSlot],
) -> list[dict]:
    """
    After model inference returns per-cell results (indexed 1–9),
    re-map each cell's data back to the original track_id.
    """
    cell_map = {slot.cell_number: slot for slot in slots}
    remapped = []
    for item in cell_results:
        cell_num = item.get("cell_number", 0)
        slot = cell_map.get(cell_num)
        if slot:
            item = dict(item)
            item["track_id"] = slot.track_id
            item["original_index"] = slot.original_index
        remapped.append(item)
    return remapped

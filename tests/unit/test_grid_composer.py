"""Unit tests for grid_composer — compose_grids and remap_cell_results_to_track_ids."""

import base64
import io
from PIL import Image

from services.worker.utils.grid_composer import (
    compose_grids,
    remap_cell_results_to_track_ids,
    CropSlot,
    GRID_SIZE,
)


def _make_crop_dict(track_id: int = 0, color: tuple = (200, 100, 50), size: tuple = (512, 512)) -> dict:
    """Return a crop dict with image_base64 and track_id — the format compose_grids expects."""
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return {
        "image_base64": base64.b64encode(buf.getvalue()).decode(),
        "track_id": track_id,
    }


class TestComposeGrids:
    def test_single_crop_produces_one_grid(self):
        crops = [_make_crop_dict(0)]
        grids = compose_grids(crops)
        assert len(grids) == 1

    def test_nine_crops_produce_one_grid(self):
        crops = [_make_crop_dict(i) for i in range(9)]
        grids = compose_grids(crops)
        assert len(grids) == 1

    def test_ten_crops_produce_two_grids(self):
        crops = [_make_crop_dict(i) for i in range(10)]
        grids = compose_grids(crops)
        assert len(grids) == 2

    def test_grid_image_is_correct_size(self):
        crops = [_make_crop_dict(i) for i in range(9)]
        grids = compose_grids(crops)
        img = Image.open(io.BytesIO(base64.b64decode(grids[0].grid_b64)))
        assert img.size == (GRID_SIZE, GRID_SIZE)

    def test_partial_grid_still_correct_size(self):
        crops = [_make_crop_dict(i) for i in range(4)]
        grids = compose_grids(crops)
        img = Image.open(io.BytesIO(base64.b64decode(grids[0].grid_b64)))
        assert img.size == (GRID_SIZE, GRID_SIZE)

    def test_empty_crops_returns_empty_list(self):
        # compose_grids with no input produces no grids
        grids = compose_grids([])
        assert grids == []

    def test_grid_result_has_slots(self):
        crops = [_make_crop_dict(i) for i in range(3)]
        grids = compose_grids(crops)
        assert len(grids[0].slots) == 3
        assert grids[0].slots[0].track_id == 0
        assert grids[0].slots[1].track_id == 1


class TestRemapCellResults:
    def test_single_result_remaps_correctly(self):
        slots = [CropSlot(cell_number=1, track_id=0, original_index=0)]
        cell_results = [{"cell_number": 1, "drug_name": "aspirine"}]
        out = remap_cell_results_to_track_ids(cell_results, slots)
        assert len(out) == 1
        assert out[0]["track_id"] == 0
        assert out[0]["drug_name"] == "aspirine"

    def test_multiple_cells_remap_correctly(self):
        slots = [
            CropSlot(cell_number=1, track_id=10, original_index=0),
            CropSlot(cell_number=2, track_id=20, original_index=1),
        ]
        cell_results = [
            {"cell_number": 1, "drug_name": "A"},
            {"cell_number": 2, "drug_name": "B"},
        ]
        out = remap_cell_results_to_track_ids(cell_results, slots)
        track_ids = {item["track_id"] for item in out}
        assert track_ids == {10, 20}

    def test_unknown_cell_number_preserved_without_track_id(self):
        slots = [CropSlot(cell_number=1, track_id=5, original_index=0)]
        cell_results = [{"cell_number": 99, "drug_name": "unknown"}]
        out = remap_cell_results_to_track_ids(cell_results, slots)
        assert len(out) == 1
        assert "track_id" not in out[0] or out[0].get("cell_number") == 99

    def test_empty_cell_results_returns_empty(self):
        slots = [CropSlot(cell_number=1, track_id=3, original_index=0)]
        out = remap_cell_results_to_track_ids([], slots)
        assert out == []

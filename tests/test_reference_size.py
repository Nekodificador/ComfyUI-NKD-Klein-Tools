"""Additional references must not be stretched to ref_0's shape. Pure python:
run with `python tests/test_reference_size.py`."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from helpers import _reference_encode_size  # noqa: E402

GRID = (1376, 768)          # ref_0's latent grid, in pixels


def test_same_aspect_snaps_to_the_grid_as_before():
    assert _reference_encode_size(2752, 1536, *GRID) == GRID      # bigger, same shape
    assert _reference_encode_size(688, 384, *GRID) == GRID        # smaller, same shape
    assert _reference_encode_size(1400, 768, *GRID) == GRID       # within 2 %


def test_other_aspect_keeps_its_own_shape():
    w, h = _reference_encode_size(1024, 1024, *GRID)              # square ref, wide canvas
    assert abs(w / h - 1.0) < 0.02, (w, h)
    w, h = _reference_encode_size(768, 1376, *GRID)               # portrait ref
    assert abs(w / h - 768 / 1376) / (768 / 1376) < 0.03, (w, h)


def test_other_aspect_respects_the_pixel_budget_and_the_16_grid():
    for ref in ((4000, 4000), (3000, 1000), (800, 1600), (200, 200)):
        w, h = _reference_encode_size(*ref, *GRID)
        assert w % 16 == 0 and h % 16 == 0, (ref, w, h)
        assert w * h <= GRID[0] * GRID[1] * 1.05 or (w, h) == (208, 208), (ref, w, h)


def test_as_reference_without_image_follows_megapixels():
    from helpers import _resolve_resolution
    assert _resolve_resolution("As Reference", 4.0, 1024, 1024) == (2048, 2048)
    assert _resolve_resolution("As Reference", 1.0, 1024, 1024) == (1024, 1024)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)

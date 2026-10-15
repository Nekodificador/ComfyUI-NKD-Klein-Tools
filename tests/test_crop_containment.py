"""The Presampling detailing crop must always contain the mask. Pure torch — run with:
python tests/test_crop_containment.py

Regression: a mask spanning an image width that is not a multiple of 16 lost its first
columns, because the box was shrunk to the /16-snapped image width (same bug as NKD
Inpaint Crop in Basic Tools)."""
import os
import sys

import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from helpers import _bbox_from_mask, _crop_by_mask  # noqa: E402


def _contained(h, w, rect, padding=256, target_pixels=1_048_576):
    mask = torch.zeros(1, h, w)
    x1, y1, x2, y2 = rect
    mask[:, y1:y2, x1:x2] = 1.0
    _crop, _cm, (bx1, by1, bx2, by2), _orig = _crop_by_mask(
        torch.zeros(1, h, w, 3), mask, padding, target_pixels)
    mx1, my1, mx2, my2 = _bbox_from_mask(mask[0])
    assert 0 <= bx1 < bx2 <= w and 0 <= by1 < by2 <= h, (bx1, by1, bx2, by2)
    assert bx1 <= mx1 and by1 <= my1 and bx2 >= mx2 and by2 >= my2, ((bx1, by1, bx2, by2), (mx1, my1, mx2, my2))
    return bx1, by1, bx2, by2


def test_mask_spanning_a_non_multiple_of_16_width():
    box = _contained(1500, 2217, (0, 205, 2217, 1342))
    assert box[0] == 0 and box[2] == 2217, box


def test_mask_spanning_a_non_multiple_of_16_height():
    box = _contained(2217, 1500, (205, 0, 1342, 2217))
    assert box[1] == 0 and box[3] == 2217, box


def test_wide_and_ordinary_masks():
    _contained(1500, 4000, (861, 880, 4000, 1351))
    _contained(600, 800, (400, 120, 520, 240), padding=50)
    _contained(3000, 4000, (1800, 1300, 2300, 1700))


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)

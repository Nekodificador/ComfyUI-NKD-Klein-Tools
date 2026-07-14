"""Self-check for postsampling colour match. Pure numpy —
run with: python tests/test_color_match.py"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from helpers import _composite_with_options


def test_match_colors_moves_edit():
    # Colour match must pull the EDITED (masked) region toward the original's
    # colours. Regression guard: measuring stats on the background is a no-op when
    # gen equals orig outside the mask (the masked-sampling case) — measure the fg.
    rng = np.random.default_rng(0)
    H, W = 128, 128
    orig = rng.random((H, W, 3), dtype=np.float32)
    mask = np.zeros((H, W), dtype=np.float32)
    mask[40:88, 40:88] = 1.0
    gen = orig.copy()
    gen[40:88, 40:88, :] = np.clip(
        orig[40:88, 40:88, :] + np.array([0.25, 0.05, -0.15], np.float32), 0, 1)
    valid = np.ones((H, W), dtype=np.float32)

    out = _composite_with_options(orig, gen, mask, valid,
                                  color_match_strength=1.0, seamless=False)
    fg = mask > 0.5
    moved = np.abs(out[fg] - gen[fg]).mean()
    err_before = np.abs(gen[fg] - orig[fg]).mean()
    err_after = np.abs(out[fg] - orig[fg]).mean()
    assert moved > 0.02, moved                      # not a no-op
    assert err_after < err_before                   # moves toward the original
    assert np.allclose(out[:20, :20], orig[:20, :20], atol=1e-4)  # bg pristine
    print("klein colour match moves edit OK")


if __name__ == "__main__":
    test_match_colors_moves_edit()

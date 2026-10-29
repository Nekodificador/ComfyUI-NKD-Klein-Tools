"""Speck cleanup: tiny detection islands go, the real edit stays."""
import sys, os
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from helpers import _mask_drop_specks


def test_drops_specks_keeps_blob():
    m = np.zeros((200, 200), np.float32)
    m[50:110, 50:110] = 1.0          # real edit: 3600 px = 9% of the image
    m[5, 5] = m[7, 190] = 1.0        # specks: 1 px each
    m[150:153, 150:153] = 1.0        # speck: 9 px
    out = _mask_drop_specks(m, min_area_px=int(200 * 200 * 0.001))  # 40 px
    assert out[50:110, 50:110].all()
    assert out.sum() == 3600


def test_all_specks_keeps_biggest():
    m = np.zeros((100, 100), np.float32)
    m[10:13, 10:13] = 1.0            # 9 px
    m[50, 50] = 1.0                  # 1 px
    out = _mask_drop_specks(m, min_area_px=1000)
    assert out.sum() == 9


def test_zero_is_off():
    m = np.zeros((50, 50), np.float32)
    m[0, 0] = 1.0
    assert _mask_drop_specks(m, 0).sum() == 1


if __name__ == "__main__":
    test_drops_specks_keeps_blob()
    test_all_specks_keeps_biggest()
    test_zero_is_off()
    print("ok")

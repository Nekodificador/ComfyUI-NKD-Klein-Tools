"""Weight -> k/v scale mapping for Klein 3 reference control. Pure python:
run with `python tests/test_flux3_scale.py`.

Calibrated by sweeping the raw scale on Klein 3: structure/pose adherence collapses
between x0.9 and x0.7 and everything below ~x0.65 is the same "reference removed"
plateau; reinforcing helps up to ~x1.10-1.15, then the image saturates and, past ~x1.25,
falls apart."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from helpers import _flux3_kv_scale  # noqa: E402


def test_neutral_and_endpoints():
    assert _flux3_kv_scale(1.0) == 1.0
    assert abs(_flux3_kv_scale(0.0) - 0.65) < 1e-9       # reference removed (the plateau starts here)
    assert abs(_flux3_kv_scale(2.0) - 1.12) < 1e-9       # strongest safe reinforcement


def test_monotonic_and_continuous_at_one():
    ws = [i / 100 for i in range(0, 201)]
    ss = [_flux3_kv_scale(w) for w in ws]
    assert all(b > a for a, b in zip(ss, ss[1:])), "must be strictly increasing"
    assert abs(_flux3_kv_scale(0.9999) - _flux3_kv_scale(1.0001)) < 1e-3


def test_transition_zone_sits_mid_slider():
    # Pose adherence flips around x0.8-0.9: that must be the middle of the 0..1 range.
    assert 0.35 < (0.80 - 0.65) / 0.35 < 0.5
    assert 0.65 < (0.90 - 0.65) / 0.35 < 0.75
    assert abs(_flux3_kv_scale(0.43) - 0.80) < 0.01


def test_clamped_to_the_slider_range():
    assert _flux3_kv_scale(-1.0) == _flux3_kv_scale(0.0)
    assert _flux3_kv_scale(5.0) == _flux3_kv_scale(2.0)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)

"""`convert_box`: excesso de w/h sobre W/H até 1e-6 é recortado; acima disso, recusado (F1-08)."""

from __future__ import annotations

import pytest

from inovalytics.imports.voc_parser import TOL, convert_box


def test_width_excess_within_tolerance_is_clamped() -> None:
    got = convert_box(1, 1, 200 + 5e-7, 100, 200, 100)
    assert got is not None
    (cx, cy, w, h), outside = got
    assert (cx, cy, w, h) == (0.5, 0.5, 1.0, 1.0)
    assert outside is False


def test_height_excess_within_tolerance_is_clamped() -> None:
    got = convert_box(1, 1, 200, 100 + 1e-6, 200, 100)
    assert got is not None and got[0] == (0.5, 0.5, 1.0, 1.0)


def test_shifted_origin_with_excess_within_tolerance_is_clamped_and_outside() -> None:
    got = convert_box(2, 1, 201 + 5e-7, 100, 200, 100)  # x0 = 1, w = 200 + 5e-7
    assert got is not None
    assert got[0][2] == 1.0 and got[1] is True


@pytest.mark.parametrize("box", [(1, 1, 200 + 2e-6, 100), (1, 1, 200, 100 + 2e-6)])
def test_excess_above_tolerance_is_still_refused(box: tuple[float, float, float, float]) -> None:
    assert convert_box(*box, 200, 100) is None


def test_tolerance_is_the_one_used_for_outside() -> None:
    assert TOL == 1e-6

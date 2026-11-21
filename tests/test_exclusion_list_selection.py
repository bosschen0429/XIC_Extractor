import math

import numpy as np
import pytest

from xic_extractor.exclusion_list.background import BackgroundWindow
from xic_extractor.exclusion_list.selection import (
    ListSpec,
    MethodSettings,
    NamedExclusion,
    Target,
    select_exclusion_list,
)

SETTINGS = MethodSettings(
    intensity_threshold=25000.0, inclusion_tolerance_ppm=20.0, max_entries=1000
)
RT_LIST = ListSpec("rt", (100.0, 1000.0), 20.0, rt=True)
WHOLE_RUN_LIST = ListSpec("no_rt", (100.0, 1000.0), 20.0, rt=False)


def window(ion, mz, start, stop, apex=1e6, scans=10):
    return BackgroundWindow(
        ion, mz, start, stop, np.arange(scans), np.full(scans, apex, dtype=float)
    )


def select(spec, named=(), targets=(), windows=(), top_n=None, settings=SETTINGS):
    counts = top_n if top_n is not None else [1] * len(windows)
    return select_exclusion_list(spec, settings, named, targets, windows, counts, 60.0)


@pytest.mark.parametrize(
    ("offset_ppm", "kept"),
    [(39.0, False), (41.0, True)],
)
def test_row_is_kept_only_when_its_exclusion_window_clears_the_inclusion_window(
    offset_ppm, kept
):
    row_mz = 500.0
    target = Target("t", row_mz * (1 + offset_ppm * 1e-6), 0.0, math.inf, "t.csv")

    result = select(RT_LIST, named=[NamedExclusion("A", row_mz)], targets=[target])

    assert [row.mz for row in result.rows] == ([row_mz] if kept else [])


def test_narrower_exclusion_tolerance_narrows_the_required_clearance():
    pbq = ListSpec("pbq", (100.0, 1000.0), 10.0, rt=True)
    target = Target("t", 500.0 * (1 + 35e-6), 0.0, math.inf, "t.csv")

    result = select(pbq, named=[NamedExclusion("A", 500.0)], targets=[target])

    assert len(result.rows) == 1


def test_target_rt_window_cuts_a_timed_row_but_removes_a_whole_run_row():
    target = Target("t", 300.0, 10.0, 14.0, "t.csv")
    ion = window(1, 300.0, 0.0, 60.0)

    timed = select(RT_LIST, targets=[target], windows=[ion])
    whole_run = select(WHOLE_RUN_LIST, targets=[target], windows=[ion])

    assert [(r.start, r.stop) for r in timed.rows] == [(0.0, 10.0), (14.0, 60.0)]
    assert whole_run.rows == ()
    assert len(whole_run.conflicts) == 1


def test_target_outside_the_row_window_leaves_the_row_whole():
    target = Target("t", 300.0, 40.0, 44.0, "t.csv")

    result = select(RT_LIST, targets=[target], windows=[window(1, 300.0, 5.0, 9.0)])

    assert [(r.start, r.stop) for r in result.rows] == [(5.0, 9.0)]
    assert result.conflicts == ()


def test_named_rows_come_first_then_the_most_often_dominant_ions_fill_the_rest():
    settings = MethodSettings(25000.0, 20.0, max_entries=3)
    windows = [
        window(1, 200.0, 1.0, 5.0),
        window(2, 300.0, 1.0, 5.0),
        window(3, 400.0, 1.0, 5.0),
        window(4, 500.0, 1.0, 5.0, apex=20000.0),
        window(5, 1200.0, 1.0, 5.0),
    ]

    result = select(
        RT_LIST,
        named=[NamedExclusion("named one", 600.0), NamedExclusion("low", 50.0)],
        windows=windows,
        top_n=[5, 30, 20, 90, 90],
        settings=settings,
    )

    assert [(r.compound, r.mz) for r in result.rows] == [
        ("EX001_R1_namedone", 600.0),
        ("BG0001_R1", 300.0),
        ("BG0002_R1", 400.0),
    ]
    assert [entry.name for entry in result.named_outside_range] == ["low"]


def test_whole_run_row_replaces_a_timed_row_it_contains():
    windows = [window(1, 300.0, 5.0, 9.0), window(2, 300.001, 0.0, 60.0)]

    result = select(RT_LIST, windows=windows, top_n=[10, 5])

    assert [(r.mz, r.start, r.stop) for r in result.rows] == [(300.001, 0.0, 60.0)]

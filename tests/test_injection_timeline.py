import json
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tools.diagnostics import build_injection_timeline as cli
from xic_extractor.injection_timeline import (
    InjectionHeader,
    PerturbationPolicy,
    ScheduledInjection,
    build_injection_timeline,
    compare_sequence,
)

BASE = datetime(2026, 1, 1, 12)


def header(
    name: str, minute: float, duration: float, *, folder: str = ""
) -> InjectionHeader:
    return InjectionHeader(
        raw_path=f"/batch/{folder}{name}.raw",
        acquisition_source=f"D:/acquired/{name}.raw",
        creation_date=BASE + timedelta(minutes=minute),
        start_time_min=0,
        end_time_min=duration,
        instrument_method=f"{duration}.meth",
    )


def test_copies_do_not_create_extra_injections_or_negative_idle() -> None:
    blank = header("blank", 0, 15)
    sample = header("sample", 17, 60)
    copies = [
        replace(
            blank,
            raw_path="/batch/validation/renamed.raw",
            acquisition_source=r"d:\ACQUIRED\blank.raw",
        ),
        replace(sample, raw_path="/batch/Pairs/sample.raw"),
    ]
    rows = build_injection_timeline([sample, *copies, blank], PerturbationPolicy())
    assert [r.raw_stem for r in rows] == ["blank", "sample"]
    assert [len(r.copy_paths) for r in rows] == [2, 2]
    assert rows[1].idle_min == 2
    assert rows[1].possibly_perturbed
    assert rows[0].idle_min is None
    assert not rows[0].possibly_perturbed
    assert not any(r.renamed_after_acquisition for r in rows)


def test_raw_stem_is_the_local_filename_after_rename() -> None:
    renamed = replace(
        header("TumorBC2302_DNAandRNA", 0, 60),
        raw_path="/batch/TumorBC2304_DNAandRNA.raw",
    )
    nested_copy = replace(renamed, raw_path="/batch/validation/copy.raw")
    [row] = build_injection_timeline([nested_copy, renamed], PerturbationPolicy())
    assert row.raw_stem == "TumorBC2304_DNAandRNA"
    assert row.acquisition_stem == "TumorBC2302_DNAandRNA"
    assert row.renamed_after_acquisition


def test_recursive_discovery_preserves_cross_folder_previous_injection(
    tmp_path: Path,
) -> None:
    (tmp_path / "except sample").mkdir()
    for name in ("sample.raw", "blank.RAW", "except sample/normal.raw", "ignore.txt"):
        (tmp_path / name).touch()
    paths = cli.discover_raw_files(tmp_path)
    assert {p.relative_to(tmp_path).as_posix() for p in paths} == {
        "sample.raw",
        "blank.RAW",
        "except sample/normal.raw",
    }
    rows = build_injection_timeline(
        [
            header("sample", 79, 60),
            header("blank", 0, 15),
            header("normal", 17, 60, folder="except sample/"),
        ],
        PerturbationPolicy(),
    )
    assert rows[-1].previous_raw_stem == "normal"
    assert rows[-1].previous_run_length_min == 60
    assert rows[-1].idle_min == 2
    assert not rows[-1].possibly_perturbed


@pytest.mark.parametrize("idle,expected", [(2.0, True), (12.4, False)])
def test_long_idle_prevents_flag_for_identical_run_lengths(
    idle: float, expected: bool
) -> None:
    rows = build_injection_timeline(
        [header("blank", 0, 15), header("qc", 15 + idle, 60)], PerturbationPolicy()
    )
    assert rows[-1].idle_min == pytest.approx(idle)
    assert rows[-1].possibly_perturbed is expected


def test_policy_controls_short_absolute_and_relative_duration() -> None:
    headers = [header("blank", 0, 25), header("sample", 27, 60)]
    assert not build_injection_timeline(headers, PerturbationPolicy())[
        -1
    ].possibly_perturbed
    assert build_injection_timeline(
        headers, PerturbationPolicy(max_previous_run_min=30)
    )[-1].possibly_perturbed
    assert not build_injection_timeline(
        [header("a", 0, 15), header("b", 17, 20)], PerturbationPolicy()
    )[-1].possibly_perturbed
    assert (
        build_injection_timeline(
            headers, PerturbationPolicy(max_previous_run_min=30, max_idle_min=1)
        )[-1].possibly_perturbed
        is False
    )


def test_clock_offsets_and_true_overlaps_are_not_clamped() -> None:
    first = replace(header("a", 0, 15), start_time_min=1)
    second = replace(header("b", 16, 60), start_time_min=2)
    rows = build_injection_timeline([first, second], PerturbationPolicy())
    assert rows[0].run_length_min == 14
    assert rows[1].acquisition_start == BASE + timedelta(minutes=18)
    assert rows[1].acquisition_end == BASE + timedelta(minutes=76)
    assert rows[1].idle_min == 3
    overlapping = build_injection_timeline(
        [header("a", 0, 15), header("b", 10, 60)], PerturbationPolicy()
    )
    assert overlapping[1].idle_min == -5
    assert overlapping[1].timing_warning == "overlapping_acquisitions"
    assert not overlapping[1].possibly_perturbed


def test_identity_keeps_reinjections_and_same_time_distinct_sources() -> None:
    a = header("a", 0, 15)
    rows = build_injection_timeline(
        [a, replace(a, creation_date=BASE + timedelta(minutes=17)), header("b", 0, 15)],
        PerturbationPolicy(),
    )
    assert len(rows) == 3
    with pytest.raises(ValueError, match="conflicting duplicate"):
        build_injection_timeline([a, replace(a, end_time_min=20)], PerturbationPolicy())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_idle_min": -1},
        {"max_previous_run_min": float("nan")},
        {"max_previous_run_fraction": 1},
    ],
)
def test_invalid_policy_is_rejected(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        PerturbationPolicy(**kwargs)


def test_sequence_preserves_segments_missing_and_ambiguous_rows() -> None:
    a = header("a", 0, 15)
    rows = build_injection_timeline(
        [
            a,
            replace(a, creation_date=BASE + timedelta(minutes=17)),
            header("b", 34, 60),
        ],
        PerturbationPolicy(),
    )
    schedule = [
        ScheduledInjection("segment.sld", i, f"D:/acquired/{name}.raw", "method")
        for i, name in enumerate(["a", "b", "missing"], 1)
    ]
    comparisons = compare_sequence(schedule, rows)
    assert [r.status for r in comparisons] == ["ambiguous", "matched", "missing_raw"]
    assert [r.injection_orders for r in comparisons] == [(1, 2), (3,), ()]
    empty = compare_sequence([ScheduledInjection("segment.sld", 4, "", "")], rows)
    assert empty[0].status == "unconfigured"
    assert empty[0].injection_orders == ()


def test_cli_persists_policy_provenance_and_review_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "blank.raw").touch()
    (tmp_path / "sample.raw").touch()
    monkeypatch.setattr(cli, "preflight_raw_reader", lambda dll: [])
    monkeypatch.setattr(
        cli,
        "read_raw_injection_header",
        lambda path, dll: header(
            path.stem,
            0 if path.stem == "blank" else 17,
            15 if path.stem == "blank" else 60,
        ),
    )
    out = tmp_path / "review.json"
    args = [
        "--raw-root",
        str(tmp_path),
        "--dll-dir",
        str(tmp_path),
        "--output",
        str(out),
    ]
    assert cli.main([*args, "--preflight-only"]) == 0
    assert not out.exists()
    assert cli.main(args) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema"] == "injection_timeline"
    assert data["schema_version"] == 1
    assert data["evidence_status"] == "diagnostic_only"
    assert data["policy"] == {
        "max_previous_run_min": 20,
        "max_previous_run_fraction": 0.5,
        "max_idle_min": 5,
    }
    assert data["injections"][1]["previous_raw_stem"] == "blank"
    assert data["injections"][1]["possibly_perturbed"] is True
    assert data["injections"][1]["header"]["creation_date"] == "2026-01-01T12:17:00"
    assert "sample" in out.with_suffix(".tsv").read_text(encoding="utf-8")

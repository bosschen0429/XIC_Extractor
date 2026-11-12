"""Review curves: saved curves for cells with a value, RAW reads for the rest."""

import json
import sqlite3
import struct
import zlib
from contextlib import contextmanager

import numpy as np
import pytest

from tests.alignment_explanation_fixtures import (
    explanation_discovery_facts,
    run_alignment_fixture,
)
from tests.explanation_review_fixtures import (
    _group_with_missing_apex_review,
    _install_distinct_curve_raws,
)
from xic_extractor.alignment_explanation.errors import ExplanationError
from xic_extractor.alignment_explanation.reader import (
    read_alignment_explanation,
    read_review_curves,
)
from xic_extractor.alignment_explanation.storage import (
    decode_review_curve,
    encode_review_curve,
)
from xic_extractor.applications.untargeted.alignment_explanation_projection import (
    write_alignment_explanation,
)
from xic_extractor.configuration.signal_processing_profile import (
    serialize_signal_processing_profile,
)
from xic_extractor.output.alignment.pipeline_outputs import _write_metadata_json
from xic_workbench.review_curves import (
    CurveAcquisitionCancelled,
    review_curve_bundle,
)


@pytest.mark.parametrize("prior_json", [False, True])
def test_review_curve_codec_keeps_float64_bits(prior_json):
    rt = (0.0, 0.1, float(np.nextafter(0.1, 1.0)), 59.99999999999999)
    raw = (-0.0, 1.23456789012345, 16777217.0, 1000000000.000001)
    # JSON curves are actual persisted inputs from runs before binary storage.
    payload = (
        zlib.compress(json.dumps({"rt": rt, "raw": raw}).encode())
        if prior_json
        else encode_review_curve(rt, raw)
    )
    times, intensities = decode_review_curve(payload)
    assert struct.pack("<8d", *times, *intensities) == struct.pack("<8d", *rt, *raw)


def _full_audit_curves(report):
    """Full-audit raw and processed series by sample and physical peak."""
    with sqlite3.connect(report) as db:
        return {
            (sample, key): json.loads(zlib.decompress(payload))["curves"]
            for sample, key, payload in db.execute(
                "SELECT sample_id, physical_peak_key, payload FROM curves "
                "WHERE physical_peak_key IS NOT NULL"
            )
        }


def test_cells_with_values_show_their_run_curves_without_raw(tmp_path, monkeypatch):
    from tests.cid_nl_raw_fixture import TEST_PROFILE

    completed, candidates, matrix, config = run_alignment_fixture(
        tmp_path, monkeypatch, "positive"
    )
    reports = {}
    for name, full_audit in (("review", False), ("audit", True)):
        folder = tmp_path / name
        folder.mkdir()
        reports[name] = write_alignment_explanation(
            folder / "alignment_explanation.sqlite",
            completed=completed,
            **explanation_discovery_facts(completed),
            peaks_by_sample=candidates,
            matrix_path=matrix,
            alignment_config=config,
            full_audit=full_audit,
        )
    # The run's settings travel beside its report, as Alignment publishes them.
    _write_metadata_json(
        tmp_path / "review" / "alignment_run_metadata.json",
        {
            "signal_processing_profile_json": serialize_signal_processing_profile(
                TEST_PROFILE
            ).decode("utf-8")
        },
    )
    monkeypatch.setattr(
        "xic_workbench.review_curves.open_raw",
        lambda *_args, **_kwargs: pytest.fail("saved curves must not open RAW"),
    )
    detail = read_alignment_explanation(
        reports["review"], candidate_id=candidates["S1"][0].peak_id
    )
    group_id = detail["groups"][0]["feature_id"]
    curves = read_review_curves(reports["review"], feature_id=group_id)
    saved = {loc.sample_key for loc in curves.locations if loc.curve is not None}
    assert saved == {"S1", "S2"}
    assert not curves.needs_raw(saved)

    bundle, unusable = review_curve_bundle(
        reports["review"], curves, key=group_id, sample_ids=saved
    )

    assert unusable == []
    audit = _full_audit_curves(reports["audit"])
    keys = {loc.sample_key: loc.physical_peak_key for loc in curves.locations}
    traces = bundle.transitions[0].traces
    assert {trace.sample_id for trace in traces} == {"S1", "S2"}
    for trace in traces:
        expected = audit[trace.sample_id, keys[trace.sample_id]]
        assert list(trace.raw.rt) == expected["rt"]
        assert list(trace.raw.intensity) == expected["raw"]
        # Processing follows the run's profile, not the default one.
        assert list(trace.gaussian15.intensity) == expected["smoothed"]
        assert list(trace.baseline.intensity) == expected["baseline"]
        assert list(trace.positive_signal.intensity) == expected["positive"]


def test_saved_curves_stay_with_their_own_cell(tmp_path):
    import hashlib

    from tests.alignment_explanation_fixtures import explanation_summary
    from tests.inspector_fixtures import NEUTRAL_LOSS_BY_TAG
    from xic_extractor.alignment_explanation.writer import ExplanationReportWriter

    matrix = tmp_path / "matrix.tsv"
    matrix.write_text("saved matrix", encoding="utf-8")
    report = tmp_path / "review.sqlite"
    samples = [{"sample_key": s, "sample_stem": s} for s in ("S1", "S2", "S3")]
    saved = {
        "S1": ((7.9, 8.0, 8.1), (10.0, 30.0, 10.0)),
        "S2": ((8.0, 8.1, 8.2, 8.3), (5.0, 50.0, 40.0, 5.0)),
    }
    with ExplanationReportWriter(report, review_only=True) as writer:
        writer.declare_samples(samples)
        for sid, apex in (("S1", 8.0), ("S2", 8.1)):
            writer.add_peak(sid, f"P{sid}", (500.0, apex, 5.0))
        writer.add_final_group(
            "G1",
            feature_id="G1",
            disposition="saved",
            members=[],
            center_mz=500.0,
            ppm=5.0,
        )
        writer.set_row_destination("G1", "matrix")
        writer.add_cells(
            "G1",
            [
                {
                    "sample_key": sid,
                    "variant": "numeric" if sid in saved else "unavailable",
                    "value": 100.0 if sid in saved else None,
                    "apex_rt_min": {"S1": 8.0, "S2": 8.1}.get(sid),
                    "observation_ids": [f"P{sid}"] if sid in saved else [],
                }
                for sid in ("S1", "S2", "S3")
            ],
        )
        writer.set_cell_position("G1", "S3", 8.05)
        for sid, (rt, raw) in saved.items():
            writer.set_cell_curve("G1", sid, rt, raw)
        writer.finalize(
            explanation_summary(
                {
                    "samples": samples,
                    "neutral_loss_by_tag": NEUTRAL_LOSS_BY_TAG,
                    "matrix_path": str(matrix),
                    "matrix_sha256": hashlib.sha256(matrix.read_bytes()).hexdigest(),
                },
                review_only=True,
            )
        )

    curves = read_review_curves(report, feature_id="G1")

    assert {loc.sample_key: loc.curve for loc in curves.locations} == {
        **saved,
        "S3": None,
    }
    assert not curves.needs_raw(("S1", "S2"))
    assert curves.needs_raw(("S1", "S3"))


def test_unsaved_samples_read_raw_at_their_recorded_coordinates(tmp_path, monkeypatch):
    # The precursors 499/501 differ from the measured ions, and S3 has a
    # Backfill value without a Discovery candidate.
    expected = {"S1": (499.012, 20.0), "S2": (501.018, 17.0), "S3": (500.123, 23.0)}
    report, _ = _group_with_missing_apex_review(tmp_path, extraction_by_sample=expected)
    acquired = {}

    @contextmanager
    def raw(path, dll, **kwargs):
        class Raw:
            def extract_xic(self, mz, lower, upper, ppm):
                acquired[path.stem] = (mz, ppm)
                rt = np.linspace(lower, upper, 51)
                return rt, 1000 * np.exp(-(((rt - 8) / 0.08) ** 2))

        yield Raw()

    monkeypatch.setattr("xic_workbench.review_curves.open_raw", raw)
    curves = read_review_curves(report, feature_id="G1")
    assert curves.needs_raw(expected)
    bundle, _ = review_curve_bundle(
        report,
        curves,
        key="G1",
        sample_ids=expected,
        raw_dir=tmp_path,
        dll_dir=tmp_path,
    )
    assert acquired == expected
    assert {
        trace.sample_id: (trace.mz, trace.ppm) for trace in bundle.transitions[0].traces
    } == expected

    acquired.clear()
    candidate = read_review_curves(report, candidate_id="C1")
    review_curve_bundle(
        report,
        candidate,
        key="C1",
        sample_ids=("S1",),
        raw_dir=tmp_path,
        dll_dir=tmp_path,
    )
    assert acquired == {"S1": expected["S1"]}


@pytest.mark.parametrize("sample_ids", [("S1", "S2", "S3"), ("S3",)])
def test_empty_cell_reads_the_group_position_and_leaves_results_unchanged(
    tmp_path, monkeypatch, sample_ids
):
    review, matrix = _group_with_missing_apex_review(tmp_path)
    originals = [path.read_bytes() for path in (review, matrix)]
    opened = []
    _install_distinct_curve_raws(monkeypatch, opened)

    bundle, _ = review_curve_bundle(
        review,
        read_review_curves(review, feature_id="G1"),
        key="G1",
        sample_ids=sample_ids,
        raw_dir=tmp_path,
        dll_dir=tmp_path,
    )

    traces = {trace.sample_id: trace for trace in bundle.transitions[0].traces}
    assert set(traces) == set(sample_ids)
    assert opened == [f"{sid}.raw" for sid in sample_ids]
    fallback = traces["S3"]
    assert fallback.mz == 500.0
    assert fallback.extraction_rt_window == (7.5, 8.5)
    assert fallback.quality_flags["acquired_at_group_position"] is True
    assert max(fallback.raw.intensity) == pytest.approx(2000)
    if "S1" in traces:
        assert traces["S1"].mz == 499.0
        assert traces["S1"].extraction_rt_window == (7.4, 8.4)
        assert max(traces["S1"].raw.intensity) == pytest.approx(1000)
        assert traces["S2"].mz == 501.0
        assert traces["S2"].extraction_rt_window == (7.6, 8.6)
        assert not traces["S2"].quality_flags["acquired_at_group_position"]
    assert all(trace.integration is None for trace in traces.values())
    assert [path.read_bytes() for path in (review, matrix)] == originals


def test_raw_read_leaves_out_a_sample_without_signal(tmp_path, monkeypatch):
    review, _ = _group_with_missing_apex_review(tmp_path)
    curves = read_review_curves(review, feature_id="G1")
    _install_distinct_curve_raws(monkeypatch, [], empty={"S1"})
    bundle, unusable = review_curve_bundle(
        review,
        curves,
        key="G1",
        sample_ids=("S1", "S2"),
        raw_dir=tmp_path,
        dll_dir=tmp_path,
    )
    assert unusable == ["S1"]
    assert [t.sample_label for t in bundle.transitions[0].traces] == ["S2"]

    _install_distinct_curve_raws(monkeypatch, [], empty={"S1", "S2"})
    with pytest.raises(ExplanationError, match="都沒有可用訊號"):
        review_curve_bundle(
            review,
            curves,
            key="G1",
            sample_ids=("S1", "S2"),
            raw_dir=tmp_path,
            dll_dir=tmp_path,
        )


def test_raw_read_stops_when_cancelled(tmp_path, monkeypatch):
    review, _ = _group_with_missing_apex_review(tmp_path)
    curves = read_review_curves(review, feature_id="G1")
    monkeypatch.setattr(
        "xic_workbench.review_curves.open_raw",
        lambda *_args, **_kwargs: pytest.fail("a cancelled read opens no RAW"),
    )
    with pytest.raises(CurveAcquisitionCancelled):
        review_curve_bundle(
            review,
            curves,
            key="G1",
            sample_ids=("S1", "S2"),
            raw_dir=tmp_path,
            dll_dir=tmp_path,
            is_cancelled=lambda: True,
        )

    cancelled, opened = {"value": False}, []
    _install_distinct_curve_raws(monkeypatch, opened, cancelled)
    with pytest.raises(CurveAcquisitionCancelled):
        review_curve_bundle(
            review,
            curves,
            key="G1",
            sample_ids=("S1", "S2"),
            raw_dir=tmp_path,
            dll_dir=tmp_path,
            is_cancelled=lambda: cancelled["value"],
        )
    assert opened == ["S1.raw"]


@pytest.mark.parametrize(
    "saved", (None, "easy_ic_probe", "easy_ic_restore", "negative")
)
def test_raw_reads_replay_each_samples_saved_mass_result(tmp_path, monkeypatch, saved):
    from xic_extractor.configuration.mass_recalibration import (
        MassRecalibration,
        MassRecalibrationResult,
        serialize_mass_recalibration_result,
    )

    review, _ = _group_with_missing_apex_review(tmp_path)
    policies = {
        "S1": "easy_ic_restore" if saved == "negative" else saved or "none",
        "S2": "none",
    }
    saved_outcome = "not_applicable" if saved == "negative" else "unavailable"
    _write_metadata_json(
        tmp_path / "alignment_run_metadata.json",
        {
            "mass_recalibration_by_sample": json.dumps(
                {
                    s: json.loads(
                        serialize_mass_recalibration_result(
                            MassRecalibrationResult(
                                saved_outcome if s == "S1" else "none",
                                policy=MassRecalibration(p),
                            )
                        )
                    )
                    for s, p in policies.items()
                }
            )
        }
        if saved
        else {},
    )
    replayed = {}

    @contextmanager
    def raw(path, dll, *, mass_recalibration=MassRecalibration.NONE, **kwargs):
        class Raw:
            def use_mass_recalibration_result(self, result):
                replayed[path.stem] = result.outcome

            def extract_xic(self, mz, lower, upper, ppm):
                rt = np.linspace(lower, upper, 51)
                amplitude = 1000 if path.stem == "S1" else 2000
                return rt, amplitude * (
                    mass_recalibration.value == policies[path.stem]
                ) * np.exp(-(((rt - 8) / 0.08) ** 2))

        yield Raw()

    monkeypatch.setattr("xic_workbench.review_curves.open_raw", raw)
    bundle, _ = review_curve_bundle(
        review,
        read_review_curves(review, feature_id="G1"),
        key="G1",
        sample_ids=("S1", "S2"),
        raw_dir=tmp_path,
        dll_dir=tmp_path,
    )
    traces = {t.sample_label: t for t in bundle.transitions[0].traces}
    assert max(traces["S1"].raw.intensity) == pytest.approx(1000)
    assert max(traces["S2"].raw.intensity) == pytest.approx(2000)
    # A saved result is replayed; the RAW is not probed again.
    assert replayed == ({"S1": saved_outcome, "S2": "none"} if saved else {})

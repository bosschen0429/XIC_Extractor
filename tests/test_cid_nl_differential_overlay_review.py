from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.diagnostics import cid_nl_differential_overlay_review as review
from xic_extractor.peak_detection.ms1_shape_identity import gaussian_smooth_values
from xic_extractor.tabular_io import read_tsv_required, write_tsv
from xic_extractor.xic_models import XICTrace
from xic_inspector.api import SelectionRequest, generate_report
from xic_inspector.sources.curve_bundle import read_curve_bundle


def test_build_transition_reviews_filters_and_prioritizes_ready_rows(
    tmp_path: Path,
) -> None:
    differential_tsv, decisions_tsv = _write_inputs(tmp_path)

    transitions = review.build_transition_reviews(
        differential_review_tsv=differential_tsv,
        decisions_tsv=decisions_tsv,
    )

    assert [item.transition_key for item in transitions] == (
        ["FAM000003->FAM000004", "FAM000001->FAM000002"]
    )
    assert transitions[0].rank == 1
    assert transitions[0].write_authorized_count == 3
    assert transitions[0].sample_decisions[0].sample_stem == "SampleC"
    assert transitions[1].sample_decisions[0].successor_decision == ("write_authorized")
    assert transitions[1].sample_decisions[1].successor_decision == (
        "no_write_detected_baseline_preserved"
    )


def test_extract_transition_trace_pairs_batches_source_and_successor_by_sample(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    differential_tsv, decisions_tsv = _write_inputs(tmp_path)
    transitions = review.build_transition_reviews(
        differential_review_tsv=differential_tsv,
        decisions_tsv=decisions_tsv,
        limit=1,
    )
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "SampleC.raw").write_text("fixture", encoding="utf-8")
    opened: list[tuple[str, int]] = []

    class FakeRaw:
        def __init__(self, raw_path: Path) -> None:
            self.raw_path = raw_path

        def __enter__(self) -> "FakeRaw":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def extract_xic_many(self, requests: object) -> tuple[XICTrace, ...]:
            request_tuple = tuple(requests)
            opened.append((self.raw_path.name, len(request_tuple)))
            return tuple(
                XICTrace.from_arrays(
                    [request.rt_min, request.rt_max],
                    [request.mz, request.mz * 2],
                )
                for request in request_tuple
            )

    def fake_open_raw(raw_path: Path, _dll_dir: Path) -> FakeRaw:
        return FakeRaw(raw_path)

    monkeypatch.setattr("xic_extractor.raw_reader.open_raw", fake_open_raw)

    pairs_by_transition = review.extract_transition_trace_pairs(
        transitions,
        raw_dir=raw_dir,
        dll_dir=tmp_path / "dll",
        rt_half_window_min=1.5,
        ppm=10,
    )

    assert opened == [("SampleC.raw", 2)]
    pair = pairs_by_transition["FAM000003->FAM000004"][0]
    assert pair.sample_stem == "SampleC"
    assert pair.missing_raw is False
    assert pair.source_trace.intensity.tolist() == [301.0, 602.0]
    assert pair.successor_trace.intensity.tolist() == [302.0, 604.0]


def test_write_differential_evidence_remains_human_review_only(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    differential_tsv, decisions_tsv = _write_inputs(tmp_path)
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "SampleC.raw").write_text("fixture", encoding="utf-8")

    class FakeRaw:
        def __enter__(self) -> "FakeRaw":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def extract_xic_many(self, requests: object) -> tuple[XICTrace, ...]:
            traces = []
            for request in tuple(requests):
                center = (request.rt_min + request.rt_max) / 2
                traces.append(
                    XICTrace.from_arrays(
                        [request.rt_min, center, request.rt_max],
                        [10.0, request.mz * 100.0, 5.0],
                    )
                )
            return tuple(traces)

    monkeypatch.setattr(
        "xic_extractor.raw_reader.open_raw",
        lambda _raw_path, _dll_dir: FakeRaw(),
    )

    payload = review.write_differential_evidence(
        differential_review_tsv=differential_tsv,
        decisions_tsv=decisions_tsv,
        output_dir=tmp_path / "review",
        raw_dir=raw_dir,
        dll_dir=tmp_path / "dll",
        limit=1,
        require_pass=True,
    )

    assert payload["overall_status"] == "pass"
    assert payload["transition_count"] == 1
    assert payload["product_writer_changed"] is False
    assert payload["default_quant_matrix_changed"] is False
    assert payload["candidate_rows_are_matrix_rows"] is False
    summary = json.loads(
        (
            tmp_path / "review" / "cid_nl_differential_overlay_review_summary.json"
        ).read_text(encoding="utf-8")
    )
    assert summary["schema_version"] == review.SCHEMA_VERSION
    assert summary["validation_label"] == "diagnostic_only"
    assert summary["overall_status"] == "pass"
    assert summary["review_frame"] == "feature_inclusion_then_identity_authority"
    assert summary["source_successor_not_mutually_exclusive"] is True
    assert "human-review evidence only" in summary["authority_statement"]
    queue_rows = read_tsv_required(
        Path(payload["queue_tsv"]),
        review.QUEUE_COLUMNS,
    )
    assert [row["transition_key"] for row in queue_rows] == ["FAM000003->FAM000004"]
    summary_rows = read_tsv_required(
        Path(payload["summary_tsv"]),
        review.SUMMARY_COLUMNS,
    )
    assert len(summary_rows) == 1
    assert summary_rows[0]["status"] == "success"
    assert summary_rows[0]["visual_successor_ms1_state"] == (
        "successor_ms1_trace_present"
    )
    review_path = Path(summary_rows[0]["review_path"])
    trace_json = Path(summary_rows[0]["trace_data_json"])
    assert review_path.is_file()
    assert not list((tmp_path / "review").glob("*.png"))
    bundle = read_curve_bundle(review_path)
    transition = bundle.transitions[0]
    assert transition.key == "FAM000003->FAM000004"
    assert transition.quality_flags["feature_inclusion_gate"] == (
        "candidate_ms1_feature_inclusion_supported"
    )
    assert transition.quality_flags["write_authorized_count"] == 3
    assert transition.quality_flags["median_gaussian15_successor_source_max_ratio"]
    source = next(trace for trace in transition.traces if trace.role == "source")
    assert source.quality_flags["successor_decision"] == "write_authorized"
    assert source.raw is not None and source.gaussian15 is not None
    assert source.gaussian15.rt == source.raw.rt
    assert source.gaussian15.intensity == gaussian_smooth_values(
        source.raw.intensity, points=15
    )
    inspector_html = tmp_path / "inspector.html"
    generate_report(review_path, SelectionRequest(), inspector_html)
    html = inspector_html.read_text(encoding="utf-8")
    assert "differentialPlot" in html
    assert "differentialScatter" in html
    embedded = html.split('<script id="inspector-data" type="application/json">', 1)[1]
    inspector_data = json.loads(embedded.split("</script>", 1)[0])
    item = inspector_data["items"][0]
    assert item["transition_review"]["review_kind"] == "cid_nl_differential"
    assert item["transition_review"]["source_center_rt"] == 23.1
    assert item["transition_review"]["successor_center_rt"] == 23.8
    projected_source = next(
        curve for curve in item["curves"] if curve["kind"] == "source"
    )
    assert projected_source["processed_curves"][0]["intensities"] == list(
        source.gaussian15.intensity
    )
    trace_payload = json.loads(trace_json.read_text(encoding="utf-8"))
    assert trace_payload["transition_key"] == "FAM000003->FAM000004"
    assert trace_payload["traces"][0]["sample_stem"] == "SampleC"


def test_trace_max_uses_gaussian15_smoothing() -> None:
    trace = XICTrace.from_arrays(
        [0.0, 0.1, 0.2, 0.3, 0.4],
        [0.0, 0.0, 100.0, 0.0, 0.0],
    )

    assert review._raw_trace_max(trace) == 100.0
    assert 0 < review._trace_max(trace) < 100.0


def test_missing_raw_is_explicit_in_bundle(tmp_path: Path) -> None:
    differential_tsv, decisions_tsv = _write_inputs(tmp_path)
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    payload = review.write_differential_evidence(
        differential_review_tsv=differential_tsv,
        decisions_tsv=decisions_tsv,
        output_dir=tmp_path / "review",
        raw_dir=raw_dir,
        dll_dir=tmp_path / "dll",
        limit=1,
    )
    assert payload["overall_status"] == "failed"
    row = read_tsv_required(Path(payload["summary_tsv"]), review.SUMMARY_COLUMNS)[0]
    bundle = read_curve_bundle(Path(row["review_path"]))
    assert all(
        trace.raw is None and trace.gaussian15 is None
        for trace in bundle.transitions[0].traces
    )
    assert {trace.unavailable_reason for trace in bundle.transitions[0].traces} == {
        "missing_raw"
    }


def _write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    differential_tsv = tmp_path / "differential.tsv"
    decisions_tsv = tmp_path / "decisions.tsv"
    write_tsv(
        differential_tsv,
        [
            _differential_row(
                "FAM000001",
                "FAM000002",
                sample_count=2,
                write_count=1,
                preserve_count=1,
                source_mz=300.0,
                successor_mz=300.5,
                source_rt=22.1,
                successor_rt=22.5,
            ),
            _differential_row(
                "FAM000003",
                "FAM000004",
                sample_count=3,
                write_count=3,
                preserve_count=0,
                source_mz=301.0,
                successor_mz=302.0,
                source_rt=23.1,
                successor_rt=23.8,
            ),
            _differential_row(
                "FAM000005",
                "<none>",
                sample_count=1,
                write_count=0,
                preserve_count=0,
                source_mz=303.0,
                successor_mz=0.0,
                source_rt=24.1,
                successor_rt=0.0,
                readiness="no_successor_target",
            ),
        ],
        review.DIFFERENTIAL_COLUMNS,
    )
    write_tsv(
        decisions_tsv,
        [
            _decision_row(
                "FAM000001",
                "FAM000002",
                "SampleB",
                "no_write_detected_baseline_preserved",
            ),
            _decision_row(
                "FAM000001",
                "FAM000002",
                "SampleA",
                "write_authorized",
            ),
            _decision_row(
                "FAM000003",
                "FAM000004",
                "SampleC",
                "write_authorized",
            ),
            _decision_row("FAM000005", "", "SampleD", "no_write_omitted"),
        ],
        review.DECISION_COLUMNS,
    )
    return differential_tsv, decisions_tsv


def _differential_row(
    source: str,
    successor: str,
    *,
    sample_count: int,
    write_count: int,
    preserve_count: int,
    source_mz: float,
    successor_mz: float,
    source_rt: float,
    successor_rt: float,
    readiness: str = "ready_for_paired_overlay",
) -> dict[str, object]:
    return {
        "source_peak_hypothesis_id": source,
        "successor_peak_hypothesis_id": successor,
        "transition_key": f"{source}->{successor}",
        "sample_count": sample_count,
        "write_authorized_count": write_count,
        "no_write_detected_baseline_preserved_count": preserve_count,
        "no_write_omitted_count": 0,
        "source_mz": source_mz,
        "source_rt": source_rt,
        "source_product_mz": "184.113",
        "source_neutral_loss_tag": "DNA_dR",
        "source_identity_decision": "audit_family",
        "source_accepted_cell_count": 0,
        "successor_mz": successor_mz,
        "successor_rt": successor_rt,
        "successor_product_mz": "184.113",
        "successor_neutral_loss_tag": "DNA_dR",
        "successor_identity_decision": "production_family",
        "successor_accepted_cell_count": sample_count,
        "mz_delta": successor_mz - source_mz,
        "rt_delta": successor_rt - source_rt,
        "feature_inclusion_gate": "candidate_ms1_feature_inclusion_supported"
        if successor != "<none>"
        else "not_assessable_no_successor_feature",
        "identity_authority_gate": "replacement_merge_dedupe_requires_expected_diff"
        if successor != "<none>"
        else "no_replacement_target",
        "source_successor_relationship": (
            "source_and_successor_not_mutually_exclusive"
            if successor != "<none>"
            else "old_identity_has_no_successor"
        ),
        "transition_type": "old_to_successor",
        "differential_overlay_readiness": readiness,
        "review_note": "fixture",
    }


def _decision_row(
    source: str,
    successor: str,
    sample: str,
    decision: str,
) -> dict[str, object]:
    return {
        "old_peak_hypothesis_id": source,
        "sample_stem": sample,
        "successor_peak_hypothesis_id": successor,
        "successor_decision": decision,
        "write_authority": decision == "write_authorized",
        "matrix_write_allowed": decision == "write_authorized",
        "matrix_effect": "candidate_write"
        if decision == "write_authorized"
        else "baseline_preserved",
        "human_explanation": "fixture explanation",
        "input_resolution_status": "resolved",
        "candidate_new_peak_hypothesis_ids": successor,
        "candidate_baseline_values": "",
        "accepted_quant_value": "123.4" if decision == "write_authorized" else "",
    }

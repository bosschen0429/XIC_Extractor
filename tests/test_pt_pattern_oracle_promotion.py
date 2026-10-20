from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.pt_pattern_trace_fixtures import write_overlay_lineage_bundle
from xic_extractor.discovery import pt_pattern_blind_overlay as overlay
from xic_extractor.discovery import pt_pattern_oracle_promotion as promotion
from xic_extractor.discovery import pt_pattern_review_packet as packet
from xic_extractor.discovery import pt_pattern_trace_evidence as evidence
from xic_extractor.tabular_io import (
    file_sha256,
    read_delimited_rows,
    write_delimited_rows,
)

UNIT_REVIEW_SHA256 = "A" * 64
UNIT_OVERLAY_SUMMARY_SHA256 = "B" * 64


def _review_packet_row(candidate_id: str, *, status: str) -> dict[str, str]:
    row = {column: "" for column in packet.PACKET_COLUMNS}
    row.update(
        {
            "schema_version": packet.SCHEMA_VERSION,
            "readiness_tier": "diagnostic_only",
            "evidence_owner": packet.EVIDENCE_OWNER,
            "packet_label": "unit_packet",
            "source_label": "pt_only",
            "packet_row_id": f"pt_only|{candidate_id}",
            "sample_stem": "SampleA",
            "candidate_id": candidate_id,
            "candidate_assignment_sha256": "a" * 64,
            "trace_extraction_sha256": "b" * 64,
            "trace_artifact_role": "anchor_local",
            "base_mz": "500.0",
            "base_rt_apex_min": "10.0",
            "trace_decision_basis": evidence.TRACE_DECISION_BASIS,
            "trace_smoothing_method": evidence.TRACE_SMOOTH_METHOD,
            "trace_smoothing_window_points": str(evidence.TRACE_SMOOTH_WINDOW_POINTS),
            "combined_pt_pattern_status": status,
            "non_base_positions_supported": "3",
            "max_abs_apex_delta_sec": "0.5",
            "min_pearson_r_to_base": "0.95",
            "min_active_overlap_fraction": "0.90",
            "trace_ambiguity_status": "clear",
            "anchor_segmentation_status": "clear",
            "anchor_segmentation_boundary_review_status": "clear",
            "oracle_status": "pending_human_review",
        }
    )
    return row


def _adjudicated_row(
    candidate_id: str,
    *,
    adjudicated_call: str = "accept",
    user_call: str = "accept",
    machine_call: str = "accept",
    machine_call_reason: str = "full_pattern_close_apex_shape_overlap",
) -> dict[str, str]:
    return {
        "dataset": "pt_only_fullscan",
        "case_index": "1",
        "adjudicated_call": adjudicated_call,
        "adjudication_basis": "unit_adjudication",
        "user_call": user_call,
        "machine_call": machine_call,
        "note_optional": "unit note",
        "machine_call_reason": machine_call_reason,
        "base_mz": "500.0",
        "base_rt_apex_min": "10.0",
        "candidate_id": candidate_id,
        "packet_row_id": f"pt_only|{candidate_id}",
        "review_packet_csv_sha256": UNIT_REVIEW_SHA256,
        "blind_overlay_summary_json_sha256": UNIT_OVERLAY_SUMMARY_SHA256,
    }


def _trace_points(candidate_ids: list[str]) -> list[dict[str, str]]:
    return [
        {
            "candidate_id": candidate_id,
            "position_label": position_label,
            "rt_min": "10.0",
            "intensity": "100.0",
        }
        for candidate_id in candidate_ids
        for position_label in evidence.POSITION_LABELS
    ]


def _write_review_bundle(
    tmp_path: Path,
    review_rows: list[dict[str, str]],
) -> tuple[Path, Path]:
    review_packet_csv = tmp_path / "review_packet.csv"
    trace_points_csv = tmp_path / "trace_points.csv"
    write_overlay_lineage_bundle(
        review_packet_csv=review_packet_csv,
        packet_rows=review_rows,
        trace_points_csv=trace_points_csv,
        trace_point_rows=_trace_points([row["candidate_id"] for row in review_rows]),
    )
    outputs = overlay.run_blind_overlay(
        review_packet_csv=review_packet_csv,
        trace_points_csv=trace_points_csv,
        output_dir=tmp_path / "overlay",
        title="Promotion evidence bundle",
    )
    return review_packet_csv, outputs["summary_json"]


def _sealed_machine_rows(overlay_summary_json: Path) -> list[dict[str, str]]:
    summary = json.loads(overlay_summary_json.read_text(encoding="utf-8"))
    return read_delimited_rows(
        Path(summary["outputs"]["machine_calls_csv"]),
        required_columns=overlay.MACHINE_CALL_COLUMNS,
        delimiter=",",
    )


def _rewrite_sealed_machine_rows(
    overlay_summary_json: Path,
    rows: list[dict[str, str]],
) -> None:
    summary = json.loads(overlay_summary_json.read_text(encoding="utf-8"))
    machine_calls_csv = Path(summary["outputs"]["machine_calls_csv"])
    write_delimited_rows(machine_calls_csv, rows, overlay.MACHINE_CALL_COLUMNS)
    summary["outputs"]["machine_calls_csv_sha256"] = file_sha256(machine_calls_csv)
    overlay_summary_json.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _bind_rows_to_bundle(
    rows: list[dict[str, str]],
    *,
    review_packet_csv: Path,
    overlay_summary_json: Path,
) -> list[dict[str, str]]:
    machine_rows = _sealed_machine_rows(overlay_summary_json)
    machine_by_packet_row_id = {row["packet_row_id"]: row for row in machine_rows}
    return [
        dict(row)
        | {
            "machine_call": machine_by_packet_row_id[row["packet_row_id"]][
                "machine_call"
            ],
            "machine_call_reason": machine_by_packet_row_id[row["packet_row_id"]][
                "machine_call_reason"
            ],
            "review_packet_csv_sha256": file_sha256(review_packet_csv),
            "blind_overlay_summary_json_sha256": file_sha256(overlay_summary_json),
        }
        for row in rows
    ]


def _build_rows(
    *,
    adjudicated_rows: list[dict[str, str]],
    review_packet_rows: list[dict[str, str]],
) -> tuple[dict[str, object], ...]:
    sealed_machine_rows = {
        row["packet_row_id"]: {
            key: str(value) for key, value in overlay.machine_call_row(row).items()
        }
        for row in review_packet_rows
    }
    return promotion.build_oracle_promotion_rows(
        adjudicated_rows=adjudicated_rows,
        review_packet_rows=review_packet_rows,
        review_hash_by_packet_row_id={
            row["packet_row_id"]: UNIT_REVIEW_SHA256 for row in review_packet_rows
        },
        overlay_bundles={
            UNIT_OVERLAY_SUMMARY_SHA256: promotion.BlindOverlayEvidenceBundle(
                summary_json_sha256=UNIT_OVERLAY_SUMMARY_SHA256,
                review_packet_csv_sha256=UNIT_REVIEW_SHA256,
                sealed_machine_rows_by_packet_row_id=sealed_machine_rows,
            )
        },
        oracle_packet_id="unit_oracle",
    )


def _write_two_row_promotion_bundle(tmp_path: Path) -> dict[str, Path]:
    review_packet_csv, overlay_summary_json = _write_review_bundle(
        tmp_path,
        [
            _review_packet_row(
                "cand-a",
                status="pt_pattern_detected_diagnostic",
            ),
            _review_packet_row(
                "cand-b",
                status="pt_pattern_candidate_incomplete",
            ),
        ],
    )
    adjudicated_csv = tmp_path / "adjudicated.csv"
    write_delimited_rows(
        adjudicated_csv,
        _bind_rows_to_bundle(
            [
                _adjudicated_row("cand-a"),
                _adjudicated_row(
                    "cand-b",
                    adjudicated_call="reject",
                    user_call="reject",
                ),
            ],
            review_packet_csv=review_packet_csv,
            overlay_summary_json=overlay_summary_json,
        ),
        promotion.ADJUDICATED_REQUIRED_COLUMNS,
    )
    return promotion.run_oracle_promotion(
        adjudicated_oracle_csv=adjudicated_csv,
        review_packet_csvs=[review_packet_csv],
        blind_overlay_summary_jsons=[overlay_summary_json],
        output_dir=tmp_path / "promotion",
        oracle_packet_id="unit_oracle",
    )


def _refresh_promotion_packet_summary(outputs: dict[str, Path]) -> None:
    packet_rows = read_delimited_rows(
        outputs["promotion_packet_csv"],
        required_columns=promotion.PROMOTION_COLUMNS,
        exact_columns=promotion.PROMOTION_COLUMNS,
        delimiter=",",
    )
    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    summary["outputs"]["promotion_packet_csv_sha256"] = file_sha256(
        outputs["promotion_packet_csv"]
    )
    summary["packet_row_count"] = len(packet_rows)
    summary["oracle_call_counts"] = {
        call: sum(row["oracle_call"] == call for row in packet_rows)
        for call in sorted({row["oracle_call"] for row in packet_rows})
    }
    summary["oracle_detection_status_counts"] = {
        status: sum(row["oracle_detection_status"] == status for row in packet_rows)
        for status in sorted({row["oracle_detection_status"] for row in packet_rows})
    }
    outputs["summary_json"].write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_oracle_promotion_allows_resolved_subset_of_review_packet() -> None:
    review_rows = [
        _review_packet_row(
            "cand-resolved",
            status="pt_pattern_detected_diagnostic",
        ),
        _review_packet_row(
            "cand-still-uncertain",
            status="pt_pattern_candidate_review",
        ),
    ]

    rows = _build_rows(
        adjudicated_rows=[_adjudicated_row("cand-resolved")],
        review_packet_rows=review_rows,
    )

    assert [row["candidate_id"] for row in rows] == ["cand-resolved"]


def test_oracle_promotion_normalizes_human_call_in_output() -> None:
    rows = _build_rows(
        adjudicated_rows=[
            _adjudicated_row(
                "cand-normalized",
                adjudicated_call=" Accept ",
                user_call=" ACCEPT ",
            )
        ],
        review_packet_rows=[
            _review_packet_row(
                "cand-normalized",
                status="pt_pattern_detected_diagnostic",
            )
        ],
    )

    assert rows[0]["oracle_call"] == "accept"
    assert rows[0]["human_review_call"] == "accept"


def test_oracle_promotion_rejects_unused_review_packet_source(
    tmp_path: Path,
) -> None:
    primary_dir = tmp_path / "primary"
    extra_dir = tmp_path / "extra"
    primary_dir.mkdir()
    extra_dir.mkdir()
    review_packet_csv, overlay_summary_json = _write_review_bundle(
        primary_dir,
        [
            _review_packet_row(
                "cand-primary",
                status="pt_pattern_detected_diagnostic",
            )
        ],
    )
    extra_review_packet_csv, extra_overlay_summary_json = _write_review_bundle(
        extra_dir,
        [
            _review_packet_row(
                "cand-extra",
                status="pt_pattern_candidate_incomplete",
            )
        ],
    )
    adjudicated_csv = tmp_path / "adjudicated.csv"
    write_delimited_rows(
        adjudicated_csv,
        _bind_rows_to_bundle(
            [_adjudicated_row("cand-primary")],
            review_packet_csv=review_packet_csv,
            overlay_summary_json=overlay_summary_json,
        ),
        promotion.ADJUDICATED_REQUIRED_COLUMNS,
    )

    with pytest.raises(ValueError, match="review packet source set.*extra"):
        promotion.run_oracle_promotion(
            adjudicated_oracle_csv=adjudicated_csv,
            review_packet_csvs=[review_packet_csv, extra_review_packet_csv],
            blind_overlay_summary_jsons=[
                overlay_summary_json,
                extra_overlay_summary_json,
            ],
            output_dir=tmp_path / "promotion",
            oracle_packet_id="unit_oracle",
        )


def test_oracle_promotion_rejects_unused_blind_overlay_source(
    tmp_path: Path,
) -> None:
    review_packet_csv, overlay_summary_json = _write_review_bundle(
        tmp_path,
        [
            _review_packet_row(
                "cand-primary",
                status="pt_pattern_detected_diagnostic",
            )
        ],
    )
    extra_overlay_summary_json = overlay.run_blind_overlay(
        review_packet_csv=review_packet_csv,
        trace_points_csv=tmp_path / "trace_points.csv",
        output_dir=tmp_path / "extra_overlay",
        title="Unused promotion evidence bundle",
    )["summary_json"]
    adjudicated_csv = tmp_path / "adjudicated.csv"
    write_delimited_rows(
        adjudicated_csv,
        _bind_rows_to_bundle(
            [_adjudicated_row("cand-primary")],
            review_packet_csv=review_packet_csv,
            overlay_summary_json=overlay_summary_json,
        ),
        promotion.ADJUDICATED_REQUIRED_COLUMNS,
    )

    with pytest.raises(ValueError, match="blind-overlay source set.*extra"):
        promotion.run_oracle_promotion(
            adjudicated_oracle_csv=adjudicated_csv,
            review_packet_csvs=[review_packet_csv],
            blind_overlay_summary_jsons=[
                overlay_summary_json,
                extra_overlay_summary_json,
            ],
            output_dir=tmp_path / "promotion",
            oracle_packet_id="unit_oracle",
        )


def test_oracle_promotion_writes_provenance_locked_packet(tmp_path: Path) -> None:
    adjudicated_csv = tmp_path / "adjudicated.csv"
    adjudication_summary_json = tmp_path / "adjudicated_summary.json"
    review_rows = [
        _review_packet_row(
            "cand-accept",
            status="pt_pattern_detected_diagnostic",
        ),
        _review_packet_row(
            "cand-reject",
            status="pt_pattern_candidate_incomplete",
        ),
    ]
    review_packet_csv, overlay_summary_json = _write_review_bundle(
        tmp_path,
        review_rows,
    )
    write_delimited_rows(
        adjudicated_csv,
        _bind_rows_to_bundle(
            [
                _adjudicated_row("cand-accept"),
                _adjudicated_row(
                    "cand-reject",
                    adjudicated_call="reject",
                    user_call="reject",
                    machine_call="reject",
                ),
            ],
            review_packet_csv=review_packet_csv,
            overlay_summary_json=overlay_summary_json,
        ),
        promotion.ADJUDICATED_REQUIRED_COLUMNS,
    )
    adjudication_summary_json.write_text(
        json.dumps(
            {
                "total_rows": 2,
                "accept_count": 1,
                "reject_count": 1,
                "uncertain_count": 0,
            }
        ),
        encoding="utf-8",
    )

    outputs = promotion.run_oracle_promotion(
        adjudicated_oracle_csv=adjudicated_csv,
        review_packet_csvs=[review_packet_csv],
        blind_overlay_summary_jsons=[overlay_summary_json],
        output_dir=tmp_path / "promotion",
        oracle_packet_id="unit_oracle",
        adjudication_summary_json=adjudication_summary_json,
    )

    rows = read_delimited_rows(
        outputs["promotion_packet_csv"],
        required_columns=promotion.PROMOTION_COLUMNS,
        delimiter=",",
    )
    assert [row["oracle_call"] for row in rows] == ["accept", "reject"]
    assert [row["oracle_detection_status"] for row in rows] == [
        "not_asserted",
        "not_asserted",
    ]
    assert rows[0]["readiness_tier"] == "diagnostic_only"
    assert rows[0]["authority_scope"] == "bounded_manual_oracle_only"
    assert rows[0]["provenance_status"] == "exact_review_evidence_bundle_matched"
    assert rows[0]["review_packet_csv_sha256"] == file_sha256(review_packet_csv)
    assert rows[0]["blind_overlay_summary_json_sha256"] == file_sha256(
        overlay_summary_json
    )

    sidecar = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    assert sidecar["readiness_tier"] == "diagnostic_only"
    assert sidecar["packet_row_count"] == 2
    assert sidecar["oracle_call_counts"] == {"accept": 1, "reject": 1}
    assert sidecar["changes_matrix"] is False
    assert sidecar["changes_counted_detection"] is False
    verified = promotion.load_verified_oracle_promotion_bundle(
        outputs["promotion_packet_csv"]
    )
    assert verified.oracle_packet_id == "unit_oracle"
    assert len(verified.rows) == 2


def test_verified_promotion_rejects_packet_changed_during_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outputs = _write_two_row_promotion_bundle(tmp_path)
    original_revalidate = promotion._revalidate_promotion_sources

    def mutate_packet_after_source_revalidation(**kwargs: object) -> None:
        original_revalidate(**kwargs)  # type: ignore[arg-type]
        with outputs["promotion_packet_csv"].open(
            "a",
            encoding="utf-8",
        ) as handle:
            handle.write("\n")

    monkeypatch.setattr(
        promotion,
        "_revalidate_promotion_sources",
        mutate_packet_after_source_revalidation,
    )

    with pytest.raises(ValueError, match="packet changed during verification"):
        promotion.load_verified_oracle_promotion_bundle(outputs["promotion_packet_csv"])


def test_verified_promotion_rejects_paired_call_tamper(tmp_path: Path) -> None:
    outputs = _write_two_row_promotion_bundle(tmp_path)
    rows = read_delimited_rows(
        outputs["promotion_packet_csv"],
        required_columns=promotion.PROMOTION_COLUMNS,
        exact_columns=promotion.PROMOTION_COLUMNS,
        delimiter=",",
    )
    rows[0]["oracle_call"] = "reject"
    rows[0]["human_review_call"] = "reject"
    write_delimited_rows(
        outputs["promotion_packet_csv"],
        rows,
        promotion.PROMOTION_COLUMNS,
    )
    _refresh_promotion_packet_summary(outputs)

    with pytest.raises(ValueError, match="deterministic projection"):
        promotion.load_verified_oracle_promotion_bundle(outputs["promotion_packet_csv"])


@pytest.mark.parametrize("mutation", ["delete", "reverse"])
def test_verified_promotion_rejects_row_set_or_order_tamper(
    tmp_path: Path,
    mutation: str,
) -> None:
    outputs = _write_two_row_promotion_bundle(tmp_path)
    rows = read_delimited_rows(
        outputs["promotion_packet_csv"],
        required_columns=promotion.PROMOTION_COLUMNS,
        exact_columns=promotion.PROMOTION_COLUMNS,
        delimiter=",",
    )
    mutated = rows[:1] if mutation == "delete" else list(reversed(rows))
    write_delimited_rows(
        outputs["promotion_packet_csv"],
        mutated,
        promotion.PROMOTION_COLUMNS,
    )
    _refresh_promotion_packet_summary(outputs)

    with pytest.raises(ValueError, match="deterministic projection"):
        promotion.load_verified_oracle_promotion_bundle(outputs["promotion_packet_csv"])


def test_verified_promotion_rejects_extra_packet_column(tmp_path: Path) -> None:
    outputs = _write_two_row_promotion_bundle(tmp_path)
    rows = read_delimited_rows(
        outputs["promotion_packet_csv"],
        required_columns=promotion.PROMOTION_COLUMNS,
        exact_columns=promotion.PROMOTION_COLUMNS,
        delimiter=",",
    )
    rows[0]["unexpected"] = "value"
    write_delimited_rows(
        outputs["promotion_packet_csv"],
        rows,
        (*promotion.PROMOTION_COLUMNS, "unexpected"),
    )

    with pytest.raises(ValueError, match="does not match exact schema"):
        promotion.load_verified_oracle_promotion_bundle(outputs["promotion_packet_csv"])


def test_verified_promotion_rejects_missing_commit_summary(
    tmp_path: Path,
) -> None:
    outputs = _write_two_row_promotion_bundle(tmp_path)
    outputs["summary_json"].unlink()

    with pytest.raises(FileNotFoundError):
        promotion.load_verified_oracle_promotion_bundle(outputs["promotion_packet_csv"])


def test_verified_promotion_rejects_non_sibling_output_binding(
    tmp_path: Path,
) -> None:
    outputs = _write_two_row_promotion_bundle(tmp_path)
    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    summary["outputs"]["promotion_packet_csv"] = str(
        outputs["promotion_packet_csv"].resolve()
    )
    outputs["summary_json"].write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="fixed sibling"):
        promotion.load_verified_oracle_promotion_bundle(outputs["promotion_packet_csv"])


def test_verified_promotion_rejects_non_integer_call_counts(
    tmp_path: Path,
) -> None:
    outputs = _write_two_row_promotion_bundle(tmp_path)
    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    summary["oracle_call_counts"] = {"accept": True, "reject": True}
    outputs["summary_json"].write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="oracle_call_counts.*integer"):
        promotion.load_verified_oracle_promotion_bundle(outputs["promotion_packet_csv"])


def test_oracle_promotion_rejects_input_at_final_output_path(
    tmp_path: Path,
) -> None:
    (tmp_path / "sources").mkdir()
    review_packet_csv, overlay_summary_json = _write_review_bundle(
        tmp_path / "sources",
        [
            _review_packet_row(
                "cand-collision",
                status="pt_pattern_detected_diagnostic",
            )
        ],
    )
    output_dir = tmp_path / "promotion"
    output_dir.mkdir()
    adjudicated_csv = output_dir / promotion.PROMOTION_PACKET_FILE_NAME
    write_delimited_rows(
        adjudicated_csv,
        _bind_rows_to_bundle(
            [_adjudicated_row("cand-collision")],
            review_packet_csv=review_packet_csv,
            overlay_summary_json=overlay_summary_json,
        ),
        promotion.ADJUDICATED_REQUIRED_COLUMNS,
    )

    with pytest.raises(ValueError, match="input/output paths must be disjoint"):
        promotion.run_oracle_promotion(
            adjudicated_oracle_csv=adjudicated_csv,
            review_packet_csvs=[review_packet_csv],
            blind_overlay_summary_jsons=[overlay_summary_json],
            output_dir=output_dir,
            oracle_packet_id="unit_collision",
        )

    assert adjudicated_csv.is_file()
    assert not (output_dir / promotion.PROMOTION_SUMMARY_FILE_NAME).exists()


@pytest.mark.parametrize("failed_target", ["packet", "summary"])
def test_oracle_promotion_publish_failure_removes_commit_marker_and_temps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_target: str,
) -> None:
    outputs = _write_two_row_promotion_bundle(tmp_path)
    summary_path = outputs["summary_json"]
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    inputs = summary["inputs"]

    def source_path(descriptor: dict[str, str]) -> Path:
        path = Path(descriptor["path"])
        return path if path.is_absolute() else (summary_path.parent / path).resolve()

    adjudicated_csv = source_path(inputs["adjudicated_oracle_csv"])
    review_packet_csvs = [
        source_path(descriptor) for descriptor in inputs["review_packet_csvs"]
    ]
    overlay_summary_jsons = [
        source_path(descriptor) for descriptor in inputs["blind_overlay_summary_jsons"]
    ]
    original_replace = Path.replace
    failed_name = (
        promotion.PROMOTION_PACKET_FILE_NAME
        if failed_target == "packet"
        else promotion.PROMOTION_SUMMARY_FILE_NAME
    )

    def fail_selected_replace(self: Path, target: Path) -> Path:
        if Path(target).name == failed_name:
            raise OSError(f"injected {failed_target} publish failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_selected_replace)

    with pytest.raises(OSError, match=f"injected {failed_target}"):
        promotion.run_oracle_promotion(
            adjudicated_oracle_csv=adjudicated_csv,
            review_packet_csvs=review_packet_csvs,
            blind_overlay_summary_jsons=overlay_summary_jsons,
            output_dir=summary_path.parent,
            oracle_packet_id=summary["oracle_packet_id"],
        )

    assert not summary_path.exists()
    assert not list(summary_path.parent.glob(".*.tmp"))


def test_oracle_promotion_self_check_interrupt_removes_commit_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    review_packet_csv, overlay_summary_json = _write_review_bundle(
        tmp_path,
        [
            _review_packet_row(
                "cand-interrupt",
                status="pt_pattern_detected_diagnostic",
            )
        ],
    )
    adjudicated_csv = tmp_path / "adjudicated.csv"
    write_delimited_rows(
        adjudicated_csv,
        _bind_rows_to_bundle(
            [_adjudicated_row("cand-interrupt")],
            review_packet_csv=review_packet_csv,
            overlay_summary_json=overlay_summary_json,
        ),
        promotion.ADJUDICATED_REQUIRED_COLUMNS,
    )
    output_dir = tmp_path / "promotion"

    def interrupt_self_check(_packet_csv: Path) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(
        promotion,
        "load_verified_oracle_promotion_bundle",
        interrupt_self_check,
    )

    with pytest.raises(KeyboardInterrupt):
        promotion.run_oracle_promotion(
            adjudicated_oracle_csv=adjudicated_csv,
            review_packet_csvs=[review_packet_csv],
            blind_overlay_summary_jsons=[overlay_summary_json],
            output_dir=output_dir,
            oracle_packet_id="unit_oracle",
        )

    assert not (output_dir / promotion.PROMOTION_SUMMARY_FILE_NAME).exists()
    assert not list(output_dir.glob(".*.tmp"))


@pytest.mark.parametrize("invalid_count", [1.5, True, "1"])
def test_oracle_promotion_rejects_non_integer_adjudication_summary_count(
    tmp_path: Path,
    invalid_count: object,
) -> None:
    summary_json = tmp_path / "adjudication_summary.json"
    summary_json.write_text(
        json.dumps(
            {
                "total_rows": invalid_count,
                "accept_count": 1,
                "reject_count": 0,
                "uncertain_count": 0,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="total_rows must be an integer"):
        promotion._validate_adjudication_summary(
            summary_json,
            [_adjudicated_row("cand-summary-count")],
        )


def test_oracle_promotion_allows_human_call_to_differ_from_sealed_machine_call(
    tmp_path: Path,
) -> None:
    review_packet_csv, overlay_summary_json = _write_review_bundle(
        tmp_path,
        [
            _review_packet_row(
                "cand-disagree",
                status="pt_pattern_candidate_incomplete",
            )
        ],
    )
    adjudicated_csv = tmp_path / "adjudicated.csv"
    write_delimited_rows(
        adjudicated_csv,
        _bind_rows_to_bundle(
            [_adjudicated_row("cand-disagree")],
            review_packet_csv=review_packet_csv,
            overlay_summary_json=overlay_summary_json,
        ),
        promotion.ADJUDICATED_REQUIRED_COLUMNS,
    )

    outputs = promotion.run_oracle_promotion(
        adjudicated_oracle_csv=adjudicated_csv,
        review_packet_csvs=[review_packet_csv],
        blind_overlay_summary_jsons=[overlay_summary_json],
        output_dir=tmp_path / "promotion",
        oracle_packet_id="unit_oracle",
    )

    rows = read_delimited_rows(
        outputs["promotion_packet_csv"],
        required_columns=promotion.PROMOTION_COLUMNS,
        delimiter=",",
    )
    assert rows[0]["oracle_call"] == "accept"
    assert rows[0]["human_review_call"] == "accept"
    assert rows[0]["machine_call"] == "uncertain"


@pytest.mark.parametrize("field", ("machine_call", "machine_call_reason"))
def test_oracle_promotion_rejects_tampered_adjudicated_machine_provenance(
    tmp_path: Path,
    field: str,
) -> None:
    review_packet_csv, overlay_summary_json = _write_review_bundle(
        tmp_path,
        [
            _review_packet_row(
                "cand-tampered",
                status="pt_pattern_detected_diagnostic",
            )
        ],
    )
    rows = _bind_rows_to_bundle(
        [_adjudicated_row("cand-tampered")],
        review_packet_csv=review_packet_csv,
        overlay_summary_json=overlay_summary_json,
    )
    rows[0][field] = "tampered"
    adjudicated_csv = tmp_path / "adjudicated.csv"
    write_delimited_rows(
        adjudicated_csv,
        rows,
        promotion.ADJUDICATED_REQUIRED_COLUMNS,
    )

    with pytest.raises(ValueError, match=rf"adjudicated {field}.*sealed"):
        promotion.run_oracle_promotion(
            adjudicated_oracle_csv=adjudicated_csv,
            review_packet_csvs=[review_packet_csv],
            blind_overlay_summary_jsons=[overlay_summary_json],
            output_dir=tmp_path / "promotion",
            oracle_packet_id="unit_oracle",
        )


@pytest.mark.parametrize(
    ("field", "stale_value"),
    (
        ("base_mz", "500.1"),
        ("base_rt_apex_min", "10.1"),
    ),
)
def test_oracle_promotion_rejects_adjudicated_coordinate_mismatch(
    field: str,
    stale_value: str,
) -> None:
    adjudicated = _adjudicated_row("cand-coordinate-mismatch")
    adjudicated[field] = stale_value

    with pytest.raises(
        ValueError,
        match=rf"adjudicated {field}.*exact review packet row",
    ):
        _build_rows(
            adjudicated_rows=[adjudicated],
            review_packet_rows=[
                _review_packet_row(
                    "cand-coordinate-mismatch",
                    status="pt_pattern_detected_diagnostic",
                )
            ],
        )


@pytest.mark.parametrize(
    ("base_mz", "base_rt_apex_min"),
    (
        ("5e2", "10.000000"),
        ("", ""),
    ),
)
def test_oracle_promotion_canonicalizes_coordinates_from_review_packet(
    base_mz: str,
    base_rt_apex_min: str,
) -> None:
    adjudicated = _adjudicated_row("cand-coordinate-format")
    adjudicated["base_mz"] = base_mz
    adjudicated["base_rt_apex_min"] = base_rt_apex_min

    rows = _build_rows(
        adjudicated_rows=[adjudicated],
        review_packet_rows=[
            _review_packet_row(
                "cand-coordinate-format",
                status="pt_pattern_detected_diagnostic",
            )
        ],
    )

    assert rows[0]["base_mz"] == "500.0"
    assert rows[0]["base_rt_apex_min"] == "10.0"


@pytest.mark.parametrize(
    "field",
    (
        "candidate_id",
        "packet_label",
        "source_label",
        "candidate_assignment_sha256",
        "trace_extraction_sha256",
    ),
)
def test_oracle_promotion_rejects_sealed_machine_identity_mismatch(
    tmp_path: Path,
    field: str,
) -> None:
    review_packet_csv, overlay_summary_json = _write_review_bundle(
        tmp_path,
        [
            _review_packet_row(
                "cand-identity",
                status="pt_pattern_detected_diagnostic",
            )
        ],
    )
    machine_rows = _sealed_machine_rows(overlay_summary_json)
    machine_rows[0][field] = (
        "c" * 64
        if field in {"candidate_assignment_sha256", "trace_extraction_sha256"}
        else f"wrong-{field}"
    )
    _rewrite_sealed_machine_rows(overlay_summary_json, machine_rows)
    adjudicated_csv = tmp_path / "adjudicated.csv"
    write_delimited_rows(
        adjudicated_csv,
        _bind_rows_to_bundle(
            [_adjudicated_row("cand-identity")],
            review_packet_csv=review_packet_csv,
            overlay_summary_json=overlay_summary_json,
        ),
        promotion.ADJUDICATED_REQUIRED_COLUMNS,
    )

    with pytest.raises(ValueError, match=rf"{field}.*exact review packet"):
        promotion.run_oracle_promotion(
            adjudicated_oracle_csv=adjudicated_csv,
            review_packet_csvs=[review_packet_csv],
            blind_overlay_summary_jsons=[overlay_summary_json],
            output_dir=tmp_path / "promotion",
            oracle_packet_id="unit_oracle",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("machine_call", "reject"),
        ("machine_call_reason", "tampered_reason"),
        ("min_pearson_r_to_base", "0.10"),
        ("readiness_tier", "production_ready"),
    ),
)
def test_oracle_promotion_rejects_non_deterministic_sealed_machine_projection(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    review_packet_csv, overlay_summary_json = _write_review_bundle(
        tmp_path,
        [
            _review_packet_row(
                "cand-projection",
                status="pt_pattern_detected_diagnostic",
            )
        ],
    )
    machine_rows = _sealed_machine_rows(overlay_summary_json)
    machine_rows[0][field] = value
    _rewrite_sealed_machine_rows(overlay_summary_json, machine_rows)
    adjudicated_csv = tmp_path / "adjudicated.csv"
    write_delimited_rows(
        adjudicated_csv,
        _bind_rows_to_bundle(
            [_adjudicated_row("cand-projection")],
            review_packet_csv=review_packet_csv,
            overlay_summary_json=overlay_summary_json,
        ),
        promotion.ADJUDICATED_REQUIRED_COLUMNS,
    )

    with pytest.raises(ValueError, match="deterministic projection"):
        promotion.run_oracle_promotion(
            adjudicated_oracle_csv=adjudicated_csv,
            review_packet_csvs=[review_packet_csv],
            blind_overlay_summary_jsons=[overlay_summary_json],
            output_dir=tmp_path / "promotion",
            oracle_packet_id="unit_oracle",
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("schema_version", "unsupported_blind_overlay", "summary schema"),
        ("trace_artifact_role", "full_context", "trace_artifact_role"),
        ("gallery_mode", "full_context_only", "gallery_mode"),
    ),
)
def test_oracle_promotion_rejects_overlay_summary_role_matrix_conflict(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    review_packet_csv, overlay_summary_json = _write_review_bundle(
        tmp_path,
        [
            _review_packet_row(
                "cand-summary-role",
                status="pt_pattern_detected_diagnostic",
            )
        ],
    )
    summary = json.loads(overlay_summary_json.read_text(encoding="utf-8"))
    summary[field] = value
    overlay_summary_json.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    adjudicated_csv = tmp_path / "adjudicated.csv"
    write_delimited_rows(
        adjudicated_csv,
        _bind_rows_to_bundle(
            [_adjudicated_row("cand-summary-role")],
            review_packet_csv=review_packet_csv,
            overlay_summary_json=overlay_summary_json,
        ),
        promotion.ADJUDICATED_REQUIRED_COLUMNS,
    )

    with pytest.raises(ValueError, match=message):
        promotion.run_oracle_promotion(
            adjudicated_oracle_csv=adjudicated_csv,
            review_packet_csvs=[review_packet_csv],
            blind_overlay_summary_jsons=[overlay_summary_json],
            output_dir=tmp_path / "promotion",
            oracle_packet_id="unit_oracle",
        )


@pytest.mark.parametrize("mutation", ("missing", "extra"))
def test_oracle_promotion_rejects_missing_or_extra_sealed_machine_rows(
    tmp_path: Path,
    mutation: str,
) -> None:
    review_packet_csv, overlay_summary_json = _write_review_bundle(
        tmp_path,
        [
            _review_packet_row(
                "cand-row-set",
                status="pt_pattern_detected_diagnostic",
            )
        ],
    )
    bound_rows = _bind_rows_to_bundle(
        [_adjudicated_row("cand-row-set")],
        review_packet_csv=review_packet_csv,
        overlay_summary_json=overlay_summary_json,
    )
    machine_rows = _sealed_machine_rows(overlay_summary_json)
    if mutation == "missing":
        machine_rows = []
    else:
        extra = dict(machine_rows[0])
        extra["packet_row_id"] = "pt_only|extra"
        extra["candidate_id"] = "extra"
        machine_rows.append(extra)
    _rewrite_sealed_machine_rows(overlay_summary_json, machine_rows)
    bound_rows[0]["blind_overlay_summary_json_sha256"] = file_sha256(
        overlay_summary_json
    )
    adjudicated_csv = tmp_path / "adjudicated.csv"
    write_delimited_rows(
        adjudicated_csv,
        bound_rows,
        promotion.ADJUDICATED_REQUIRED_COLUMNS,
    )

    with pytest.raises(
        ValueError,
        match=rf"{mutation} packet_row_id",
    ):
        promotion.run_oracle_promotion(
            adjudicated_oracle_csv=adjudicated_csv,
            review_packet_csvs=[review_packet_csv],
            blind_overlay_summary_jsons=[overlay_summary_json],
            output_dir=tmp_path / "promotion",
            oracle_packet_id="unit_oracle",
        )


def test_oracle_promotion_rejects_uncertain_calls() -> None:
    with pytest.raises(ValueError, match="accept or reject"):
        _build_rows(
            adjudicated_rows=[
                _adjudicated_row("cand-uncertain", adjudicated_call="uncertain")
            ],
            review_packet_rows=[
                _review_packet_row(
                    "cand-uncertain",
                    status="pt_pattern_candidate_partial",
                )
            ],
        )


def test_oracle_promotion_rejects_machine_override_of_human_call() -> None:
    with pytest.raises(ValueError, match="must equal the fresh human user_call"):
        _build_rows(
            adjudicated_rows=[
                _adjudicated_row(
                    "cand-overridden",
                    adjudicated_call="reject",
                    user_call="accept",
                )
            ],
            review_packet_rows=[
                _review_packet_row(
                    "cand-overridden",
                    status="pt_pattern_candidate_review",
                )
            ],
        )


@pytest.mark.parametrize("user_call", ["uncertain", "unassessable", ""])
def test_oracle_promotion_excludes_non_binary_human_calls(user_call: str) -> None:
    with pytest.raises(ValueError, match="fresh human accept or reject"):
        _build_rows(
            adjudicated_rows=[
                _adjudicated_row(
                    "cand-not-calibratable",
                    adjudicated_call="reject",
                    user_call=user_call,
                )
            ],
            review_packet_rows=[
                _review_packet_row(
                    "cand-not-calibratable",
                    status="pt_pattern_candidate_review",
                )
            ],
        )


def test_oracle_promotion_requires_review_packet_provenance() -> None:
    with pytest.raises(ValueError, match="outside the review packet"):
        _build_rows(
            adjudicated_rows=[_adjudicated_row("cand-missing")],
            review_packet_rows=[],
        )


def test_oracle_promotion_rejects_adjudicated_row_outside_review_packet() -> None:
    review_rows = [
        _review_packet_row(
            "cand-one",
            status="pt_pattern_detected_diagnostic",
        )
    ]
    adjudicated_rows = [
        _adjudicated_row("cand-one"),
        _adjudicated_row("cand-extra"),
    ]

    with pytest.raises(ValueError, match="outside the review packet"):
        _build_rows(
            adjudicated_rows=adjudicated_rows,
            review_packet_rows=review_rows,
        )


def test_oracle_promotion_rejects_logical_id_match_with_stale_packet_hash() -> None:
    adjudicated = _adjudicated_row("cand-stale")
    adjudicated["review_packet_csv_sha256"] = "C" * 64

    with pytest.raises(ValueError, match="does not match the supplied review packet"):
        _build_rows(
            adjudicated_rows=[adjudicated],
            review_packet_rows=[
                _review_packet_row(
                    "cand-stale",
                    status="pt_pattern_detected_diagnostic",
                )
            ],
        )


def test_oracle_promotion_rejects_tampered_gallery_in_bound_summary(
    tmp_path: Path,
) -> None:
    review_rows = [
        _review_packet_row(
            "cand-supported",
            status="pt_pattern_detected_diagnostic",
        )
    ]
    review_packet_csv, overlay_summary_json = _write_review_bundle(
        tmp_path,
        review_rows,
    )
    adjudicated_csv = tmp_path / "adjudicated.csv"
    write_delimited_rows(
        adjudicated_csv,
        _bind_rows_to_bundle(
            [_adjudicated_row("cand-supported")],
            review_packet_csv=review_packet_csv,
            overlay_summary_json=overlay_summary_json,
        ),
        promotion.ADJUDICATED_REQUIRED_COLUMNS,
    )
    overlay_summary = json.loads(overlay_summary_json.read_text(encoding="utf-8"))
    gallery_path = Path(overlay_summary["outputs"]["gallery_html"])
    gallery_path.write_text(
        gallery_path.read_text(encoding="utf-8") + "\n<!-- tampered -->\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="gallery HTML sha256 does not match"):
        promotion.run_oracle_promotion(
            adjudicated_oracle_csv=adjudicated_csv,
            review_packet_csvs=[review_packet_csv],
            blind_overlay_summary_jsons=[overlay_summary_json],
            output_dir=tmp_path / "promotion",
            oracle_packet_id="unit_oracle",
        )


def test_oracle_promotion_rejects_historical_unbound_adjudication(
    tmp_path: Path,
) -> None:
    review_rows = [
        _review_packet_row(
            "cand-supported",
            status="pt_pattern_detected_diagnostic",
        )
    ]
    review_packet_csv, overlay_summary_json = _write_review_bundle(
        tmp_path,
        review_rows,
    )
    adjudicated_csv = tmp_path / "legacy_adjudicated.csv"
    write_delimited_rows(
        adjudicated_csv,
        [_adjudicated_row("cand-supported")],
        promotion.LEGACY_ADJUDICATED_REQUIRED_COLUMNS,
    )

    with pytest.raises(ValueError, match="review_packet_csv_sha256"):
        promotion.run_oracle_promotion(
            adjudicated_oracle_csv=adjudicated_csv,
            review_packet_csvs=[review_packet_csv],
            blind_overlay_summary_jsons=[overlay_summary_json],
            output_dir=tmp_path / "promotion",
            oracle_packet_id="unit_oracle",
        )


def test_oracle_promotion_cli_writes_packet(tmp_path: Path) -> None:
    adjudicated_csv = tmp_path / "adjudicated.csv"
    review_packet_csv, overlay_summary_json = _write_review_bundle(
        tmp_path,
        [
            _review_packet_row(
                "cand-supported",
                status="pt_pattern_detected_diagnostic",
            )
        ],
    )
    write_delimited_rows(
        adjudicated_csv,
        _bind_rows_to_bundle(
            [_adjudicated_row("cand-supported")],
            review_packet_csv=review_packet_csv,
            overlay_summary_json=overlay_summary_json,
        ),
        promotion.ADJUDICATED_REQUIRED_COLUMNS,
    )

    from tools.diagnostics import pt_pattern_oracle_promotion as cli

    assert (
        cli.main(
            [
                "--adjudicated-oracle-csv",
                str(adjudicated_csv),
                "--review-packet-csv",
                str(review_packet_csv),
                "--blind-overlay-summary-json",
                str(overlay_summary_json),
                "--output-dir",
                str(tmp_path / "promotion"),
                "--oracle-packet-id",
                "unit_oracle",
            ]
        )
        == 0
    )

    rows = read_delimited_rows(
        tmp_path / "promotion" / "pt_pattern_oracle_promotion_packet.csv",
        required_columns=promotion.PROMOTION_COLUMNS,
        delimiter=",",
    )
    assert rows[0]["oracle_status"] == "manual_oracle_promoted"

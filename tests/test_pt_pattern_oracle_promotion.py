from __future__ import annotations

import json
from pathlib import Path

import pytest

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
            "schema_version": packet.ANCHOR_LOCAL_SCHEMA_VERSION,
            "readiness_tier": "diagnostic_only",
            "evidence_owner": packet.EVIDENCE_OWNER,
            "packet_label": "unit_packet",
            "source_label": "pt_only",
            "packet_row_id": f"pt_only|{candidate_id}",
            "sample_stem": "SampleA",
            "candidate_id": candidate_id,
            "base_mz": "500.0",
            "base_rt_apex_min": "10.0",
            "trace_decision_basis": evidence.TRACE_DECISION_BASIS,
            "trace_smoothing_method": evidence.TRACE_SMOOTH_METHOD,
            "trace_smoothing_window_points": str(
                evidence.TRACE_SMOOTH_WINDOW_POINTS
            ),
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
    write_delimited_rows(review_packet_csv, review_rows, packet.PACKET_COLUMNS)
    write_delimited_rows(
        trace_points_csv,
        _trace_points([row["candidate_id"] for row in review_rows]),
        overlay.TRACE_POINT_REQUIRED_COLUMNS,
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
        required_columns=overlay.LEGACY_MACHINE_CALL_REQUIRED_COLUMNS,
        delimiter=",",
    )


def _rewrite_sealed_machine_rows(
    overlay_summary_json: Path,
    rows: list[dict[str, str]],
) -> None:
    summary = json.loads(overlay_summary_json.read_text(encoding="utf-8"))
    machine_calls_csv = Path(summary["outputs"]["machine_calls_csv"])
    write_delimited_rows(machine_calls_csv, rows, overlay.MACHINE_CALL_COLUMNS)
    summary["outputs"]["machine_calls_csv_sha256"] = file_sha256(
        machine_calls_csv
    )
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
    machine_by_packet_row_id = {
        row["packet_row_id"]: row for row in machine_rows
    }
    return [
        dict(row)
        | {
            "machine_call": machine_by_packet_row_id[row["packet_row_id"]][
                "machine_call"
            ],
            "machine_call_reason": machine_by_packet_row_id[
                row["packet_row_id"]
            ]["machine_call_reason"],
            "review_packet_csv_sha256": file_sha256(review_packet_csv),
            "blind_overlay_summary_json_sha256": file_sha256(
                overlay_summary_json
            ),
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
    assert rows[0]["machine_call"] == "reject"


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


@pytest.mark.parametrize("field", ("candidate_id", "packet_label", "source_label"))
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
    machine_rows[0][field] = f"wrong-{field}"
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

    assert cli.main(
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
    ) == 0

    rows = read_delimited_rows(
        tmp_path / "promotion" / "pt_pattern_oracle_promotion_packet.csv",
        required_columns=promotion.PROMOTION_COLUMNS,
        delimiter=",",
    )
    assert rows[0]["oracle_status"] == "manual_oracle_promoted"

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xic_extractor.discovery import pt_pattern_oracle_promotion as promotion
from xic_extractor.discovery import pt_pattern_review_packet as packet
from xic_extractor.tabular_io import read_delimited_rows, write_delimited_rows


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
            "base_mz": "500.0",
            "base_rt_apex_min": "10.0",
            "combined_pt_pattern_status": status,
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
) -> dict[str, str]:
    return {
        "dataset": "pt_only_fullscan",
        "case_index": "1",
        "adjudicated_call": adjudicated_call,
        "adjudication_basis": "unit_adjudication",
        "user_call": user_call,
        "machine_call": machine_call,
        "note_optional": "unit note",
        "machine_call_reason": "unit_machine_reason",
        "base_mz": "500.0",
        "base_rt_apex_min": "10.0",
        "candidate_id": candidate_id,
        "packet_row_id": f"pt_only|{candidate_id}",
    }


def test_oracle_promotion_writes_provenance_locked_packet(tmp_path: Path) -> None:
    adjudicated_csv = tmp_path / "adjudicated.csv"
    review_packet_csv = tmp_path / "review_packet.csv"
    summary_json = tmp_path / "adjudicated_summary.json"
    write_delimited_rows(
        adjudicated_csv,
        [
            _adjudicated_row("cand-accept"),
            _adjudicated_row(
                "cand-reject",
                adjudicated_call="reject",
                user_call="uncertain",
                machine_call="reject",
            ),
        ],
        promotion.ADJUDICATED_REQUIRED_COLUMNS,
    )
    write_delimited_rows(
        review_packet_csv,
        [
            _review_packet_row(
                "cand-accept",
                status="pt_pattern_detected_diagnostic",
            ),
            _review_packet_row(
                "cand-reject",
                status="pt_pattern_candidate_incomplete",
            ),
        ],
        packet.PACKET_COLUMNS,
    )
    summary_json.write_text(
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
        output_dir=tmp_path / "promotion",
        oracle_packet_id="unit_oracle",
        adjudication_summary_json=summary_json,
    )

    rows = read_delimited_rows(
        outputs["promotion_packet_csv"],
        required_columns=promotion.PROMOTION_COLUMNS,
        delimiter=",",
    )
    assert [row["oracle_call"] for row in rows] == ["accept", "reject"]
    assert [row["oracle_detection_status"] for row in rows] == [
        "detected",
        "non_detected",
    ]
    assert rows[0]["readiness_tier"] == "production_candidate"
    assert rows[0]["authority_scope"] == "bounded_manual_oracle_only"
    assert rows[0]["provenance_status"] == "review_packet_row_matched"

    sidecar = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    assert sidecar["readiness_tier"] == "production_candidate"
    assert sidecar["packet_row_count"] == 2
    assert sidecar["oracle_call_counts"] == {"accept": 1, "reject": 1}
    assert sidecar["changes_matrix"] is False
    assert sidecar["changes_counted_detection"] is False


def test_oracle_promotion_rejects_uncertain_calls() -> None:
    with pytest.raises(ValueError, match="accept or reject"):
        promotion.build_oracle_promotion_rows(
            adjudicated_rows=[
                _adjudicated_row("cand-uncertain", adjudicated_call="uncertain")
            ],
            review_packet_rows=[
                _review_packet_row(
                    "cand-uncertain",
                    status="pt_pattern_candidate_partial",
                )
            ],
            oracle_packet_id="unit_oracle",
        )


def test_oracle_promotion_requires_review_packet_provenance() -> None:
    with pytest.raises(ValueError, match="missing review packet"):
        promotion.build_oracle_promotion_rows(
            adjudicated_rows=[_adjudicated_row("cand-missing")],
            review_packet_rows=[],
            oracle_packet_id="unit_oracle",
        )


def test_oracle_promotion_cli_writes_packet(tmp_path: Path) -> None:
    adjudicated_csv = tmp_path / "adjudicated.csv"
    review_packet_csv = tmp_path / "review_packet.csv"
    write_delimited_rows(
        adjudicated_csv,
        [_adjudicated_row("cand-supported")],
        promotion.ADJUDICATED_REQUIRED_COLUMNS,
    )
    write_delimited_rows(
        review_packet_csv,
        [
            _review_packet_row(
                "cand-supported",
                status="pt_pattern_detected_diagnostic",
            )
        ],
        packet.PACKET_COLUMNS,
    )

    from tools.diagnostics import pt_pattern_oracle_promotion as cli

    assert cli.main(
        [
            "--adjudicated-oracle-csv",
            str(adjudicated_csv),
            "--review-packet-csv",
            str(review_packet_csv),
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

import csv
import json
from pathlib import Path

import pytest

from tools.diagnostics import owner_backfill_request_economics as economics


def test_owner_backfill_economics_groups_requests_by_identity_and_tag(
    tmp_path: Path,
) -> None:
    alignment_dir = tmp_path / "alignment"
    output_dir = tmp_path / "diagnostics"
    _write_tsv(
        alignment_dir / "alignment_review.tsv",
        (
            _review_row(
                "FAM001",
                tag="dR",
                identity="production_family",
                primary=True,
                detected=2,
                evidence="owner_complete_link",
                accepted_backfill=1,
            ),
            _review_row(
                "FAM002",
                tag="R",
                identity="provisional_discovery",
                primary=False,
                detected=1,
                evidence="single_sample_local_owner",
                accepted_backfill=0,
            ),
            _review_row(
                "FAM003",
                tag="MeR",
                identity="audit_family",
                primary=False,
                detected=2,
                evidence="identity_conflict_review_only",
                accepted_backfill=0,
            ),
        ),
    )
    _write_tsv(
        alignment_dir / "alignment_cells.tsv",
        (
            _cell_row("FAM001", "S1", "detected"),
            _cell_row("FAM001", "S2", "detected"),
            _cell_row("FAM001", "S3", "backfilled"),
            _cell_row("FAM002", "S1", "detected"),
            _cell_row("FAM002", "S2", "absent"),
            _cell_row("FAM002", "S3", "absent"),
            _cell_row("FAM003", "S1", "detected"),
            _cell_row("FAM003", "S2", "detected"),
            _cell_row("FAM003", "S3", "absent"),
        ),
    )

    code = economics.main(
        [
            "--alignment-dir",
            str(alignment_dir),
            "--output-dir",
            str(output_dir),
            "--owner-backfill-min-detected-samples",
            "1",
        ],
    )

    assert code == 0
    payload = json.loads(
        (output_dir / "owner_backfill_request_economics.json").read_text(
            encoding="utf-8",
        ),
    )
    assert payload["totals"]["eligible_family_count"] == 2
    assert payload["totals"]["request_target_count"] == 3
    assert payload["totals"]["production_request_target_count"] == 1
    assert payload["totals"]["non_primary_request_target_count"] == 2
    assert payload["totals"]["skipped_review_only_family_count"] == 1

    summary = _read_tsv(output_dir / "owner_backfill_request_economics_summary.tsv")
    assert {
        row["identity_decision"]: row["request_target_count"]
        for row in summary
    } == {
        "production_family": "1",
        "provisional_discovery": "2",
    }
    features = _read_tsv(output_dir / "owner_backfill_request_economics_features.tsv")
    assert features[0]["feature_family_id"] == "FAM002"
    assert features[0]["request_target_count"] == "2"
    assert features[0]["rescued_target_count"] == "0"
    assert features[0]["include_in_primary_matrix"] == "False"
    assert (output_dir / "owner_backfill_request_economics.md").is_file()


def test_owner_backfill_economics_accounts_for_preconsolidated_confirm_requests(
    tmp_path: Path,
) -> None:
    alignment_dir = tmp_path / "alignment"
    _write_tsv(
        alignment_dir / "alignment_review.tsv",
        (
            _review_row(
                "FAM001",
                tag="dR",
                identity="production_family",
                primary=True,
                detected=2,
                evidence="pre_backfill_identity_consolidated;family_count=2",
                accepted_backfill=1,
            ),
        ),
    )
    _write_tsv(
        alignment_dir / "alignment_cells.tsv",
        (
            _cell_row("FAM001", "S1", "detected", area="100"),
            _cell_row("FAM001", "S2", "detected", area="1000"),
            _cell_row("FAM001", "S3", "absent"),
        ),
    )

    result = economics.build_economics(
        alignment_dir=alignment_dir,
        owner_backfill_min_detected_samples=1,
    )

    feature = result["features"][0]
    assert feature["is_pre_backfill_consolidated"] is True
    assert feature["seed_center_count_estimate"] == 2
    assert feature["request_target_count"] == 2
    assert feature["request_extract_count_estimate"] == 4
    assert feature["confirmation_target_count"] == 1


def test_owner_backfill_cell_index_preserves_order_contracts() -> None:
    rows = (
        _cell_row("FAM002", "S2", "detected"),
        _cell_row("FAM001", "S1", "absent"),
        _cell_row("FAM002", "S1", "backfilled"),
        _cell_row("FAM001", "S2", "detected"),
    )

    index = economics._index_cell_rows(rows)

    assert index.sample_order == ("S2", "S1")
    assert [
        row["sample_stem"] for row in index.cells_by_feature["FAM002"]
    ] == [
        "S2",
        "S1",
    ]


def test_owner_backfill_writer_rejects_later_extra_fields(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="dict contains fields not in fieldnames"):
        economics._write_tsv(
            tmp_path / "rows.tsv",
            [
                {"family": "FAM001", "request_count": 1},
                {"family": "FAM002", "request_count": 2, "unexpected": "value"},
            ],
        )


def _review_row(
    feature_id: str,
    *,
    tag: str,
    identity: str,
    primary: bool,
    detected: int,
    evidence: str,
    accepted_backfill: int,
) -> dict[str, str]:
    return {
        "feature_family_id": feature_id,
        "neutral_loss_tag": tag,
        "family_center_mz": "300.0",
        "family_center_rt": "8.0",
        "detected_count": str(detected),
        "identity_decision": identity,
        "accepted_backfill_count": str(accepted_backfill),
        "review_backfill_count": "0",
        "include_in_primary_matrix": "TRUE" if primary else "FALSE",
        "family_evidence": evidence,
        "primary_evidence": evidence,
        "identity_reason": evidence,
        "row_flags": "",
        "warning": "",
    }


def _cell_row(
    feature_id: str,
    sample: str,
    status: str,
    *,
    area: str = "10",
) -> dict[str, str]:
    return {
        "feature_family_id": feature_id,
        "sample_stem": sample,
        "status": status,
        "area": area if status in {"detected", "backfilled"} else "",
        "apex_rt": "8.0" if status in {"detected", "backfilled"} else "",
        "neutral_loss_tag": "dR",
        "family_center_mz": "300.0",
        "family_center_rt": "8.0",
        "reason": "",
    }


def _write_tsv(path: Path, rows: tuple[dict[str, str], ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))

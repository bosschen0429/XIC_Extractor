import csv
import json
from pathlib import Path

import pytest

from scripts.check_bounded_product_lanes import (
    DEFAULT_ACCEPTANCE,
    DEFAULT_SCHEMA,
    DEFAULT_STATUS_INDEX,
    check_bounded_product_lanes,
)


@pytest.mark.ledger_snapshot
def test_bounded_product_lanes_accept_current_artifacts() -> None:
    assert check_bounded_product_lanes() == []


def test_bounded_schema_matches_acceptance_header() -> None:
    schema = json.loads(DEFAULT_SCHEMA.read_text(encoding="utf-8"))
    header, rows = _read_tsv(DEFAULT_ACCEPTANCE)

    assert header == schema["required_columns"]
    assert {row["lane_id"] for row in rows} == set(schema["required_lane_ids"])


def test_acceptance_packet_does_not_include_backfill() -> None:
    _, rows = _read_tsv(DEFAULT_ACCEPTANCE)

    assert all("backfill" not in row["lane_id"] for row in rows)


def test_checker_rejects_broad_backfill_row(tmp_path: Path) -> None:
    mutated = _mutated_acceptance(
        tmp_path,
        append={
            "lane_id": "broad_backfill_autowrite",
            "readiness_status": "parked",
            "bounded_lane_class": "broad_backfill",
        },
    )

    problems = check_bounded_product_lanes(acceptance_path=mutated)

    assert any("broad Backfill must not appear" in problem for problem in problems)
    assert any("unregistered lane_id" in problem for problem in problems)


def test_checker_rejects_unapproved_metadata_value_promotion(tmp_path: Path) -> None:
    mutated = _mutated_acceptance(
        tmp_path,
        lane_id="sample_metadata_role_value_behavior",
        updates={
            "readiness_status": "production_ready",
            "current_bounded_surface": "TRUE",
            "allowed_scope": "all_targets",
            "allowed_effect": "detected_flagged_only",
        },
    )

    problems = check_bounded_product_lanes(acceptance_path=mutated)

    assert any("readiness_status disagrees" in problem for problem in problems)
    assert any("production_ready lane set drifted" in problem for problem in problems)
    assert any("current bounded surfaces drifted" in problem for problem in problems)


def test_checker_rejects_selected_peak_or_area_writeback(tmp_path: Path) -> None:
    mutated = _mutated_acceptance(
        tmp_path,
        lane_id="review_action_selected_candidate_switch",
        updates={
            "may_change_selected_peak": "TRUE",
            "may_change_selected_area": "TRUE",
        },
    )

    problems = check_bounded_product_lanes(acceptance_path=mutated)

    assert any("forbidden capability" in problem for problem in problems)


def test_checker_rejects_status_index_disagreement(tmp_path: Path) -> None:
    status = _mutated_status_index(
        tmp_path,
        "sample_metadata_order_projection",
        {"readiness_status": "blocked"},
    )

    problems = check_bounded_product_lanes(status_index_path=status)

    assert any("readiness_status disagrees" in problem for problem in problems)


def test_checker_rejects_custom_status_index_product_risk(
    tmp_path: Path,
) -> None:
    status = _mutated_status_index(
        tmp_path,
        "sample_metadata_order_projection",
        {"may_change_quant_output": "TRUE"},
    )

    problems = check_bounded_product_lanes(status_index_path=status)

    assert any(
        "status index: row" in problem and "changes product output" in problem
        for problem in problems
    )


def test_checker_accepts_unrelated_valid_status_metadata_change(
    tmp_path: Path,
) -> None:
    header, rows = _read_tsv(DEFAULT_STATUS_INDEX)
    row = _row(rows, "sample_metadata_order_projection")
    row["notes"] += "; editorial clarification"
    status = _write_tsv(tmp_path / "status_metadata.tsv", header, rows)

    assert check_bounded_product_lanes(status_index_path=status) == []


def test_checker_rejects_status_index_extra_writer(
    tmp_path: Path,
) -> None:
    status_header, status_rows = _read_tsv(DEFAULT_STATUS_INDEX)
    extra = dict(status_rows[0])
    extra.update(
        {
            "lane_id": "targeted_ms1_shape_identity_broad_writer",
            "lane_group": "targeted_ms1",
            "readiness_status": "production_ready",
            "write_authority": "TRUE",
            "product_authority_scope": "backfill_policy_write_ready_rows",
        }
    )
    status_rows.append(extra)
    status = _write_tsv(tmp_path / "status_extra.tsv", status_header, status_rows)

    problems = check_bounded_product_lanes(status_index_path=status)

    assert any(
        "status index: unregistered lane_id values" in problem for problem in problems
    )
    assert any(
        "status index: status index writer lanes mismatch" in problem
        for problem in problems
    )


def test_checker_rejects_coordinated_schema_acceptance_status_promotion(
    tmp_path: Path,
) -> None:
    status = _mutated_status_index(
        tmp_path,
        "sample_metadata_role_value_behavior",
        {"readiness_status": "production_ready"},
    )
    acceptance = _mutated_acceptance(
        tmp_path,
        lane_id="sample_metadata_role_value_behavior",
        updates={
            "readiness_status": "production_ready",
            "current_bounded_surface": "TRUE",
            "allowed_scope": "all_targets",
            "allowed_effect": "detected_flagged_only",
        },
    )
    schema = _mutated_schema(
        tmp_path,
        {
            "rules": {
                "production_ready_lanes": [
                    "targeted_ms1_shape_identity_limited_rescue",
                    "sample_metadata_order_projection",
                    "sample_metadata_role_value_behavior",
                ]
            }
        },
    )

    problems = check_bounded_product_lanes(
        schema_path=schema,
        acceptance_path=acceptance,
        status_index_path=status,
    )

    assert any("bounded readiness status drifted" in problem for problem in problems)
    assert any(
        "schema production_ready_lanes drifted" in problem for problem in problems
    )
    assert any("production_ready lane set drifted" in problem for problem in problems)


def _mutated_acceptance(
    tmp_path: Path,
    lane_id: str | None = None,
    updates: dict[str, str] | None = None,
    append: dict[str, str] | None = None,
) -> Path:
    header, rows = _read_tsv(DEFAULT_ACCEPTANCE)
    if lane_id:
        for row in rows:
            if row["lane_id"] == lane_id:
                row.update(updates or {})
                break
        else:
            raise AssertionError(f"lane not found: {lane_id}")
    if append is not None:
        new_row = dict(rows[0])
        for field in header:
            new_row.setdefault(field, "")
        new_row.update(append)
        rows.append(new_row)
    return _write_tsv(tmp_path / "acceptance.tsv", header, rows)


def _mutated_schema(tmp_path: Path, updates: dict[str, object]) -> Path:
    schema = json.loads(DEFAULT_SCHEMA.read_text(encoding="utf-8"))
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(schema.get(key), dict):
            schema[key].update(value)
        else:
            schema[key] = value
    output = tmp_path / "schema.json"
    output.write_text(json.dumps(schema, indent=2), encoding="utf-8")
    return output


def _mutated_status_index(
    tmp_path: Path,
    lane_id: str,
    updates: dict[str, str],
) -> Path:
    header, rows = _read_tsv(DEFAULT_STATUS_INDEX)
    for row in rows:
        if row["lane_id"] == lane_id:
            row.update(updates)
            break
    else:
        raise AssertionError(f"lane not found: {lane_id}")
    return _write_tsv(tmp_path / "status.tsv", header, rows)


def _write_tsv(path: Path, header: list[str], rows: list[dict[str, str]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _read_tsv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return list(reader.fieldnames or []), list(reader)


def _row(rows: list[dict[str, str]], lane_id: str) -> dict[str, str]:
    for row in rows:
        if row["lane_id"] == lane_id:
            return row
    raise AssertionError(f"lane not found: {lane_id}")

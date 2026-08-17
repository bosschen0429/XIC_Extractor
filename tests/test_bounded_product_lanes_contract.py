import csv
import hashlib
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


@pytest.mark.ledger_snapshot
def test_bounded_schema_matches_acceptance_header() -> None:
    schema = json.loads(DEFAULT_SCHEMA.read_text(encoding="utf-8"))
    header, rows = _read_tsv(DEFAULT_ACCEPTANCE)

    assert header == schema["required_columns"]
    assert {row["lane_id"] for row in rows} == set(schema["required_lane_ids"])


@pytest.mark.ledger_snapshot
def test_limited_targeted_ms1_scope_and_effect_are_pinned() -> None:
    _, rows = _read_tsv(DEFAULT_ACCEPTANCE)
    row = _row(rows, "targeted_ms1_shape_identity_limited_rescue_v1")

    assert row["readiness_status"] == "production_ready"
    assert row["allowed_effect"] == "detected_flagged_only"
    assert set(row["allowed_scope"].split(";")) == {
        "limited_5hmdc_5medc_v1",
        "5-hmdC",
        "5-medC",
    }
    assert row["may_enable_gui"] == "FALSE"
    assert row["may_expand_targets"] == "FALSE"
    assert row["grants_new_product_authority"] == "FALSE"


@pytest.mark.ledger_snapshot
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


def test_checker_rejects_broader_target_promotion(tmp_path: Path) -> None:
    mutated = _mutated_acceptance(
        tmp_path,
        lane_id="targeted_ms1_shape_identity_broader_targets",
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


def test_checker_rejects_stale_status_index_hash(tmp_path: Path) -> None:
    mutated = _mutated_acceptance(
        tmp_path,
        lane_id="sample_metadata_order_projection_v1",
        updates={"source_status_index_sha256": "0" * 64},
    )

    problems = check_bounded_product_lanes(acceptance_path=mutated)

    assert any("source_status_index_sha256 mismatch" in problem for problem in problems)


def test_checker_rejects_status_index_disagreement(tmp_path: Path) -> None:
    status = _mutated_status_index(
        tmp_path,
        "sample_metadata_order_projection_v1",
        {"readiness_status": "blocked"},
    )

    problems = check_bounded_product_lanes(status_index_path=status)

    assert any("readiness_status disagrees" in problem for problem in problems)


def test_checker_rejects_custom_status_index_hash_target_mismatch(
    tmp_path: Path,
) -> None:
    status = _mutated_status_index(
        tmp_path,
        "sample_metadata_order_projection_v1",
        {"may_change_quant_output": "TRUE"},
    )

    problems = check_bounded_product_lanes(status_index_path=status)

    assert any("source_status_index_sha256 mismatch" in problem for problem in problems)
    assert any(
        "status index: row" in problem and "changes product output" in problem
        for problem in problems
    )


def test_checker_rejects_status_index_extra_writer_even_with_refreshed_hash(
    tmp_path: Path,
) -> None:
    status_header, status_rows = _read_tsv(DEFAULT_STATUS_INDEX)
    extra = dict(status_rows[0])
    extra.update(
        {
            "lane_id": "targeted_ms1_shape_identity_broad_writer_v2",
            "lane_group": "targeted_ms1",
            "readiness_status": "production_ready",
            "write_authority": "TRUE",
            "product_authority_scope": "backfill_policy_write_ready_rows",
        }
    )
    status_rows.append(extra)
    status = _write_tsv(tmp_path / "status_extra.tsv", status_header, status_rows)
    acceptance = _acceptance_with_source_hash(tmp_path, _sha256(status))

    problems = check_bounded_product_lanes(
        acceptance_path=acceptance,
        status_index_path=status,
    )

    assert any(
        "status index: unregistered lane_id values" in problem
        for problem in problems
    )
    assert any(
        "status index: status index writer lanes mismatch"
        in problem
        for problem in problems
    )


def test_checker_rejects_coordinated_schema_acceptance_status_promotion(
    tmp_path: Path,
) -> None:
    status = _mutated_status_index(
        tmp_path,
        "targeted_ms1_shape_identity_broader_targets",
        {"readiness_status": "production_ready"},
    )
    acceptance = _mutated_acceptance(
        tmp_path,
        lane_id="targeted_ms1_shape_identity_broader_targets",
        updates={
            "readiness_status": "production_ready",
            "current_bounded_surface": "TRUE",
            "allowed_scope": "all_targets",
            "allowed_effect": "detected_flagged_only",
            "source_status_index_sha256": _sha256(status),
        },
    )
    schema = _mutated_schema(
        tmp_path,
        {
            "rules": {
                "production_ready_lanes": [
                    "targeted_ms1_shape_identity_limited_rescue_v1",
                    "sample_metadata_order_projection_v1",
                    "targeted_ms1_shape_identity_broader_targets",
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
        "schema production_ready_lanes drifted" in problem
        for problem in problems
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


def _acceptance_with_source_hash(tmp_path: Path, source_hash: str) -> Path:
    header, rows = _read_tsv(DEFAULT_ACCEPTANCE)
    for row in rows:
        row["source_status_index_sha256"] = source_hash
    return _write_tsv(tmp_path / "acceptance_refreshed.tsv", header, rows)


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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _read_tsv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return list(reader.fieldnames or []), list(reader)


def _row(rows: list[dict[str, str]], lane_id: str) -> dict[str, str]:
    for row in rows:
        if row["lane_id"] == lane_id:
            return row
    raise AssertionError(f"lane not found: {lane_id}")

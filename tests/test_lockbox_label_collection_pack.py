import csv
import json
from pathlib import Path

import pytest

from scripts.build_lockbox_label_collection_pack import (
    LABEL_TEMPLATE_HEADER,
    LOCKBOX_MANIFEST,
    PACKET_INDEX_HEADER,
    build_lockbox_label_collection_pack,
)
from scripts.check_lockbox_label_schema import (
    LABEL_TEMPLATE,
    PACKET_DIR,
    SCHEMA_PATH,
    check_lockbox_label_schema,
)


def test_lockbox_label_schema_matches_pack_headers() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["required_label_template_columns"] == LABEL_TEMPLATE_HEADER
    assert schema["required_packet_index_columns"] == PACKET_INDEX_HEADER
    assert schema["allowed_peak_choice_labels"] == [
        "correct",
        "wrong_peak",
        "wrong_family",
        "unresolved",
        "insufficient_evidence",
    ]
    assert schema["allowed_area_labels"] == [
        "acceptable",
        "unacceptable",
        "not_assessable",
    ]
    assert "visual_trace_overlay_review" in schema["allowed_reviewer_reason_codes"]
    assert schema["allowed_evidence_viewed"] == [
        "packet",
        "packet_trace_overlay_hypothesis",
        "packet_recovered_trace_overlay_hypothesis",
        "packet_missing_evidence_record",
    ]
    assert schema["authority_rules"]["label_grants_product_authority"] is False
    assert schema["authority_rules"]["may_touch_matrix"] is False
    assert (
        schema["review_contract"][
            "completed_labels_reject_agent_or_subagent_reviewer_ids"
        ]
        is True
    )
    assert (
        schema["review_contract"]["completed_labels_require_reviewer_slots_1_and_2"]
        is True
    )
    assert schema["review_contract"]["allowed_human_truth_reviewer_ids"] == [
        "user_batch_review_2026_06_18",
        "second_human_lockbox_reviewer_v1",
    ]
    assert (
        schema["review_contract"][
            "ai_challenge_review_must_be_stored_outside_human_truth_slots"
        ]
        is True
    )


def test_label_collection_builder_is_deterministic_and_non_authoritative(
    tmp_path: Path,
) -> None:
    packet_dir = tmp_path / "packets"
    label_template = tmp_path / "labels.tsv"

    result = build_lockbox_label_collection_pack(
        packet_dir=packet_dir,
        label_template_path=label_template,
    )

    packet_rows = _read_tsv(packet_dir / "packet_index.tsv")
    label_rows = _read_tsv(label_template)
    markdown_packets = sorted(packet_dir.glob("LOCKBOXV1_*.md"))

    assert result["case_count"] == 72
    assert len(packet_rows) == 72
    assert len(markdown_packets) == 72
    assert len(label_rows) == 144
    assert {row["may_touch_matrix"] for row in packet_rows} == {"FALSE"}
    assert {row["may_grant_product_authority"] for row in packet_rows} == {"FALSE"}
    assert {row["label_grants_product_authority"] for row in label_rows} == {"FALSE"}
    assert {row["may_touch_matrix"] for row in label_rows} == {"FALSE"}
    assert {row["round_trip_oracle_used"] for row in label_rows} == {"FALSE"}
    assert {row["peak_choice_label"] for row in label_rows} == {""}
    assert {row["area_label"] for row in label_rows} == {""}
    assert {row["boundary_label"] for row in label_rows} == {""}


@pytest.mark.ledger_snapshot
def test_current_label_collection_pack_validates() -> None:
    assert check_lockbox_label_schema() == []


@pytest.mark.ledger_snapshot
def test_packets_have_visual_evidence_or_explicit_missing_reason() -> None:
    packet_rows = _read_tsv(PACKET_DIR / "packet_index.tsv")

    for row in packet_rows:
        if row["evidence_status"] == "missing_evidence_recorded":
            assert row["missing_evidence_reason"]
            continue
        assert row["trace_data_path"]
        assert row["trace_data_sha256"]
        assert row["overlay_png_path"]
        assert row["overlay_png_sha256"]
        assert row["hypothesis_png_path"]
        assert row["hypothesis_png_sha256"]


@pytest.mark.ledger_snapshot
def test_empty_label_template_fails_complete_mode() -> None:
    problems = check_lockbox_label_schema(require_complete=True)

    assert any("peak_choice_label is required" in problem for problem in problems)
    assert any("reviewer_id is required" in problem for problem in problems)


def test_completed_label_template_can_pass_complete_mode(tmp_path: Path) -> None:
    header, rows = _read_tsv_with_header(LABEL_TEMPLATE)
    for row in rows:
        row.update(
            {
                "reviewer_id": _human_reviewer_id(row),
                "reviewed_at_utc": "2026-06-18T00:00:00Z",
                "peak_choice_label": "correct",
                "area_label": "acceptable",
                "boundary_label": "acceptable",
                "reviewer_confidence": "medium",
                "reviewer_reason_code": "visual_trace_overlay_review",
                "evidence_viewed": "packet",
            },
        )
    labels = _write_tsv(tmp_path / "completed.tsv", header, rows)

    assert check_lockbox_label_schema(
        label_template_path=labels,
        require_complete=True,
    ) == []


def test_checker_rejects_invalid_label_enum(tmp_path: Path) -> None:
    mutated = _mutated_labels(
        tmp_path,
        0,
        {
            "peak_choice_label": "writer_approved",
            "area_label": "acceptable",
            "boundary_label": "acceptable",
            "reviewer_confidence": "high",
        },
    )

    problems = check_lockbox_label_schema(label_template_path=mutated)

    assert any("invalid peak_choice_label" in problem for problem in problems)


def test_checker_rejects_invalid_reason_code(tmp_path: Path) -> None:
    mutated = _mutated_labels(
        tmp_path,
        0,
        {"reviewer_reason_code": "freeform_reason"},
    )

    problems = check_lockbox_label_schema(label_template_path=mutated)

    assert any("invalid reviewer_reason_code" in problem for problem in problems)


def test_checker_rejects_completed_label_without_evidence_binding(
    tmp_path: Path,
) -> None:
    header, rows = _read_tsv_with_header(LABEL_TEMPLATE)
    for row in rows:
        row.update(
            {
                "reviewer_id": _human_reviewer_id(row),
                "reviewed_at_utc": "2026-06-18T00:00:00Z",
                "peak_choice_label": "correct",
                "area_label": "acceptable",
                "boundary_label": "acceptable",
                "reviewer_confidence": "medium",
                "reviewer_reason_code": "visual_trace_overlay_review",
                "evidence_viewed": "packet",
            },
        )
    rows[0]["evidence_viewed"] = ""
    rows[0]["source_artifact_hashes"] = "tampered"
    labels = _write_tsv(tmp_path / "unbound.tsv", header, rows)

    problems = check_lockbox_label_schema(
        label_template_path=labels,
        require_complete=True,
    )

    assert any("evidence_viewed is required" in problem for problem in problems)
    assert any("source_artifact_hashes must match packet" in p for p in problems)


def test_checker_rejects_label_identity_drift(tmp_path: Path) -> None:
    mutated = _mutated_labels(
        tmp_path,
        0,
        {"family_id": "FAM_TAMPERED"},
    )

    problems = check_lockbox_label_schema(label_template_path=mutated)

    assert any("family_id must match packet" in problem for problem in problems)


def test_checker_rejects_authority_flags(tmp_path: Path) -> None:
    mutated = _mutated_labels(
        tmp_path,
        0,
        {"label_grants_product_authority": "TRUE", "may_touch_matrix": "TRUE"},
    )

    problems = check_lockbox_label_schema(label_template_path=mutated)

    assert any("label_grants_product_authority must be FALSE" in p for p in problems)
    assert any("may_touch_matrix must be FALSE" in p for p in problems)


def test_checker_rejects_duplicate_completed_reviewer_ids(tmp_path: Path) -> None:
    header, rows = _read_tsv_with_header(LABEL_TEMPLATE)
    for row in rows:
        row.update(
            {
                "reviewer_id": "same_reviewer",
                "reviewed_at_utc": "2026-06-18T00:00:00Z",
                "peak_choice_label": "correct",
                "area_label": "acceptable",
                "boundary_label": "acceptable",
                "reviewer_confidence": "medium",
                "reviewer_reason_code": "visual_trace_overlay_review",
                "evidence_viewed": "packet",
            },
        )
    labels = _write_tsv(tmp_path / "duplicate.tsv", header, rows)

    problems = check_lockbox_label_schema(
        label_template_path=labels,
        require_complete=True,
    )

    assert any("reviewer IDs must be distinct" in problem for problem in problems)


def test_checker_rejects_subagent_as_human_truth_reviewer(
    tmp_path: Path,
) -> None:
    header, rows = _read_tsv_with_header(LABEL_TEMPLATE)
    for index, row in enumerate(rows):
        row.update(
            {
                "reviewer_id": (
                    "codex_subagent_review" if index % 2 else "owner_reviewer"
                ),
                "reviewed_at_utc": "2026-06-18T00:00:00Z",
                "peak_choice_label": "correct",
                "area_label": "acceptable",
                "boundary_label": "acceptable",
                "reviewer_confidence": "medium",
                "reviewer_reason_code": "visual_trace_overlay_review",
                "evidence_viewed": "packet",
            },
        )
    labels = _write_tsv(tmp_path / "subagent_truth.tsv", header, rows)

    problems = check_lockbox_label_schema(
        label_template_path=labels,
        require_complete=True,
    )

    assert any("reviewer_id is not human truth" in problem for problem in problems)


def test_checker_rejects_unregistered_human_looking_reviewer(
    tmp_path: Path,
) -> None:
    header, rows = _read_tsv_with_header(LABEL_TEMPLATE)
    for row in rows:
        row.update(
            {
                "reviewer_id": (
                    "reviewer_two"
                    if row["reviewer_slot"] == "2"
                    else "user_batch_review_2026_06_18"
                ),
                "reviewed_at_utc": "2026-06-18T00:00:00Z",
                "peak_choice_label": "correct",
                "area_label": "acceptable",
                "boundary_label": "acceptable",
                "reviewer_confidence": "medium",
                "reviewer_reason_code": "visual_trace_overlay_review",
                "evidence_viewed": "packet",
            },
        )
    labels = _write_tsv(tmp_path / "unregistered_truth.tsv", header, rows)

    problems = check_lockbox_label_schema(
        label_template_path=labels,
        require_complete=True,
    )

    assert any(
        "allowed human truth reviewer registry" in problem for problem in problems
    )


def test_checker_rejects_missing_packet_file(tmp_path: Path) -> None:
    packet_dir = tmp_path / "packets"
    label_template = tmp_path / "labels.tsv"
    build_lockbox_label_collection_pack(
        packet_dir=packet_dir,
        label_template_path=label_template,
    )
    first_packet = next(packet_dir.glob("LOCKBOXV1_*.md"))
    first_packet.unlink()

    problems = check_lockbox_label_schema(
        packet_dir=packet_dir,
        label_template_path=label_template,
    )

    assert any("packet file missing" in problem for problem in problems)


def test_checker_default_is_hermetic_but_can_verify_evidence_files(
    tmp_path: Path,
) -> None:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    trace = evidence_dir / "trace.json"
    overlay = evidence_dir / "overlay.png"
    hypothesis = evidence_dir / "overlay_hypothesis.png"
    trace.write_text('{"points": []}\n', encoding="utf-8")
    overlay.write_bytes(b"\x89PNG\r\n\x1a\nminimal-overlay")
    hypothesis.write_bytes(b"\x89PNG\r\n\x1a\nminimal-hypothesis")

    manifest = tmp_path / "manifest.tsv"
    manifest_header, manifest_rows = _read_tsv_with_header(LOCKBOX_MANIFEST)
    manifest_rows[0]["trace_data_path"] = str(trace)
    manifest_rows[0]["overlay_png_path"] = str(overlay)
    _write_tsv(manifest, manifest_header, manifest_rows)

    packet_dir = tmp_path / "packets"
    label_template = tmp_path / "labels.tsv"
    build_lockbox_label_collection_pack(
        manifest_path=manifest,
        packet_dir=packet_dir,
        label_template_path=label_template,
    )
    header, packet_rows = _read_tsv_with_header(packet_dir / "packet_index.tsv")
    row = next(
        packet_row
        for packet_row in packet_rows
        if packet_row["lockbox_case_id"] == manifest_rows[0]["lockbox_case_id"]
    )
    assert row["evidence_status"] == "complete_visual_evidence"
    row["trace_data_path"] = str(tmp_path / "missing_trace.json")
    _write_tsv(packet_dir / "packet_index.tsv", header, packet_rows)

    structural_problems = check_lockbox_label_schema(
        manifest_path=manifest,
        packet_dir=packet_dir,
        label_template_path=label_template,
    )
    evidence_problems = check_lockbox_label_schema(
        manifest_path=manifest,
        packet_dir=packet_dir,
        label_template_path=label_template,
        verify_evidence_files=True,
    )

    assert structural_problems == []
    assert any("trace_data_path missing" in problem for problem in evidence_problems)


def test_checker_rejects_noncanonical_packet_path(tmp_path: Path) -> None:
    packet_dir = tmp_path / "packets"
    label_template = tmp_path / "labels.tsv"
    build_lockbox_label_collection_pack(
        packet_dir=packet_dir,
        label_template_path=label_template,
    )
    header, packet_rows = _read_tsv_with_header(packet_dir / "packet_index.tsv")
    packet_rows[0]["packet_path"] = packet_rows[1]["packet_path"]
    _write_tsv(packet_dir / "packet_index.tsv", header, packet_rows)

    problems = check_lockbox_label_schema(
        packet_dir=packet_dir,
        label_template_path=label_template,
    )

    assert any("packet path must be canonical" in problem for problem in problems)


def _mutated_labels(
    tmp_path: Path,
    row_index: int,
    updates: dict[str, str],
) -> Path:
    header, rows = _read_tsv_with_header(LABEL_TEMPLATE)
    rows[row_index].update(updates)
    return _write_tsv(tmp_path / "labels.tsv", header, rows)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    return _read_tsv_with_header(path)[1]


def _read_tsv_with_header(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return list(reader.fieldnames or []), list(reader)


def _write_tsv(path: Path, header: list[str], rows: list[dict[str, str]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _human_reviewer_id(row: dict[str, str]) -> str:
    if row["reviewer_slot"] == "1":
        return "user_batch_review_2026_06_18"
    return "second_human_lockbox_reviewer_v1"

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from xic_extractor.alignment.backfill_evidence_contract import (
    ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
    ALIGNMENT_BACKFILL_CELL_EVIDENCE_SCHEMA_VERSION,
    PREDICATE_VERSION,
    SKIPPED_EVIDENCE_LEDGER_COLUMNS,
    BackfillEvidenceOwnerBindings,
    EvidenceClaimState,
    SkippedEvidenceOwnerBindings,
)
from xic_extractor.alignment.backfill_evidence_tsv import (
    iter_backfill_evidence_tsv,
    iter_skipped_evidence_tsv,
    lookup_backfill_evidence_tsv,
    lookup_skipped_evidence_tsv,
)
from xic_extractor.alignment.backfill_scope import (
    SkippedEvidenceRecord,
    write_skipped_evidence_ledger_tsv,
)


def _write_rows(
    path: Path,
    header: tuple[str, ...],
    rows: list[dict[str, str]],
) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    return path


def _backfill_row(**overrides: str) -> dict[str, str]:
    row = {
        "schema_version": ALIGNMENT_BACKFILL_CELL_EVIDENCE_SCHEMA_VERSION,
        "feature_family_id": "FAM000001",
        "group_hypothesis_id": "FAM000001",
        "public_family_id": "FAM000001",
        "sample_stem": "sample-a",
        "status": "detected",
        "production_cell_status": "detected",
        "rescue_tier": "",
        "write_matrix_value": "TRUE",
        "include_in_primary_matrix": "TRUE",
        "identity_decision": "primary",
        "row_flags": "backfill_cell_evidence_required;rescue_heavy",
        "area": "10",
        "primary_matrix_area": "9.5",
        "primary_matrix_area_source": "gaussian15_positive_asls_residual",
        "primary_matrix_area_reason": "",
        "apex_rt": "1.2",
        "height": "5",
        "peak_start_rt": "1.0",
        "peak_end_rt": "1.4",
        "rt_delta_sec": "'-1.25",
        "trace_quality": "owner_backfill",
        "scan_support_score": "1",
        "gap_fill_state": "gap_fill_detected",
        "gap_fill_reason": "group_centered_query_detected",
        "backfill_evidence_reason": "owner_backfill_supported",
        "source_candidate_id": "sample-a#candidate-1",
        "source_raw_file": "sample-a.raw",
        "neutral_loss_tag": "DNA_dR",
        "family_center_mz": "242.1",
        "family_center_rt": "1.25",
        "region_shadow_verdict": "",
        "reason": "owner-centered MS1 backfill",
    }
    row.update(overrides)
    return row


def _skipped_row(**overrides: str) -> dict[str, str]:
    row = {
        "feature_family_id": "FAM000001",
        "sample_stem": "sample-a",
        "family_center_mz": "242.1",
        "family_center_rt": "1.25",
        "rt_window_start": "-1.75",
        "rt_window_end": "4.25",
        "pre_backfill_category": "single_detected_no_consolidation_candidate",
        "skipped_stage": "owner_backfill",
        "skip_reason": "single_detected_no_consolidation_candidate",
        "backfill_scope": "production-equivalent",
        "predicate_version": PREDICATE_VERSION,
        "raw_xic_requests_skipped": "1",
        "would_emit_in_full_audit": "True",
        "full_audit_available": "True",
        "source_artifact": "owner_backfill",
    }
    row.update(overrides)
    return row


def _backfill_bindings() -> BackfillEvidenceOwnerBindings:
    return BackfillEvidenceOwnerBindings(
        known_group_ids=frozenset({"FAM000001", "FAM000002"}),
        known_sample_stems=frozenset({"sample-a", "sample-b"}),
        candidate_samples={
            "sample-a#candidate-1": "sample-a",
            "sample-b#candidate-1": "sample-b",
        },
    )


def _skipped_bindings() -> SkippedEvidenceOwnerBindings:
    return SkippedEvidenceOwnerBindings(
        known_feature_family_ids=frozenset({"FAM000001", "FAM000002"}),
        known_sample_stems=frozenset({"sample-a", "sample-b"}),
        expected_backfill_scope="production-equivalent",
        expected_predicate_version=PREDICATE_VERSION,
        expected_source_artifact="owner_backfill",
    )


def test_backfill_lookup_rehydrates_reduced_owner_record_and_claims(
    tmp_path: Path,
) -> None:
    path = _write_rows(
        tmp_path / "backfill.tsv",
        ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
        [_backfill_row()],
    )

    result = lookup_backfill_evidence_tsv(
        path,
        feature_family_id="FAM000001",
        sample_stem="sample-a",
        owner_bindings=_backfill_bindings(),
    )

    assert len(result.records) == 1
    record = result.records[0]
    assert record.identity == ("FAM000001", "FAM000001", "sample-a")
    assert record.write_matrix_value is True
    assert record.row_flags == (
        "backfill_cell_evidence_required",
        "rescue_heavy",
    )
    assert record.rt_delta_sec == -1.25
    assert result.claim("record_content").state is EvidenceClaimState.STRUCTURE_BOUND
    assert result.claim("cross_artifact_binding").state is (
        EvidenceClaimState.OWNER_REHYDRATED
    )
    assert result.claim("production_decision").state is (
        EvidenceClaimState.UNAVAILABLE
    )
    assert result.claim("full_aligned_cell").state is EvidenceClaimState.UNAVAILABLE
    assert result.claim("missing_key_absence").state is EvidenceClaimState.UNAVAILABLE


@pytest.mark.parametrize(
    "missing_owner_id",
    ["group_hypothesis_id", "public_family_id"],
)
def test_backfill_lookup_keeps_missing_owner_id_structure_bound(
    tmp_path: Path,
    missing_owner_id: str,
) -> None:
    path = _write_rows(
        tmp_path / "backfill.tsv",
        ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
        [_backfill_row(**{missing_owner_id: ""})],
    )

    result = lookup_backfill_evidence_tsv(
        path,
        feature_family_id="FAM000001",
        sample_stem="sample-a",
        owner_bindings=_backfill_bindings(),
    )

    assert len(result.records) == 1
    assert getattr(result.records[0], missing_owner_id) == ""
    assert result.claim("record_content").state is EvidenceClaimState.STRUCTURE_BOUND
    assert result.claim("cross_artifact_binding").state is (
        EvidenceClaimState.UNAVAILABLE
    )


def test_skipped_lookup_roundtrips_writer_record_and_claims(tmp_path: Path) -> None:
    source = SkippedEvidenceRecord(
        feature_family_id="FAM000001",
        sample_stem="sample-a",
        family_center_mz=242.1,
        family_center_rt=1.25,
        rt_window_start=-1.75,
        rt_window_end=4.25,
        pre_backfill_category="single_detected_no_consolidation_candidate",
        skipped_stage="owner_backfill",
        skip_reason="single_detected_no_consolidation_candidate",
        backfill_scope="production-equivalent",
        predicate_version=PREDICATE_VERSION,
        raw_xic_requests_skipped=1,
        would_emit_in_full_audit=True,
        full_audit_available=True,
        source_artifact="owner_backfill",
    )
    path = write_skipped_evidence_ledger_tsv(tmp_path / "skipped.tsv", (source,))

    result = lookup_skipped_evidence_tsv(
        path,
        feature_family_id="FAM000001",
        sample_stem="sample-a",
        owner_bindings=_skipped_bindings(),
    )

    assert result.records == (source,)
    assert result.claim("record_content").state is EvidenceClaimState.OWNER_REHYDRATED
    assert result.claim("cross_artifact_binding").state is (
        EvidenceClaimState.OWNER_REHYDRATED
    )
    assert result.claim("full_audit_execution").state is (
        EvidenceClaimState.STRUCTURE_BOUND
    )
    assert result.claim("missing_key_absence").state is EvidenceClaimState.UNAVAILABLE


@pytest.mark.parametrize(
    ("lookup", "header", "row", "query"),
    [
        (
            lookup_backfill_evidence_tsv,
            ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
            _backfill_row(),
            {"feature_family_id": "missing", "sample_stem": "sample-a"},
        ),
        (
            lookup_skipped_evidence_tsv,
            SKIPPED_EVIDENCE_LEDGER_COLUMNS,
            _skipped_row(),
            {"feature_family_id": "missing", "sample_stem": "sample-a"},
        ),
    ],
)
def test_missing_lookup_never_becomes_absence_proof(
    tmp_path: Path,
    lookup,
    header,
    row,
    query,
) -> None:
    path = _write_rows(tmp_path / "evidence.tsv", header, [row])

    result = lookup(path, **query)

    assert result.records == ()
    assert result.claim("record_content").state is EvidenceClaimState.UNAVAILABLE
    assert result.claim("missing_key_absence").state is EvidenceClaimState.UNAVAILABLE


@pytest.mark.parametrize("mutation", ["missing", "unknown", "reordered"])
def test_backfill_reader_rejects_noncanonical_header(
    tmp_path: Path,
    mutation: str,
) -> None:
    header = list(ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS)
    row = _backfill_row()
    if mutation == "missing":
        header.remove("sample_stem")
        row.pop("sample_stem")
    elif mutation == "unknown":
        header.append("caller_claim")
        row["caller_claim"] = "owner_rehydrated"
    else:
        header[1], header[2] = header[2], header[1]
    path = _write_rows(tmp_path / "backfill.tsv", tuple(header), [row])

    with pytest.raises(ValueError, match="exact header"):
        tuple(iter_backfill_evidence_tsv(path))


def test_backfill_reader_rejects_duplicate_header(tmp_path: Path) -> None:
    path = tmp_path / "backfill.tsv"
    header = (*ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS, "sample_stem")
    values = (*(_backfill_row()[column] for column in header[:-1]), "sample-b")
    path.write_text(
        "\t".join(header) + "\n" + "\t".join(values) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate header fields: sample_stem"):
        tuple(iter_backfill_evidence_tsv(path))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schema_version", "alignment_backfill_cell_evidence_v0", "schema_version"),
        ("write_matrix_value", "true", "write_matrix_value"),
        ("area", "nan", "area"),
        ("production_cell_status", "accepted_rescue", "rescue_tier"),
    ],
)
def test_backfill_reader_rejects_malformed_or_inconsistent_owner_fields(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    path = _write_rows(
        tmp_path / "backfill.tsv",
        ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
        [_backfill_row(**{field: value})],
    )

    with pytest.raises(ValueError, match=message):
        tuple(iter_backfill_evidence_tsv(path))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"group_hypothesis_id": "FAM999999"}, "unknown group_hypothesis_id"),
        ({"sample_stem": "sample-z"}, "unknown sample_stem"),
        (
            {"sample_stem": "sample-b", "source_candidate_id": "sample-a#candidate-1"},
            "belongs to sample",
        ),
    ],
)
def test_backfill_reader_rejects_cross_key_substitution(
    tmp_path: Path,
    overrides: dict[str, str],
    message: str,
) -> None:
    path = _write_rows(
        tmp_path / "backfill.tsv",
        ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
        [_backfill_row(**overrides)],
    )

    with pytest.raises(ValueError, match=message):
        tuple(
            iter_backfill_evidence_tsv(
                path,
                owner_bindings=_backfill_bindings(),
            )
        )


def test_backfill_lookup_rejects_duplicate_composite_identity(tmp_path: Path) -> None:
    path = _write_rows(
        tmp_path / "backfill.tsv",
        ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
        [_backfill_row(), _backfill_row(area="11")],
    )

    with pytest.raises(ValueError, match="duplicate composite identity"):
        lookup_backfill_evidence_tsv(
            path,
            feature_family_id="FAM000001",
            sample_stem="sample-a",
        )


def test_reordered_distinct_backfill_rows_preserve_source_order(tmp_path: Path) -> None:
    row_a = _backfill_row()
    row_b = _backfill_row(
        feature_family_id="FAM000002",
        group_hypothesis_id="FAM000002",
        public_family_id="FAM000002",
        sample_stem="sample-b",
        source_candidate_id="sample-b#candidate-1",
    )
    path = _write_rows(
        tmp_path / "backfill.tsv",
        ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
        [row_b, row_a],
    )

    records = tuple(
        iter_backfill_evidence_tsv(path, owner_bindings=_backfill_bindings())
    )

    assert [record.feature_family_id for record in records] == [
        "FAM000002",
        "FAM000001",
    ]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"predicate_version": "p7-backfill-scope-v0"}, "predicate_version"),
        ({"source_artifact": "caller_projection"}, "source_artifact"),
        ({"would_emit_in_full_audit": "FALSE"}, "would_emit_in_full_audit"),
        ({"raw_xic_requests_skipped": "0"}, "raw_xic_requests_skipped"),
        ({"sample_stem": "sample-z"}, "unknown sample_stem"),
        (
            {"backfill_scope": "selected-families"},
            "pre_backfill_category",
        ),
    ],
)
def test_skipped_reader_rejects_stale_malformed_or_cross_bound_fields(
    tmp_path: Path,
    overrides: dict[str, str],
    message: str,
) -> None:
    path = _write_rows(
        tmp_path / "skipped.tsv",
        SKIPPED_EVIDENCE_LEDGER_COLUMNS,
        [_skipped_row(**overrides)],
    )

    with pytest.raises(ValueError, match=message):
        tuple(
            iter_skipped_evidence_tsv(
                path,
                owner_bindings=_skipped_bindings(),
            )
        )


def test_skipped_lookup_rejects_duplicate_composite_identity(tmp_path: Path) -> None:
    path = _write_rows(
        tmp_path / "skipped.tsv",
        SKIPPED_EVIDENCE_LEDGER_COLUMNS,
        [_skipped_row(), _skipped_row(raw_xic_requests_skipped="2")],
    )

    with pytest.raises(ValueError, match="duplicate composite identity"):
        lookup_skipped_evidence_tsv(
            path,
            feature_family_id="FAM000001",
            sample_stem="sample-a",
        )

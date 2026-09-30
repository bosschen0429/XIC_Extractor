from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from tools.diagnostics import backfill_evidence_parquet_shadow
from xic_extractor.alignment.backfill_evidence_contract import (
    ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
    ALIGNMENT_BACKFILL_CELL_EVIDENCE_SCHEMA_VERSION,
    PREDICATE_VERSION,
    SKIPPED_EVIDENCE_LEDGER_COLUMNS,
    BackfillEvidenceOwnerBindings,
    EvidenceClaimState,
    SkippedEvidenceOwnerBindings,
)
from xic_extractor.diagnostics.backfill_evidence.bundle import (
    PARQUET_SHADOW_BUNDLE_SCHEMA_VERSION,
    open_backfill_evidence_parquet_shadow_bundle,
    write_backfill_evidence_parquet_shadow_bundle,
)
from xic_extractor.diagnostics.backfill_evidence.parquet import (
    iter_backfill_evidence_parquet,
    iter_skipped_evidence_parquet,
    lookup_backfill_evidence_parquet,
    lookup_skipped_evidence_parquet,
)
from xic_extractor.diagnostics.backfill_evidence.tsv import (
    iter_backfill_evidence_tsv,
    iter_skipped_evidence_tsv,
    lookup_backfill_evidence_tsv,
    lookup_skipped_evidence_tsv,
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
        "backfill_tier": "",
        "write_matrix_value": "TRUE",
        "include_in_primary_matrix": "TRUE",
        "identity_decision": "primary",
        "row_flags": "backfill_cell_evidence_required",
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
        "source_candidate_id": "sample-a#candidate-1",
        "neutral_loss_tag": "DNA_dR",
        "family_center_mz": "242.1",
        "family_center_rt": "1.25",
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


def _sources(
    tmp_path: Path,
    *,
    duplicate_backfill: bool = False,
    multiple_lookup_matches: bool = False,
) -> tuple[Path, ...]:
    backfill_rows = [
        _backfill_row(),
        _backfill_row(
            feature_family_id=(
                "FAM000001" if multiple_lookup_matches else "FAM000002"
            ),
            group_hypothesis_id="FAM000002",
            public_family_id="FAM000002",
            sample_stem="sample-a" if multiple_lookup_matches else "sample-b",
            source_candidate_id=(
                "sample-a#candidate-2"
                if multiple_lookup_matches
                else "sample-b#candidate-1"
            ),
        ),
    ]
    if duplicate_backfill:
        backfill_rows.append(_backfill_row(area="11"))
    backfill = _write_rows(
        tmp_path / "alignment_backfill_cell_evidence.tsv",
        ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
        backfill_rows,
    )
    skipped = _write_rows(
        tmp_path / "skipped_evidence_ledger.tsv",
        SKIPPED_EVIDENCE_LEDGER_COLUMNS,
        [
            _skipped_row(),
            _skipped_row(
                feature_family_id=(
                    "FAM000001" if multiple_lookup_matches else "FAM000002"
                ),
                sample_stem="sample-a" if multiple_lookup_matches else "sample-b",
                family_center_mz="243.1",
                **(
                    {
                        "pre_backfill_category": "selected_family_excluded",
                        "skip_reason": "not_in_selected_family_allowlist",
                        "backfill_scope": "selected-families",
                    }
                    if multiple_lookup_matches
                    else {}
                ),
            ),
        ],
    )
    metadata = tmp_path / "alignment_run_metadata.json"
    metadata.write_text(
        json.dumps(
            {
                "schema_version": "alignment-results",
                "backfill_scope": "production-equivalent",
                "skipped_evidence_predicate_version": PREDICATE_VERSION,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return backfill, skipped, metadata


def _backfill_bindings() -> BackfillEvidenceOwnerBindings:
    return BackfillEvidenceOwnerBindings(
        known_group_ids=frozenset({"FAM000001", "FAM000002"}),
        known_sample_stems=frozenset({"sample-a", "sample-b"}),
        candidate_samples={
            "sample-a#candidate-1": "sample-a",
            "sample-b#candidate-1": "sample-b",
        },
        group_feature_family_ids={
            "FAM000001": "FAM000001",
            "FAM000002": "FAM000002",
        },
        group_public_family_ids={
            "FAM000001": "FAM000001",
            "FAM000002": "FAM000002",
        },
    )


def _skipped_bindings() -> SkippedEvidenceOwnerBindings:
    return SkippedEvidenceOwnerBindings(
        known_feature_family_ids=frozenset({"FAM000001", "FAM000002"}),
        known_sample_stems=frozenset({"sample-a", "sample-b"}),
        expected_backfill_scope="production-equivalent",
    )


def test_shadow_bundle_roundtrips_owner_records_queries_and_claims(
    tmp_path: Path,
) -> None:
    backfill, skipped, metadata = _sources(tmp_path)
    output_dir = tmp_path / "backfill_evidence_parquet_shadow"

    result = write_backfill_evidence_parquet_shadow_bundle(
        output_dir,
        backfill_tsv=backfill,
        skipped_tsv=skipped,
        run_metadata_json=metadata,
    )
    bundle = open_backfill_evidence_parquet_shadow_bundle(
        result,
        source_root=tmp_path,
    )

    assert result == output_dir
    assert bundle.schema_version == PARQUET_SHADOW_BUNDLE_SCHEMA_VERSION
    assert tuple(iter_backfill_evidence_parquet(bundle.backfill_parquet)) == tuple(
        iter_backfill_evidence_tsv(backfill)
    )
    assert tuple(iter_skipped_evidence_parquet(bundle.skipped_parquet)) == tuple(
        iter_skipped_evidence_tsv(skipped)
    )

    tsv_backfill = lookup_backfill_evidence_tsv(
        backfill,
        feature_family_id="FAM000001",
        sample_stem="sample-a",
        owner_bindings=_backfill_bindings(),
    )
    parquet_backfill = lookup_backfill_evidence_parquet(
        bundle.backfill_parquet,
        feature_family_id="FAM000001",
        sample_stem="sample-a",
        owner_bindings=_backfill_bindings(),
    )
    assert parquet_backfill == tsv_backfill

    tsv_skipped = lookup_skipped_evidence_tsv(
        skipped,
        feature_family_id="FAM000001",
        sample_stem="sample-a",
        owner_bindings=_skipped_bindings(),
    )
    parquet_skipped = lookup_skipped_evidence_parquet(
        bundle.skipped_parquet,
        feature_family_id="FAM000001",
        sample_stem="sample-a",
        owner_bindings=_skipped_bindings(),
    )
    assert parquet_skipped == tsv_skipped


def test_shadow_bundle_rehydrates_formula_escaped_machine_identities(
    tmp_path: Path,
) -> None:
    backfill, skipped, metadata = _sources(tmp_path)
    canonical = {
        "feature_family_id": "=family",
        "group_hypothesis_id": "=group",
        "public_family_id": "=public",
        "sample_stem": "=sample",
        "source_candidate_id": "=candidate",
    }
    persisted = {field: f"'{value}" for field, value in canonical.items()}
    _write_rows(
        backfill,
        ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
        [_backfill_row(**persisted)],
    )
    bindings = BackfillEvidenceOwnerBindings(
        known_group_ids=frozenset({canonical["group_hypothesis_id"]}),
        known_sample_stems=frozenset({canonical["sample_stem"]}),
        candidate_samples={
            canonical["source_candidate_id"]: canonical["sample_stem"]
        },
        group_feature_family_ids={
            canonical["group_hypothesis_id"]: canonical["feature_family_id"]
        },
        group_public_family_ids={
            canonical["group_hypothesis_id"]: canonical["public_family_id"]
        },
    )
    output_dir = write_backfill_evidence_parquet_shadow_bundle(
        tmp_path / "shadow",
        backfill_tsv=backfill,
        skipped_tsv=skipped,
        run_metadata_json=metadata,
    )

    parquet_result = lookup_backfill_evidence_parquet(
        output_dir / "backfill.parquet",
        feature_family_id=canonical["feature_family_id"],
        group_hypothesis_id=canonical["group_hypothesis_id"],
        sample_stem=canonical["sample_stem"],
        owner_bindings=bindings,
    )

    assert len(parquet_result.records) == 1
    record = parquet_result.records[0]
    for field, value in canonical.items():
        assert getattr(record, field) == value


def test_shadow_bundle_roundtrips_structure_bound_rows_without_owner_ids(
    tmp_path: Path,
) -> None:
    backfill, skipped, metadata = _sources(tmp_path)
    _write_rows(
        backfill,
        ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
        [_backfill_row(group_hypothesis_id="", public_family_id="")],
    )
    output_dir = write_backfill_evidence_parquet_shadow_bundle(
        tmp_path / "shadow",
        backfill_tsv=backfill,
        skipped_tsv=skipped,
        run_metadata_json=metadata,
    )

    result = lookup_backfill_evidence_parquet(
        output_dir / "backfill.parquet",
        feature_family_id="FAM000001",
        sample_stem="sample-a",
        owner_bindings=BackfillEvidenceOwnerBindings(
            known_group_ids=frozenset(),
            known_sample_stems=frozenset({"sample-a"}),
            candidate_samples={"sample-a#candidate-1": "sample-a"},
        ),
    )

    assert len(result.records) == 1
    assert result.claim("record_content").state is EvidenceClaimState.STRUCTURE_BOUND
    assert result.claim("cross_artifact_binding").state is (
        EvidenceClaimState.UNAVAILABLE
    )


def test_shadow_manifest_uses_relative_locators_and_reduced_claim(
    tmp_path: Path,
) -> None:
    backfill, skipped, metadata = _sources(tmp_path)
    output_dir = tmp_path / "shadow"
    write_backfill_evidence_parquet_shadow_bundle(
        output_dir,
        backfill_tsv=backfill,
        skipped_tsv=skipped,
        run_metadata_json=metadata,
    )

    manifest_text = (output_dir / "bundle_manifest.json").read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)

    assert str(tmp_path) not in manifest_text
    assert manifest["schema_version"] == PARQUET_SHADOW_BUNDLE_SCHEMA_VERSION
    assert manifest["authority"] == "diagnostic_only_shadow"
    assert manifest["source_artifacts"]["backfill"]["filename"] == backfill.name
    assert manifest["source_artifacts"]["skipped"]["filename"] == skipped.name
    assert manifest["source_artifacts"]["run_metadata"]["filename"] == metadata.name


def test_missing_parquet_lookup_never_becomes_absence_proof(tmp_path: Path) -> None:
    backfill, skipped, metadata = _sources(tmp_path)
    output_dir = write_backfill_evidence_parquet_shadow_bundle(
        tmp_path / "shadow",
        backfill_tsv=backfill,
        skipped_tsv=skipped,
        run_metadata_json=metadata,
    )
    bundle = open_backfill_evidence_parquet_shadow_bundle(output_dir)

    backfill_result = lookup_backfill_evidence_parquet(
        bundle.backfill_parquet,
        feature_family_id="missing",
        sample_stem="sample-a",
    )
    skipped_result = lookup_skipped_evidence_parquet(
        bundle.skipped_parquet,
        feature_family_id="missing",
        sample_stem="sample-a",
    )

    assert backfill_result.records == ()
    assert skipped_result.records == ()
    assert backfill_result.claim("missing_key_absence").state is (
        EvidenceClaimState.UNAVAILABLE
    )
    assert skipped_result.claim("missing_key_absence").state is (
        EvidenceClaimState.UNAVAILABLE
    )


def test_shadow_bundle_rejects_stale_source_bytes(tmp_path: Path) -> None:
    backfill, skipped, metadata = _sources(tmp_path)
    output_dir = write_backfill_evidence_parquet_shadow_bundle(
        tmp_path / "shadow",
        backfill_tsv=backfill,
        skipped_tsv=skipped,
        run_metadata_json=metadata,
    )
    source_text = backfill.read_text(encoding="utf-8")
    backfill.write_text(
        source_text.replace("FAM000001", "FAM999999", 1),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="source backfill SHA-256 mismatch"):
        open_backfill_evidence_parquet_shadow_bundle(
            output_dir,
            source_root=tmp_path,
        )


@pytest.mark.parametrize(
    ("source_index", "source_label"),
    [(0, "backfill"), (1, "skipped"), (2, "run_metadata")],
)
def test_shadow_bundle_rejects_source_replacement_during_conversion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_index: int,
    source_label: str,
) -> None:
    from xic_extractor.diagnostics.backfill_evidence import (
        bundle as backfill_evidence_parquet_bundle,
    )

    sources = _sources(tmp_path)
    source_path = sources[source_index]
    output_dir = tmp_path / "shadow"
    original_write = backfill_evidence_parquet_bundle.write_skipped_evidence_parquet

    def write_then_replace_source(*args, **kwargs):
        row_count = original_write(*args, **kwargs)
        replacement = bytearray(source_path.read_bytes())
        replacement[0] ^= 1
        source_path.write_bytes(replacement)
        return row_count

    monkeypatch.setattr(
        backfill_evidence_parquet_bundle,
        "write_skipped_evidence_parquet",
        write_then_replace_source,
    )

    with pytest.raises(ValueError, match=f"source {source_label}"):
        write_backfill_evidence_parquet_shadow_bundle(
            output_dir,
            backfill_tsv=sources[0],
            skipped_tsv=sources[1],
            run_metadata_json=sources[2],
        )

    assert not output_dir.exists()
    assert not tuple(tmp_path.glob("shadow.tmp-*"))


def test_shadow_bundle_rejects_tampered_parquet_bytes(tmp_path: Path) -> None:
    backfill, skipped, metadata = _sources(tmp_path)
    output_dir = write_backfill_evidence_parquet_shadow_bundle(
        tmp_path / "shadow",
        backfill_tsv=backfill,
        skipped_tsv=skipped,
        run_metadata_json=metadata,
    )
    parquet_path = output_dir / "backfill.parquet"
    parquet_bytes = parquet_path.read_bytes()
    parquet_path.write_bytes(bytes([parquet_bytes[0] ^ 1]) + parquet_bytes[1:])

    with pytest.raises(ValueError, match="backfill Parquet SHA-256 mismatch"):
        open_backfill_evidence_parquet_shadow_bundle(output_dir)


def test_parquet_reader_rejects_unknown_column(tmp_path: Path) -> None:
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    backfill, skipped, metadata = _sources(tmp_path)
    output_dir = write_backfill_evidence_parquet_shadow_bundle(
        tmp_path / "shadow",
        backfill_tsv=backfill,
        skipped_tsv=skipped,
        run_metadata_json=metadata,
    )
    source = pq.read_table(output_dir / "backfill.parquet")
    mutated = source.append_column(
        "caller_claim",
        pa.array(["owner_rehydrated"] * source.num_rows),
    )
    mutated_path = tmp_path / "mutated.parquet"
    pq.write_table(mutated, mutated_path)

    with pytest.raises(ValueError, match="exact Parquet schema"):
        tuple(iter_backfill_evidence_parquet(mutated_path))


def test_parquet_reader_rejects_reordered_ordinals(tmp_path: Path) -> None:
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    backfill, skipped, metadata = _sources(tmp_path)
    output_dir = write_backfill_evidence_parquet_shadow_bundle(
        tmp_path / "shadow",
        backfill_tsv=backfill,
        skipped_tsv=skipped,
        run_metadata_json=metadata,
    )
    source = pq.read_table(output_dir / "backfill.parquet")
    ordinal_index = source.schema.get_field_index("_row_ordinal")
    mutated = source.set_column(
        ordinal_index,
        source.schema.field(ordinal_index),
        pa.array(list(reversed(range(source.num_rows)))),
    )
    mutated_path = tmp_path / "reordered.parquet"
    pq.write_table(mutated, mutated_path)

    with pytest.raises(ValueError, match="row ordinal"):
        tuple(iter_backfill_evidence_parquet(mutated_path))


@pytest.mark.parametrize(
    ("artifact_name", "lookup"),
    [
        ("backfill.parquet", lookup_backfill_evidence_parquet),
        ("skipped.parquet", lookup_skipped_evidence_parquet),
    ],
)
@pytest.mark.parametrize(
    "ordinals",
    [(1, 0), (0, 0)],
    ids=("reordered", "duplicate"),
)
def test_filtered_parquet_lookup_rejects_non_increasing_ordinals(
    tmp_path: Path,
    artifact_name: str,
    lookup,
    ordinals: tuple[int, int],
) -> None:
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    backfill, skipped, metadata = _sources(
        tmp_path,
        multiple_lookup_matches=True,
    )
    output_dir = write_backfill_evidence_parquet_shadow_bundle(
        tmp_path / "shadow",
        backfill_tsv=backfill,
        skipped_tsv=skipped,
        run_metadata_json=metadata,
    )
    source = pq.read_table(output_dir / artifact_name)
    ordinal_index = source.schema.get_field_index("_row_ordinal")
    mutated = source.set_column(
        ordinal_index,
        source.schema.field(ordinal_index),
        pa.array(ordinals),
    )
    mutated_path = tmp_path / f"mutated-{artifact_name}"
    pq.write_table(mutated, mutated_path)

    with pytest.raises(ValueError, match="strictly increasing"):
        lookup(
            mutated_path,
            feature_family_id="FAM000001",
            sample_stem="sample-a",
        )


def test_parquet_lookup_rejects_duplicate_composite_identity(tmp_path: Path) -> None:
    backfill, skipped, metadata = _sources(tmp_path, duplicate_backfill=True)
    output_dir = write_backfill_evidence_parquet_shadow_bundle(
        tmp_path / "shadow",
        backfill_tsv=backfill,
        skipped_tsv=skipped,
        run_metadata_json=metadata,
    )

    with pytest.raises(ValueError, match="duplicate composite identity"):
        lookup_backfill_evidence_parquet(
            output_dir / "backfill.parquet",
            feature_family_id="FAM000001",
            sample_stem="sample-a",
        )


def test_shadow_bundle_rejects_unknown_manifest_field(tmp_path: Path) -> None:
    backfill, skipped, metadata = _sources(tmp_path)
    output_dir = write_backfill_evidence_parquet_shadow_bundle(
        tmp_path / "shadow",
        backfill_tsv=backfill,
        skipped_tsv=skipped,
        run_metadata_json=metadata,
    )
    manifest_path = output_dir / "bundle_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["caller_claim"] = "product_authorized"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="exact manifest fields"):
        open_backfill_evidence_parquet_shadow_bundle(output_dir)


def test_failed_shadow_validation_never_publishes_final_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor.diagnostics.backfill_evidence import (
        bundle as backfill_evidence_parquet_bundle,
    )

    backfill, skipped, metadata = _sources(tmp_path)
    output_dir = tmp_path / "shadow"

    def fail_validation(*_args, **_kwargs):
        raise RuntimeError("injected staged validation failure")

    monkeypatch.setattr(
        backfill_evidence_parquet_bundle,
        "_validate_staged_bundle",
        fail_validation,
    )

    with pytest.raises(RuntimeError, match="injected staged validation failure"):
        write_backfill_evidence_parquet_shadow_bundle(
            output_dir,
            backfill_tsv=backfill,
            skipped_tsv=skipped,
            run_metadata_json=metadata,
        )

    assert not output_dir.exists()
    assert not tuple(tmp_path.glob("shadow.tmp-*"))


def test_shadow_bundle_refuses_to_overwrite_existing_output(tmp_path: Path) -> None:
    backfill, skipped, metadata = _sources(tmp_path)
    output_dir = tmp_path / "shadow"
    output_dir.mkdir()

    with pytest.raises(FileExistsError, match="already exists"):
        write_backfill_evidence_parquet_shadow_bundle(
            output_dir,
            backfill_tsv=backfill,
            skipped_tsv=skipped,
            run_metadata_json=metadata,
        )


def test_missing_optional_dependency_has_actionable_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor.diagnostics.backfill_evidence import (
        parquet as backfill_evidence_parquet,
    )

    def missing_pyarrow():
        raise RuntimeError(
            "Parquet shadow support requires the optional 'parquet' dependency"
        )

    monkeypatch.setattr(backfill_evidence_parquet, "_load_pyarrow", missing_pyarrow)

    with pytest.raises(RuntimeError, match="optional 'parquet' dependency"):
        tuple(iter_backfill_evidence_parquet(tmp_path / "missing.parquet"))


def test_opt_in_shadow_cli_builds_bundle(tmp_path: Path) -> None:
    backfill, skipped, metadata = _sources(tmp_path)
    output_dir = tmp_path / "shadow"

    code = backfill_evidence_parquet_shadow.main(
        [
            "--backfill-tsv",
            str(backfill),
            "--skipped-tsv",
            str(skipped),
            "--run-metadata-json",
            str(metadata),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert code == 0
    assert (output_dir / "bundle_manifest.json").is_file()

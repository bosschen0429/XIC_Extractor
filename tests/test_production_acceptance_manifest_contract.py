import csv
import hashlib
import json
from pathlib import Path

import pytest

from scripts.check_production_acceptance_manifest import (
    ACCEPTANCE_DECISIONS,
    PRODUCTION_ACCEPTANCE_MANIFEST_SCHEMA,
    REQUIRED_COLUMNS,
    check_production_acceptance_manifest,
    check_production_acceptance_manifest_schema,
    production_acceptance_manifest_sha256,
)


def test_current_production_acceptance_manifest_schema_validates() -> None:
    assert check_production_acceptance_manifest_schema() == []

    schema = json.loads(
        PRODUCTION_ACCEPTANCE_MANIFEST_SCHEMA.read_text(encoding="utf-8"),
    )

    assert schema["schema_version"] == "production_acceptance_manifest_schema"
    assert schema["required_columns"] == list(REQUIRED_COLUMNS)
    assert schema["allowed_acceptance_decisions"] == sorted(ACCEPTANCE_DECISIONS)
    assert schema["authority_rules"]["primary_key"] == [
        "peak_hypothesis_id",
        "sample_stem",
    ]
    assert schema["authority_rules"]["family_id_is_context_only"] is True
    assert (
        schema["authority_rules"]["acceptance_manifest_writes_default_matrix"]
        is False
    )
    assert schema["authority_rules"]["source_artifact_hashes_must_match_files"] is True
    assert (
        schema["authority_rules"]["source_artifact_paths_must_stay_within_repo_root"]
        is True
    )
    assert (
        schema["authority_rules"]["accepted_quant_value_must_be_finite_non_negative"]
        is True
    )
    assert schema["authority_rules"]["backfill_fraction_must_match_counts"] is True


def test_checker_accepts_minimal_authorized_manifest_row(tmp_path: Path) -> None:
    manifest = tmp_path / "production_acceptance.tsv"
    _write_manifest(manifest, [_valid_accept_row()])

    assert _check_manifest(manifest) == []


def test_checker_rejects_missing_peak_hypothesis_id_even_with_family(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "production_acceptance.tsv"
    row = _valid_accept_row()
    row["peak_hypothesis_id"] = ""
    row["feature_family_id"] = "FAMILY_ONLY_IS_NOT_AUTHORITY"
    _write_manifest(manifest, [row])

    problems = _check_manifest(manifest)

    assert any("missing peak_hypothesis_id" in problem for problem in problems)
    assert any(
        "accepted row must have a formal primary key" in problem
        for problem in problems
    )


def test_checker_rejects_duplicate_primary_key_even_across_families(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "production_acceptance.tsv"
    first = _valid_accept_row()
    second = _valid_accept_row()
    second["feature_family_id"] = "FAM_OTHER"
    _write_manifest(manifest, [first, second])

    problems = _check_manifest(manifest)

    assert any("duplicate primary key" in problem for problem in problems)


def test_checker_rejects_manual_negative_acceptance(tmp_path: Path) -> None:
    manifest = tmp_path / "production_acceptance.tsv"
    row = _valid_accept_row()
    row["truth_status"] = "manual_negative"
    row["hard_blocker_rule_ids"] = "manual_negative"
    _write_manifest(manifest, [row])

    problems = _check_manifest(manifest)

    assert any("manual_negative cannot grant write authority" in p for p in problems)
    assert any("hard blocker cannot grant write authority" in p for p in problems)


def test_checker_rejects_blocked_doublet_write_authority(tmp_path: Path) -> None:
    blocked_cases = (
        ("right_reference_blocked", "right"),
        ("unclear_reference_blocked", "unclear"),
        ("unresolved_blocked", "unresolved"),
    )
    for index, (status, side) in enumerate(blocked_cases):
        manifest = tmp_path / f"production_acceptance_{index}.tsv"
        row = _valid_accept_row()
        row["doublet_status"] = status
        row["reference_side"] = side
        row["doublet_allowed"] = "FALSE"
        _write_manifest(manifest, [row])

        problems = _check_manifest(manifest)

        assert any("doublet state cannot grant write authority" in p for p in problems)


def test_checker_rejects_authority_lane_contradictions(tmp_path: Path) -> None:
    cases = (
        (
            {"shadow_only": "TRUE"},
            "shadow_only row cannot grant write authority",
        ),
        (
            {"acceptance_decision": "require_review"},
            "non-acceptance decision cannot grant write authority",
        ),
        (
            {"write_authority": "FALSE", "matrix_write_allowed": "TRUE"},
            "matrix_write_allowed requires write_authority",
        ),
    )
    for index, (updates, expected) in enumerate(cases):
        manifest = tmp_path / f"production_acceptance_{index}.tsv"
        row = _valid_accept_row()
        row.update(updates)
        _write_manifest(manifest, [row])

        problems = _check_manifest(manifest)

        assert any(expected in problem for problem in problems)


def test_checker_rejects_missing_provenance_and_manifest_hash_drift(
    tmp_path: Path,
) -> None:
    missing_source = tmp_path / "missing_source.tsv"
    row = _valid_accept_row()
    row["source_row_sha256"] = ""
    _write_manifest(missing_source, [row])

    problems = _check_manifest(missing_source)

    assert any("source_row_sha256 is required" in problem for problem in problems)

    stale_manifest = tmp_path / "stale_manifest.tsv"
    row = _valid_accept_row()
    _write_manifest(stale_manifest, [row])
    header, rows = _read_manifest(stale_manifest)
    rows[0]["manifest_sha256"] = "0" * 64
    _write_raw_manifest(stale_manifest, header, rows)

    problems = _check_manifest(stale_manifest)

    assert any("manifest_sha256 mismatch" in problem for problem in problems)


def test_checker_rejects_naked_alignment_or_shadow_source_write(
    tmp_path: Path,
) -> None:
    cases = (
        (
            {"quant_value_source": "alignment_cells_area_only"},
            "naked alignment_cells area cannot grant authority",
        ),
        (
            {
                "source_artifact_relpath": (
                    "docs/superpowers/validation/"
                    "lockbox_shadow_automation_cases.tsv"
                ),
            },
            (
                "direct shadow/report/gallery/candidate source cannot grant "
                "write authority"
            ),
        ),
    )
    for index, (updates, expected) in enumerate(cases):
        manifest = tmp_path / f"production_acceptance_{index}.tsv"
        row = _valid_accept_row()
        row.update(updates)
        _write_manifest(manifest, [row])

        problems = _check_manifest(manifest)

        assert any(expected in problem for problem in problems)


def test_checker_rejects_source_artifact_hash_mismatch(tmp_path: Path) -> None:
    manifest = tmp_path / "production_acceptance.tsv"
    row = _valid_accept_row()
    _write_manifest(manifest, [row])
    header, rows = _read_manifest(manifest)
    rows[0]["source_artifact_sha256"] = "0" * 64
    _write_raw_manifest(manifest, header, rows)

    problems = _check_manifest(manifest)

    assert any("source_artifact_sha256 mismatch" in problem for problem in problems)


def test_checker_reports_hash_mismatch_per_row_for_shared_source(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "production_acceptance.tsv"
    second = _valid_accept_row()
    second["peak_hypothesis_id"] = "PH002"
    _write_manifest(manifest, [_valid_accept_row(), second])
    header, rows = _read_manifest(manifest)
    for source_name in ("doublet_source", "source_artifact"):
        shared = tmp_path / rows[0][f"{source_name}_relpath"]
        actual = hashlib.sha256(shared.read_bytes()).hexdigest().upper()
        for row in rows:
            row[f"{source_name}_sha256"] = actual
    rows[1]["source_artifact_sha256"] = "0" * 64
    manifest_sha = production_acceptance_manifest_sha256(rows)
    for row in rows:
        row["manifest_sha256"] = manifest_sha
    _write_raw_manifest(manifest, header, rows)

    problems = _check_manifest(manifest)

    assert [p for p in problems if "sha256 mismatch" in p] == [
        "row 3: source_artifact_sha256 mismatch"
    ]


def test_checker_rejects_source_path_traversal(tmp_path: Path) -> None:
    cases = (
        ("source_artifact_relpath", "sources/../../outside.tsv"),
        ("doublet_source_relpath", r"..\outside.tsv"),
    )
    for index, (field, value) in enumerate(cases):
        manifest = tmp_path / f"production_acceptance_{index}.tsv"
        row = _valid_accept_row()
        _write_manifest(manifest, [row])
        header, rows = _read_manifest(manifest)
        rows[0][field] = value
        _write_raw_manifest(manifest, header, rows)

        problems = _check_manifest(manifest)

        assert any(f"{field} must stay within repo_root" in p for p in problems)


@pytest.mark.parametrize("alias_kind", ("leaf", "parent"))
def test_checker_classifies_resolved_authority_source_path(
    tmp_path: Path,
    alias_kind: str,
) -> None:
    manifest = tmp_path / "production_acceptance.tsv"
    row = _valid_accept_row()
    row["source_artifact_relpath"] = "validation/approved/source.tsv"
    _write_manifest(manifest, [row])

    approved_dir = tmp_path / "validation/approved"
    forbidden_dir = tmp_path / "validation/lockbox_shadow_automation"
    forbidden_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        if alias_kind == "leaf":
            forbidden_dir.mkdir()
            (approved_dir / "source.tsv").rename(forbidden_dir / "source.tsv")
            (approved_dir / "source.tsv").symlink_to(
                Path("../lockbox_shadow_automation/source.tsv")
            )
        else:
            approved_dir.rename(forbidden_dir)
            approved_dir.symlink_to(
                Path("lockbox_shadow_automation"),
                target_is_directory=True,
            )
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows runner lacks symbolic-link privilege")
        raise

    problems = _check_manifest(manifest)

    assert any(
        "direct shadow/report/gallery/candidate source" in problem
        for problem in problems
    )


def test_checker_rejects_missing_source_path_without_crashing(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "production_acceptance.tsv"
    row = _valid_accept_row()
    _write_manifest(manifest, [row])
    header, rows = _read_manifest(manifest)
    rows[0]["source_artifact_relpath"] = ""
    _write_raw_manifest(manifest, header, rows)

    problems = _check_manifest(manifest)

    assert any("source_artifact_relpath is required" in p for p in problems)


def test_checker_rejects_invalid_authorized_quant_value(tmp_path: Path) -> None:
    invalid_values = ("abc", "NaN", "-1")
    for index, value in enumerate(invalid_values):
        manifest = tmp_path / f"production_acceptance_{index}.tsv"
        row = _valid_accept_row()
        row["quant_value"] = value
        _write_manifest(manifest, [row])

        problems = _check_manifest(manifest)

        assert any("quant_value must be finite non-negative" in p for p in problems)


def test_checker_rejects_backfill_fraction_mismatch(tmp_path: Path) -> None:
    manifest = tmp_path / "production_acceptance.tsv"
    row = _valid_accept_row()
    row["backfill_fraction"] = "0.100000"
    _write_manifest(manifest, [row])

    problems = _check_manifest(manifest)

    assert any("backfill_fraction mismatch" in problem for problem in problems)


def test_report_only_prevalence_risk_does_not_block_acceptance(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "production_acceptance.tsv"
    row = _valid_accept_row()
    row["detected_count"] = "1"
    row["backfilled_count"] = "49"
    row["quant_available_count"] = "50"
    row["backfill_fraction"] = "0.980000"
    row["prevalence_flags"] = "low_seed_support;high_backfill_dependency"
    row["triggered_risk_rule_ids"] = "low_seed_support;high_backfill_dependency"
    row["closure_rule_ids"] = ""
    _write_manifest(manifest, [row])

    assert _check_manifest(manifest) == []


def test_strict_risk_requires_specific_closure(tmp_path: Path) -> None:
    manifest = tmp_path / "production_acceptance.tsv"
    row = _valid_accept_row()
    row["acceptance_decision"] = "accept_strict_backfill"
    row["acceptance_basis"] = "machine_strict"
    row["triggered_risk_rule_ids"] = "weak_boundary"
    row["closure_rule_ids"] = ""
    _write_manifest(manifest, [row])

    problems = _check_manifest(manifest)

    assert any(
        "strict risk requires closure_rule_ids" in problem
        for problem in problems
    )

    row["closure_rule_ids"] = "boundary_stability"
    _write_manifest(manifest, [row])

    assert _check_manifest(manifest) == []


def _valid_accept_row() -> dict[str, str]:
    return {
        "schema_version": "production_acceptance_manifest",
        "peak_hypothesis_id": "PH001",
        "sample_stem": "Sample001_DNA",
        "feature_family_id": "FAM001",
        "acceptance_decision": "accept_basic_backfill",
        "acceptance_basis": "machine_basic",
        "truth_status": "not_truth_claimed",
        "shadow_only": "FALSE",
        "write_authority": "TRUE",
        "matrix_write_allowed": "TRUE",
        "quant_value": "123.456",
        "quant_value_source": "gaussian_smoothed_integration",
        "matrix_area_source": "gaussian_smoothed_boundary_integration",
        "detected_count": "12",
        "backfilled_count": "8",
        "quant_available_count": "20",
        "missing_count": "0",
        "backfill_fraction": "0.400000",
        "prevalence_flags": "",
        "hard_blocker_rule_ids": "",
        "triggered_risk_rule_ids": "",
        "closure_rule_ids": "trace_integration_provenance",
        "decision_reason": "machine_basic_identity_and_trace_provenance",
        "next_evidence_needed": "",
        "doublet_status": "no_doublet_claim",
        "reference_side": "not_applicable",
        "doublet_allowed": "TRUE",
        "doublet_source_relpath": "sources/doublet_source.tsv",
        "doublet_source_sha256": "",
        "source_artifact_relpath": "sources/alignment_backfill_cell_evidence.tsv",
        "source_artifact_sha256": "",
        "source_row_sha256": "C" * 64,
        "manifest_sha256": "",
        "acceptance_contract_version": "production_acceptance_manifest_contract",
}


def _check_manifest(path: Path) -> list[str]:
    return check_production_acceptance_manifest(
        manifest_path=path,
        repo_root=path.parent,
    )


def _write_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    rows = [dict(row) for row in rows]
    for row in rows:
        _materialize_source_file(path.parent, row, "doublet_source")
        _materialize_source_file(path.parent, row, "source_artifact")
    manifest_sha = production_acceptance_manifest_sha256(rows)
    for row in rows:
        row["manifest_sha256"] = manifest_sha
    _write_raw_manifest(path, list(REQUIRED_COLUMNS), rows)


def _materialize_source_file(root: Path, row: dict[str, str], source_name: str) -> None:
    path_field = f"{source_name}_relpath"
    sha_field = f"{source_name}_sha256"
    relative = row[path_field]
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    content = f"{source_name}\t{row['peak_hypothesis_id']}\t{row['sample_stem']}\n"
    path.write_bytes(content.encode("utf-8"))
    row[sha_field] = hashlib.sha256(content.encode("utf-8")).hexdigest().upper()


def _read_manifest(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return list(reader.fieldnames or []), list(reader)


def _write_raw_manifest(
    path: Path,
    header: list[str],
    rows: list[dict[str, str]],
) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)

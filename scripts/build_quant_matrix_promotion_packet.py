"""Build/check the QuantMatrix promotion packet.

This no-RAW adapter binds the real/candidate QuantMatrix bundle to the existing
large-cohort and heldout-oracle evidence packet, then runs promotion readiness
against the real bundle inputs. It does not mutate ProductWriter, default
matrix output, workbooks, GUI behavior, selected peaks/areas, or counted
detections.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.build_quant_matrix_promotion_validation_packet import (
    DEFAULT_COHORT_ID,
    DEFAULT_HELDOUT_ORACLE_ARTIFACT,
    DEFAULT_LARGE_COHORT_ARTIFACT,
    DEFAULT_ORACLE_PACKET_ID,
)
from scripts.build_quant_matrix_real_bundle import (
    DEFAULT_ACCEPTED_BACKFILL_COUNT,
    DEFAULT_DOWNSTREAM_SCOPE,
    DEFAULT_SOURCE_RUN_ID,
    validate_quant_matrix_real_bundle,
)
from scripts.build_quant_matrix_real_bundle import (
    DEFAULT_OUTPUT_DIR as DEFAULT_REAL_BUNDLE_DIR,
)
from scripts.build_quant_matrix_version import run_activation
from xic_extractor.diagnostics.quant_matrix.fixture_contract import (
    validate_fixture_contract,
)
from xic_extractor.diagnostics.quant_matrix.promotion import (
    evaluate_quant_matrix_promotion_readiness,
)
from xic_extractor.diagnostics.quant_matrix.validation_packet import (
    VALIDATION_EVIDENCE_ROW_COLUMNS,
    ValidationEvidenceArtifact,
    build_quant_matrix_validation_evidence_packet,
    validate_quant_matrix_validation_evidence_packet,
)
from xic_extractor.tabular_io import file_sha256, optional_int, write_tsv

ROOT = Path(__file__).resolve().parents[1]

PROMOTION_PACKET_SUMMARY_SCHEMA = "quant_matrix_promotion_packet_summary"
DEFAULT_OUTPUT_DIR = Path(
    "docs/superpowers/validation/quant_matrix_promotion_packet",
)
DEFAULT_PACKET_ID = "quant-matrix-promotion-packet"


def build_quant_matrix_promotion_packet(
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    source_root: Path = ROOT,
    real_bundle_summary_json: Path = (
        DEFAULT_REAL_BUNDLE_DIR / "quant_matrix_real_bundle_summary.json"
    ),
    large_cohort_artifact: Path = DEFAULT_LARGE_COHORT_ARTIFACT,
    heldout_oracle_artifact: Path = DEFAULT_HELDOUT_ORACLE_ARTIFACT,
    packet_id: str = DEFAULT_PACKET_ID,
    cohort_id: str = DEFAULT_COHORT_ID,
    raw_run_count: int = 85,
    oracle_packet_id: str = DEFAULT_ORACLE_PACKET_ID,
    requested_readiness_label: str = "production_ready",
    expected_source_run_id: str = DEFAULT_SOURCE_RUN_ID,
    expected_downstream_scope: str = DEFAULT_DOWNSTREAM_SCOPE,
    expected_accepted_backfill_count: int = DEFAULT_ACCEPTED_BACKFILL_COUNT,
) -> Mapping[str, Path]:
    real_bundle_summary = _resolve_source(
        real_bundle_summary_json,
        source_root=source_root,
    )
    problems = validate_quant_matrix_real_bundle(
        summary_json=real_bundle_summary,
        repo_root=source_root,
        expected_source_run_id=expected_source_run_id,
        expected_downstream_scope=expected_downstream_scope,
        expected_accepted_backfill_count=expected_accepted_backfill_count,
    )
    if problems:
        raise ValueError("real bundle check failed: " + "; ".join(problems))

    bundle_payload = _read_json_object(real_bundle_summary)
    bundle_paths = _real_bundle_paths(bundle_payload, real_bundle_summary)
    downstream_scope = str(bundle_payload.get("downstream_scope", "")).strip()

    output_dir.mkdir(parents=True, exist_ok=True)
    packet_outputs = build_quant_matrix_validation_evidence_packet(
        output_dir=output_dir,
        evidence_artifacts=[
            ValidationEvidenceArtifact(
                tier="full_cohort",
                status="pass",
                source_artifact=large_cohort_artifact,
                cohort_id=cohort_id,
                raw_run_count=raw_run_count,
                evidence_note=(
                    "Existing no-RAW FULL_COHORT consolidated standard-peak activation "
                    "input summary; large-cohort evidence only, not matrix "
                    "authority."
                ),
            ),
            ValidationEvidenceArtifact(
                tier="heldout_oracle",
                status="pass",
                source_artifact=heldout_oracle_artifact,
                oracle_packet_id=oracle_packet_id,
                evidence_note=(
                    "Existing 20-case heldout trace reintegration oracle smoke "
                    "pass; oracle evidence only, not truth authority."
                ),
            ),
            ValidationEvidenceArtifact(
                tier="downstream_impact_smoke",
                status="pass",
                source_artifact=_source_argument(
                    bundle_paths["downstream_impact_summary_json"],
                    source_root=source_root,
                ),
                downstream_scope=downstream_scope,
                evidence_note=(
                    "Real/candidate QuantMatrixVersion downstream-impact smoke "
                    "artifact bound by copied summary and row metrics hashes."
                ),
            ),
        ],
        packet_id=packet_id,
        requested_readiness_label=requested_readiness_label,
        source_root=source_root,
    )
    _localize_downstream_evidence_inputs(
        packet_outputs=packet_outputs,
        bundle_paths=bundle_paths,
    )

    readiness_dir = output_dir / "real_bundle_readiness"
    readiness_outputs = evaluate_quant_matrix_promotion_readiness(
        expected_diff_summary_tsv=bundle_paths["expected_diff_summary"],
        cell_provenance_tsv=bundle_paths["cell_provenance"],
        row_summary_tsv=bundle_paths["row_summary"],
        review_summary_json=bundle_paths["review_summary_json"],
        validation_evidence_json=packet_outputs["validation_evidence_json"],
        output_dir=readiness_dir,
        validation_artifact_root=source_root,
        cell_provenance_contract_summary=bundle_paths.get(
            "cell_provenance_summary",
        ),
        cell_provenance_minimal_fixture=bundle_paths.get(
            "cell_provenance_minimal_fixture",
        ),
    )
    _rewrite_readiness_input_paths(
        readiness_outputs["summary_json"],
        paths={
            "expected_diff_summary_tsv": bundle_paths["expected_diff_summary"],
            "cell_provenance_tsv": bundle_paths["cell_provenance"],
            "row_summary_tsv": bundle_paths["row_summary"],
            "review_summary_json": bundle_paths["review_summary_json"],
            "validation_evidence_json": packet_outputs["validation_evidence_json"],
        },
    )

    summary_json = output_dir / "quant_matrix_promotion_packet_summary.json"
    _write_summary(
        summary_json,
        output_dir=output_dir,
        source_root=source_root,
        packet_id=packet_id,
        real_bundle_summary=real_bundle_summary,
        bundle_payload=bundle_payload,
        large_cohort_artifact=_resolve_source(
            large_cohort_artifact,
            source_root=source_root,
        ),
        heldout_oracle_artifact=_resolve_source(
            heldout_oracle_artifact,
            source_root=source_root,
        ),
        packet_outputs=packet_outputs,
        readiness_outputs=readiness_outputs,
        requested_readiness_label=requested_readiness_label,
    )
    return {
        **packet_outputs,
        "real_bundle_readiness_summary_json": readiness_outputs["summary_json"],
        "real_bundle_readiness_checks_tsv": readiness_outputs["checks_tsv"],
        "summary_json": summary_json,
    }


def validate_quant_matrix_promotion_packet(
    *,
    summary_json: Path = DEFAULT_OUTPUT_DIR
    / "quant_matrix_promotion_packet_summary.json",
    source_root: Path = ROOT,
    expected_source_run_id: str = DEFAULT_SOURCE_RUN_ID,
    expected_downstream_scope: str = DEFAULT_DOWNSTREAM_SCOPE,
    expected_accepted_backfill_count: int = DEFAULT_ACCEPTED_BACKFILL_COUNT,
) -> list[str]:
    problems: list[str] = []
    try:
        payload = _read_json_object(summary_json)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [str(exc)]

    for field, expected_text in (
        ("schema_version", PROMOTION_PACKET_SUMMARY_SCHEMA),
        ("artifact_role", "promotion_candidate_packet"),
        ("status", "pass"),
        ("requested_readiness_label", "production_ready"),
        ("readiness_label", "production_ready_candidate_packet"),
    ):
        if payload.get(field) != expected_text:
            problems.append(f"promotion packet {field} mismatch")
    for field, expected_bool in (
        ("read_only", True),
        ("write_authority", False),
        ("scorer_ran", False),
        ("raw_or_full_cohort_ran", False),
        ("product_writer_changed", False),
        ("default_quant_matrix_changed", False),
        ("broad_backfill_unparked", False),
        ("production_ready", True),
        ("may_promote_default_quant_matrix", True),
    ):
        if payload.get(field) is not expected_bool:
            problems.append(
                f"promotion packet {field} must be {str(expected_bool).lower()}",
            )
    if (
        optional_int(payload.get("accepted_backfill_count", ""))
        != expected_accepted_backfill_count
    ):
        problems.append(
            "promotion packet accepted_backfill_count mismatch: "
            f"expected {expected_accepted_backfill_count}",
        )
    if payload.get("downstream_scope") != expected_downstream_scope:
        problems.append(
            "promotion packet downstream_scope mismatch: "
            f"expected {expected_downstream_scope}",
        )

    input_artifacts = _input_artifacts(
        payload,
        source_root=source_root,
        problems=problems,
    )
    artifacts = _output_artifacts(payload, summary_json.parent, problems)

    real_bundle_summary = input_artifacts.get("real_bundle_summary_json")
    bundle_paths: dict[str, Path] = {}
    if real_bundle_summary is not None:
        problems.extend(
            "real bundle: " + problem
            for problem in validate_quant_matrix_real_bundle(
                summary_json=real_bundle_summary,
                repo_root=source_root,
                expected_source_run_id=expected_source_run_id,
                expected_downstream_scope=expected_downstream_scope,
                expected_accepted_backfill_count=expected_accepted_backfill_count,
            )
        )
        try:
            bundle_payload = _read_json_object(real_bundle_summary)
            bundle_paths = _real_bundle_paths(bundle_payload, real_bundle_summary)
        except (OSError, ValueError, FileNotFoundError) as exc:
            problems.append(f"real bundle paths: {exc}")

    evidence_json = artifacts.get("validation_evidence_json")
    if evidence_json is not None:
        problems.extend(
            validate_quant_matrix_validation_evidence_packet(
                evidence_json,
                source_root=source_root,
                cell_provenance_contract_summary=bundle_paths.get(
                    "cell_provenance_summary",
                ),
                cell_provenance_minimal_fixture=bundle_paths.get(
                    "cell_provenance_minimal_fixture",
                ),
            )
        )

    readiness_summary = artifacts.get("real_bundle_readiness_summary_json")
    readiness_checks = artifacts.get("real_bundle_readiness_checks_tsv")
    if (
        real_bundle_summary is not None
        and evidence_json is not None
        and readiness_summary is not None
        and readiness_checks is not None
    ):
        _append_readiness_candidate_problems(
            readiness_summary,
            problems,
        )
        if (
            "cell_provenance" in bundle_paths
            or "cell_provenance_summary" in bundle_paths
        ):
            _append_readiness_rerun_problems(
                bundle_paths=bundle_paths,
                validation_evidence_json=evidence_json,
                readiness_summary=readiness_summary,
                readiness_checks=readiness_checks,
                source_root=source_root,
                problems=problems,
            )
        elif "cell_provenance_summary" not in bundle_paths:
            problems.append("promotion packet cell_provenance replacement missing")

    return problems


def resolve_quant_matrix_promotion_packet_real_bundle(
    summary_json: Path,
    *,
    source_root: Path = ROOT,
) -> Path:
    """Resolve the validated packet's direct real-bundle predecessor."""
    payload = _read_json_object(summary_json)
    raw = payload.get("input_artifacts")
    if not isinstance(raw, dict):
        raise ValueError("promotion packet input_artifacts must be an object")
    path_value = str(raw.get("real_bundle_summary_json", "")).strip()
    if not path_value:
        raise ValueError("promotion packet real_bundle_summary_json is missing")
    path = _resolve_source(Path(path_value), source_root=source_root)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    return path


def _write_summary(
    summary_json: Path,
    *,
    output_dir: Path,
    source_root: Path,
    packet_id: str,
    real_bundle_summary: Path,
    bundle_payload: Mapping[str, Any],
    large_cohort_artifact: Path,
    heldout_oracle_artifact: Path,
    packet_outputs: Mapping[str, Path],
    readiness_outputs: Mapping[str, Path],
    requested_readiness_label: str,
) -> None:
    readiness = _read_json_object(readiness_outputs["summary_json"])
    payload = {
        "schema_version": PROMOTION_PACKET_SUMMARY_SCHEMA,
        "artifact_role": "promotion_candidate_packet",
        "status": "pass",
        "packet_id": packet_id,
        "requested_readiness_label": requested_readiness_label,
        "read_only": True,
        "write_authority": False,
        "scorer_ran": False,
        "raw_or_full_cohort_ran": False,
        "product_writer_changed": False,
        "default_quant_matrix_changed": False,
        "broad_backfill_unparked": False,
        "source_run_id": bundle_payload.get("source_run_id", ""),
        "downstream_scope": bundle_payload.get("downstream_scope", ""),
        "accepted_backfill_count": bundle_payload.get("accepted_backfill_count", 0),
        "readiness_label": readiness.get("readiness_label", ""),
        "contract_correctness_status": readiness.get(
            "contract_correctness_status",
            "",
        ),
        "scientific_confidence_status": readiness.get(
            "scientific_confidence_status",
            "",
        ),
        "production_ready": readiness.get("production_ready", False),
        "may_promote_default_quant_matrix": readiness.get(
            "may_promote_default_quant_matrix",
            False,
        ),
        "missing_science_evidence": readiness.get("missing_science_evidence", []),
        "validation_tiers": readiness.get("validation_tiers", {}),
        "input_artifacts": {
            "real_bundle_summary_json": _source_relpath(
                real_bundle_summary,
                source_root=source_root,
            ),
            "real_bundle_summary_json_sha256": file_sha256(real_bundle_summary),
            "large_cohort_artifact": _source_relpath(
                large_cohort_artifact,
                source_root=source_root,
            ),
            "large_cohort_artifact_sha256": file_sha256(large_cohort_artifact),
            "heldout_oracle_artifact": _source_relpath(
                heldout_oracle_artifact,
                source_root=source_root,
            ),
            "heldout_oracle_artifact_sha256": file_sha256(heldout_oracle_artifact),
        },
        "artifacts": {
            "validation_evidence_json": _artifact_record(
                packet_outputs["validation_evidence_json"],
                base_dir=output_dir,
            ),
            "validation_evidence_rows_tsv": _artifact_record(
                packet_outputs["validation_evidence_rows_tsv"],
                base_dir=output_dir,
            ),
            "validation_evidence_summary_json": _artifact_record(
                packet_outputs["summary_json"],
                base_dir=output_dir,
            ),
            "real_bundle_readiness_summary_json": _artifact_record(
                readiness_outputs["summary_json"],
                base_dir=output_dir,
            ),
            "real_bundle_readiness_checks_tsv": _artifact_record(
                readiness_outputs["checks_tsv"],
                base_dir=output_dir,
            ),
        },
        "authority_statement": (
            "Promotion candidate packet is a Product Ready candidate packet "
            "only. It may promote a later default quant matrix activation gate, "
            "but this artifact itself is read-only and does not change "
            "ProductWriter defaults or matrix-writing authority."
        ),
    }
    summary_json.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _append_readiness_candidate_problems(
    readiness_summary: Path,
    problems: list[str],
) -> None:
    try:
        summary = _read_json_object(readiness_summary)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        problems.append(f"promotion packet readiness summary: {exc}")
        return
    for field, expected in (
        ("readiness_label", "production_ready_candidate_packet"),
        ("contract_correctness_status", "pass"),
        ("scientific_confidence_status", "pass"),
    ):
        if summary.get(field) != expected:
            problems.append(f"promotion packet readiness {field} mismatch")
    for field in ("production_ready", "may_promote_default_quant_matrix"):
        if summary.get(field) is not True:
            problems.append(f"promotion packet readiness {field} must be true")
    if summary.get("missing_science_evidence") != []:
        problems.append("promotion packet readiness missing_science_evidence")
    expected_tiers = {
        "full_cohort": "pass",
        "heldout_oracle": "pass",
        "downstream_impact_smoke": "pass",
    }
    if summary.get("validation_tiers") != expected_tiers:
        problems.append("promotion packet readiness validation_tiers mismatch")


def _append_readiness_rerun_problems(
    *,
    bundle_paths: Mapping[str, Path],
    validation_evidence_json: Path,
    readiness_summary: Path,
    readiness_checks: Path,
    source_root: Path,
    problems: list[str],
) -> None:
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            cell_provenance_tsv = _cell_provenance_for_readiness_rerun(
                bundle_paths=bundle_paths,
                source_root=source_root,
                tmpdir=tmpdir_path,
                problems=problems,
            )
            if cell_provenance_tsv is None:
                return
            rerun_validation_evidence_json = _validation_evidence_for_readiness_rerun(
                validation_evidence_json=validation_evidence_json,
                cell_provenance_tsv=cell_provenance_tsv,
                tmpdir=tmpdir_path,
            )
            outputs = evaluate_quant_matrix_promotion_readiness(
                expected_diff_summary_tsv=bundle_paths["expected_diff_summary"],
                cell_provenance_tsv=cell_provenance_tsv,
                row_summary_tsv=bundle_paths["row_summary"],
                review_summary_json=bundle_paths["review_summary_json"],
                validation_evidence_json=rerun_validation_evidence_json,
                output_dir=tmpdir_path / "readiness",
                validation_artifact_root=source_root,
                cell_provenance_contract_summary=bundle_paths.get(
                    "cell_provenance_summary",
                ),
                cell_provenance_minimal_fixture=bundle_paths.get(
                    "cell_provenance_minimal_fixture",
                ),
            )
            expected_summary = _json_without_input_artifacts(outputs["summary_json"])
            actual_summary = _json_without_input_artifacts(readiness_summary)
            if actual_summary != expected_summary:
                problems.append("promotion packet readiness summary is stale")
            expected_checks = outputs["checks_tsv"].read_text(encoding="utf-8")
            actual_checks = readiness_checks.read_text(encoding="utf-8")
            if actual_checks != expected_checks:
                problems.append("promotion packet readiness checks TSV is stale")
    except (OSError, ValueError) as exc:
        problems.append(f"promotion packet readiness rerun failed: {exc}")


def _cell_provenance_for_readiness_rerun(
    *,
    bundle_paths: Mapping[str, Path],
    source_root: Path,
    tmpdir: Path,
    problems: list[str],
) -> Path | None:
    existing = bundle_paths.get("cell_provenance")
    if existing is not None:
        return existing
    summary = bundle_paths.get("cell_provenance_summary")
    fixture = bundle_paths.get("cell_provenance_minimal_fixture")
    if summary is None or fixture is None:
        problems.append("promotion packet cell_provenance replacement missing")
        return None
    problems.extend(
        f"promotion packet cell_provenance contract: {problem}"
        for problem in validate_fixture_contract(summary, fixture)
    )
    payload = _read_json_object(summary)
    source_sha = str(payload.get("source_sha256", "")).upper()
    activation_outputs = dict(
        run_activation(
            input_quant_matrix_tsv=bundle_paths["baseline_quant_matrix"],
            input_matrix_identity_tsv=bundle_paths["input_matrix_identity"],
            production_acceptance_manifest_tsv=bundle_paths[
                "production_acceptance_manifest"
            ],
            expected_diff_tsv=bundle_paths["expected_diff"],
            output_dir=tmpdir / "activation_rerun",
            manifest_root=source_root,
        )
    )
    cell_provenance = activation_outputs["cell_provenance"]
    if file_sha256(cell_provenance) != source_sha:
        problems.append(
            "promotion packet cell_provenance does not match rerun activation",
        )
    return cell_provenance


def _validation_evidence_for_readiness_rerun(
    *,
    validation_evidence_json: Path,
    cell_provenance_tsv: Path,
    tmpdir: Path,
) -> Path:
    packet_dir = validation_evidence_json.parent
    rerun_dir = tmpdir / "validation_evidence"
    payload = _read_json_object(validation_evidence_json)
    rows = payload.get("evidence")
    if not isinstance(rows, list):
        raise ValueError("validation evidence rows must be a list")
    for raw_row in rows:
        if not isinstance(raw_row, dict):
            continue
        relpath = Path(str(raw_row.get("artifact_path", "")))
        if not relpath or relpath.is_absolute() or ".." in relpath.parts:
            raise ValueError("validation evidence artifact path invalid")
        source_artifact = packet_dir / relpath
        target_artifact = rerun_dir / relpath
        target_artifact.parent.mkdir(parents=True, exist_ok=True)
        target_artifact.write_bytes(source_artifact.read_bytes())
        if raw_row.get("tier") == "downstream_impact_smoke":
            downstream_payload = _read_json_object(target_artifact)
            _copy_downstream_row_metrics_for_rerun(
                source_summary=source_artifact,
                target_summary=target_artifact,
                payload=downstream_payload,
            )
            _copy_downstream_input_artifacts_for_rerun(
                source_summary=source_artifact,
                target_summary=target_artifact,
                payload=downstream_payload,
            )
            input_artifacts = downstream_payload.get("input_artifacts")
            if not isinstance(input_artifacts, dict):
                raise ValueError("downstream summary input_artifacts must be an object")
            input_artifacts["cell_provenance_tsv"] = str(
                cell_provenance_tsv.resolve(strict=True),
            )
            input_artifacts["cell_provenance_sha256"] = file_sha256(
                cell_provenance_tsv,
            )
            target_artifact.write_text(
                json.dumps(downstream_payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )
        raw_row["artifact_sha256"] = file_sha256(target_artifact)
    rerun_json = rerun_dir / validation_evidence_json.name
    rerun_json.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return rerun_json


def _copy_downstream_input_artifacts_for_rerun(
    *,
    source_summary: Path,
    target_summary: Path,
    payload: Mapping[str, Any],
) -> None:
    input_artifacts = payload.get("input_artifacts")
    if not isinstance(input_artifacts, dict):
        raise ValueError("downstream summary input_artifacts must be an object")
    for field in ("quant_matrix_tsv", "row_summary_tsv"):
        relpath = Path(str(input_artifacts.get(field, "")))
        if not relpath or relpath.is_absolute() or ".." in relpath.parts:
            raise ValueError(f"downstream summary {field} path invalid")
        source_path = source_summary.parent / relpath
        target_path = target_summary.parent / relpath
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(source_path.read_bytes())


def _copy_downstream_row_metrics_for_rerun(
    *,
    source_summary: Path,
    target_summary: Path,
    payload: Mapping[str, Any],
) -> None:
    relpath = Path(str(payload.get("row_metrics_tsv", "")))
    if not relpath or relpath.is_absolute() or ".." in relpath.parts:
        raise ValueError("downstream summary row_metrics_tsv path invalid")
    source_rows = source_summary.parent / relpath
    target_rows = target_summary.parent / relpath
    target_rows.parent.mkdir(parents=True, exist_ok=True)
    target_rows.write_bytes(source_rows.read_bytes())


def _localize_downstream_evidence_inputs(
    *,
    packet_outputs: Mapping[str, Path],
    bundle_paths: Mapping[str, Path],
) -> None:
    evidence_json = packet_outputs["validation_evidence_json"]
    packet_dir = evidence_json.parent
    payload = _read_json_object(evidence_json)
    rows = payload.get("evidence")
    if not isinstance(rows, list):
        raise ValueError("validation evidence rows must be a list")
    downstream_row: dict[str, Any] | None = None
    for raw_row in rows:
        if (
            isinstance(raw_row, dict)
            and raw_row.get("tier") == "downstream_impact_smoke"
        ):
            downstream_row = raw_row
            break
    if downstream_row is None:
        raise ValueError("downstream_impact_smoke evidence row is missing")
    copied_summary = (packet_dir / str(downstream_row["artifact_path"])).resolve(
        strict=False,
    )
    copied_payload = _read_json_object(copied_summary)
    inputs_dir = copied_summary.parent / "downstream_impact_inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    localized_inputs = {
        "quant_matrix_tsv": bundle_paths["quant_matrix"],
        "row_summary_tsv": bundle_paths["row_summary"],
    }
    input_artifacts = copied_payload.get("input_artifacts")
    if not isinstance(input_artifacts, dict):
        raise ValueError("downstream summary input_artifacts must be an object")
    for field, source in localized_inputs.items():
        destination = inputs_dir / source.name
        if source.resolve(strict=True) != destination.resolve(strict=False):
            shutil.copy2(source, destination)
        input_artifacts[field] = destination.relative_to(
            copied_summary.parent,
        ).as_posix()
        input_artifacts[field.replace("_tsv", "_sha256")] = file_sha256(destination)
    cell_source = bundle_paths.get("cell_provenance")
    if cell_source is not None:
        cell_destination = inputs_dir / cell_source.name
        input_artifacts["cell_provenance_tsv"] = cell_destination.relative_to(
            copied_summary.parent,
        ).as_posix()
        input_artifacts["cell_provenance_sha256"] = file_sha256(cell_source)
        if _is_default_output_dir(packet_dir):
            if cell_destination.exists():
                cell_destination.unlink()
        else:
            if cell_source.resolve(strict=True) != cell_destination.resolve(
                strict=False,
            ):
                shutil.copy2(cell_source, cell_destination)
    copied_summary.write_text(
        json.dumps(copied_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    downstream_row["artifact_sha256"] = file_sha256(copied_summary)
    evidence_json.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    normalized_rows = [
        {column: str(row.get(column, "")) for column in VALIDATION_EVIDENCE_ROW_COLUMNS}
        for row in rows
        if isinstance(row, dict)
    ]
    write_tsv(
        packet_outputs["validation_evidence_rows_tsv"],
        normalized_rows,
        VALIDATION_EVIDENCE_ROW_COLUMNS,
        extrasaction="raise",
        lineterminator="\n",
    )
    summary = _read_json_object(packet_outputs["summary_json"])
    summary["validation_evidence_json_sha256"] = file_sha256(evidence_json)
    summary["validation_evidence_rows_tsv_sha256"] = file_sha256(
        packet_outputs["validation_evidence_rows_tsv"],
    )
    packet_outputs["summary_json"].write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _is_default_output_dir(path: Path) -> bool:
    return path.resolve(strict=False) == (ROOT / DEFAULT_OUTPUT_DIR).resolve(
        strict=False,
    )


def _real_bundle_paths(
    payload: Mapping[str, Any],
    summary_json: Path,
) -> dict[str, Path]:
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("real bundle artifacts must be an object")
    output_dir = summary_json.parent
    labels = (
        "baseline_quant_matrix",
        "input_matrix_identity",
        "production_acceptance_manifest",
        "expected_diff",
        "quant_matrix",
        "expected_diff_summary",
        "row_summary",
        "review_summary_json",
        "downstream_impact_summary_json",
    )
    result: dict[str, Path] = {}
    for label in labels:
        raw_entry = artifacts.get(label)
        if not isinstance(raw_entry, dict):
            raise ValueError(f"real bundle {label} artifact is missing")
        relpath = str(raw_entry.get("path", "")).strip()
        if not relpath:
            raise ValueError(f"real bundle {label} path is missing")
        path = Path(relpath)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"real bundle {label} path must be bundle-relative")
        resolved = (output_dir / path).resolve(strict=False)
        try:
            resolved.relative_to(output_dir.resolve(strict=False))
        except ValueError as exc:
            raise ValueError(f"real bundle {label} path escapes bundle") from exc
        if not resolved.is_file():
            raise FileNotFoundError(str(resolved))
        result[label] = resolved
    _append_real_bundle_cell_provenance_paths(
        artifacts,
        output_dir=output_dir,
        result=result,
    )
    for label in ("cell_provenance_summary", "cell_provenance_minimal_fixture"):
        raw_entry = artifacts.get(label)
        if isinstance(raw_entry, dict):
            result[label] = _resolve_real_bundle_artifact(
                raw_entry,
                output_dir=output_dir,
                label=label,
            )
    return result


def _append_real_bundle_cell_provenance_paths(
    artifacts: Mapping[str, Any],
    *,
    output_dir: Path,
    result: dict[str, Path],
) -> None:
    raw_entry = artifacts.get("cell_provenance")
    if not isinstance(raw_entry, dict):
        raise ValueError("real bundle cell_provenance artifact is missing")
    relpath = str(raw_entry.get("path", "")).strip()
    if not relpath:
        raise ValueError("real bundle cell_provenance path is missing")
    path = Path(relpath)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("real bundle cell_provenance path must be bundle-relative")
    resolved = (output_dir / path).resolve(strict=False)
    try:
        resolved.relative_to(output_dir.resolve(strict=False))
    except ValueError as exc:
        raise ValueError("real bundle cell_provenance path escapes bundle") from exc
    if resolved.is_file():
        result["cell_provenance"] = resolved
        return
    if raw_entry.get("externalized") is not True:
        raise FileNotFoundError(str(resolved))
    externalized = _resolve_externalized_artifact(raw_entry)
    if externalized is not None and externalized.is_file():
        result["cell_provenance"] = externalized
        return
    summary_relpath = str(raw_entry.get("replacement_or_summary", "")).strip()
    if not summary_relpath:
        raise ValueError("real bundle cell_provenance replacement is missing")
    summary_path = _resolve_real_bundle_artifact(
        {"path": summary_relpath},
        output_dir=output_dir,
        label="cell_provenance_summary",
    )
    result["cell_provenance_summary"] = summary_path


def _resolve_externalized_artifact(raw_entry: Mapping[str, Any]) -> Path | None:
    relpath = str(raw_entry.get("externalized_path", "")).strip()
    if not relpath:
        return None
    path = Path(relpath)
    if path.is_absolute() or ".." in path.parts:
        return None
    return (ROOT / path).resolve(strict=False)


def _resolve_real_bundle_artifact(
    raw_entry: Mapping[str, Any],
    *,
    output_dir: Path,
    label: str,
) -> Path:
    relpath = str(raw_entry.get("path", "")).strip()
    if not relpath:
        raise ValueError(f"real bundle {label} path is missing")
    path = Path(relpath)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"real bundle {label} path must be bundle-relative")
    resolved = (output_dir / path).resolve(strict=False)
    try:
        resolved.relative_to(output_dir.resolve(strict=False))
    except ValueError as exc:
        raise ValueError(f"real bundle {label} path escapes bundle") from exc
    if not resolved.is_file():
        raise FileNotFoundError(str(resolved))
    return resolved


def _input_artifacts(
    payload: Mapping[str, Any],
    *,
    source_root: Path,
    problems: list[str],
) -> dict[str, Path]:
    raw = payload.get("input_artifacts")
    if not isinstance(raw, dict):
        problems.append("promotion packet input_artifacts must be an object")
        return {}
    result: dict[str, Path] = {}
    for field in (
        "real_bundle_summary_json",
        "large_cohort_artifact",
        "heldout_oracle_artifact",
    ):
        path_value = str(raw.get(field, "")).strip()
        sha_value = str(raw.get(f"{field}_sha256", "")).strip()
        if not path_value:
            problems.append(f"promotion packet {field} is missing")
            continue
        path = _resolve_source(Path(path_value), source_root=source_root)
        result[field] = path
        if not path.is_file():
            problems.append(f"promotion packet {field} does not exist")
            continue
        if not _is_sha256(sha_value) or file_sha256(path) != sha_value.upper():
            problems.append(f"promotion packet {field}_sha256 mismatch")
    return result


def _output_artifacts(
    payload: Mapping[str, Any],
    output_dir: Path,
    problems: list[str],
) -> dict[str, Path]:
    raw = payload.get("artifacts")
    if not isinstance(raw, dict):
        problems.append("promotion packet artifacts must be an object")
        return {}
    result: dict[str, Path] = {}
    for label, raw_entry in raw.items():
        if not isinstance(raw_entry, dict):
            problems.append(f"promotion packet {label} entry must be an object")
            continue
        relpath = str(raw_entry.get("path", "")).strip()
        sha256 = str(raw_entry.get("sha256", "")).strip()
        path = Path(relpath)
        if not relpath or path.is_absolute() or ".." in path.parts:
            problems.append(f"promotion packet {label} path must be output-relative")
            continue
        resolved = (output_dir / path).resolve(strict=False)
        try:
            resolved.relative_to(output_dir.resolve(strict=False))
        except ValueError:
            problems.append(f"promotion packet {label} path escapes output")
            continue
        result[str(label)] = resolved
        if not resolved.is_file():
            problems.append(f"promotion packet {label} does not exist")
            continue
        if not _is_sha256(sha256) or file_sha256(resolved) != sha256.upper():
            problems.append(f"promotion packet {label} sha256 mismatch")
    return result


def _rewrite_readiness_input_paths(
    summary_json: Path,
    *,
    paths: Mapping[str, Path],
) -> None:
    payload = _read_json_object(summary_json)
    input_artifacts = payload.get("input_artifacts")
    if not isinstance(input_artifacts, dict):
        raise ValueError("readiness summary input_artifacts must be an object")
    for field, path in paths.items():
        input_artifacts[field] = _display_path(path, base_dir=summary_json.parent)
    summary_json.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _artifact_record(path: Path, *, base_dir: Path) -> dict[str, str]:
    return {
        "path": path.resolve(strict=False)
        .relative_to(
            base_dir.resolve(strict=False),
        )
        .as_posix(),
        "sha256": file_sha256(path),
    }


def _source_argument(path: Path, *, source_root: Path) -> Path:
    try:
        return path.resolve(strict=True).relative_to(
            source_root.resolve(strict=True),
        )
    except ValueError:
        return path


def _source_relpath(path: Path, *, source_root: Path) -> str:
    try:
        return (
            path.resolve(strict=True)
            .relative_to(
                source_root.resolve(strict=True),
            )
            .as_posix()
        )
    except ValueError:
        return str(path.resolve(strict=True))


def _resolve_source(path: Path, *, source_root: Path) -> Path:
    return path if path.is_absolute() else source_root / path


def _display_path(path: Path, *, base_dir: Path) -> str:
    return Path(os.path.relpath(path.resolve(strict=False), base_dir)).as_posix()


def _json_without_input_artifacts(path: Path) -> object:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = dict(data)
        data.pop("input_artifacts", None)
    return data


def _read_json_object(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected JSON object")
    return data


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(
        character in "0123456789abcdefABCDEF" for character in value
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.check_only:
        summary_json = args.summary_json or (
            args.output_dir / "quant_matrix_promotion_packet_summary.json"
        )
        problems = validate_quant_matrix_promotion_packet(
            summary_json=summary_json,
            source_root=args.source_root,
            expected_source_run_id=args.expected_source_run_id,
            expected_downstream_scope=args.expected_downstream_scope,
            expected_accepted_backfill_count=args.expected_accepted_backfill_count,
        )
        if problems:
            for problem in problems:
                print(problem, file=sys.stderr)
            return 1
        print(f"promotion_packet_summary_json: {summary_json}")
        print("promotion_packet_status: pass")
        return 0
    try:
        outputs = build_quant_matrix_promotion_packet(
            output_dir=args.output_dir,
            source_root=args.source_root,
            real_bundle_summary_json=args.real_bundle_summary_json,
            large_cohort_artifact=args.large_cohort_artifact,
            heldout_oracle_artifact=args.heldout_oracle_artifact,
            packet_id=args.packet_id,
            cohort_id=args.cohort_id,
            raw_run_count=args.raw_run_count,
            oracle_packet_id=args.oracle_packet_id,
            requested_readiness_label=args.requested_readiness_label,
            expected_source_run_id=args.expected_source_run_id,
            expected_downstream_scope=args.expected_downstream_scope,
            expected_accepted_backfill_count=args.expected_accepted_backfill_count,
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    for label, path in outputs.items():
        print(f"{label}: {path}")
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument(
        "--real-bundle-summary-json",
        type=Path,
        default=DEFAULT_REAL_BUNDLE_DIR / "quant_matrix_real_bundle_summary.json",
    )
    parser.add_argument(
        "--large-cohort-artifact",
        type=Path,
        default=DEFAULT_LARGE_COHORT_ARTIFACT,
    )
    parser.add_argument(
        "--heldout-oracle-artifact",
        type=Path,
        default=DEFAULT_HELDOUT_ORACLE_ARTIFACT,
    )
    parser.add_argument("--packet-id", default=DEFAULT_PACKET_ID)
    parser.add_argument("--cohort-id", default=DEFAULT_COHORT_ID)
    parser.add_argument("--raw-run-count", type=int, default=85)
    parser.add_argument("--oracle-packet-id", default=DEFAULT_ORACLE_PACKET_ID)
    parser.add_argument("--requested-readiness-label", default="production_ready")
    parser.add_argument("--expected-source-run-id", default=DEFAULT_SOURCE_RUN_ID)
    parser.add_argument(
        "--expected-downstream-scope",
        default=DEFAULT_DOWNSTREAM_SCOPE,
    )
    parser.add_argument(
        "--expected-accepted-backfill-count",
        type=int,
        default=DEFAULT_ACCEPTED_BACKFILL_COUNT,
    )
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--summary-json", type=Path)
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())

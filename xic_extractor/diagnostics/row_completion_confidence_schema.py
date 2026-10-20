"""Contracts for row-completion confidence diagnostics."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from xic_extractor.tabular_io import file_sha256, read_tsv_with_header, text_value

SCHEMA_VERSION = "row_completion_confidence"
NO_AUTHORITY_STATEMENT = (
    "This row-completion confidence report is diagnostic_only. It does not "
    "change matrix authority, ProductWriter authority, selected values, "
    "counted detections, workbook/TSV product schemas, control-plane/status "
    "index, active lane, maturity tier, or default preset behavior."
)
PRODUCT_GATE_NO_AUTHORITY_STATEMENT = (
    "This row-completion confidence report is a baseline-bound shadow gate. It "
    "can block product-gate eligibility, but it does not change matrix "
    "authority, ProductWriter authority, selected values, counted detections, "
    "workbook/TSV product schemas, control-plane/status index, active lane, "
    "maturity tier, or default preset behavior."
)

Status = Literal[
    "PASS",
    "WARN",
    "FAIL",
    "INCONCLUSIVE",
    "not_available",
    "schema_valid",
]
RequiredAction = Literal[
    "no_rerun",
    "artifact_only_rerun",
    "fresh_fixture_cohort_required",
    "fresh_full_cohort_required_after_fixture_cohort",
    "inconclusive",
]
MappingStatus = Literal[
    "not_available",
    "schema_valid_mapping_quality_unavailable",
    "FAIL",
    "INCONCLUSIVE",
]
MissingEvidenceCode = Literal[
    "",
    "missing_required_artifact",
    "missing_required_column",
    "unknown_schema_version",
    "stale_artifact_manifest",
    "baseline_current_unbound",
    "metric_source_unavailable",
    "canonical_panel_case_unbound",
    "external_mapping_quality_failed",
    "manual_review_required",
    "product_gate_required",
]

SUMMARY_COLUMNS = (
    "schema_version",
    "run_id",
    "lane",
    "metric_name",
    "status",
    "current_value",
    "baseline_value",
    "delta",
    "direction",
    "evidence_source",
    "artifact_relpath",
    "artifact_sha256",
    "reason",
    "missing_evidence_code",
    "input_artifact_manifest",
    "no_authority_statement",
)
SENTINEL_COLUMNS = (
    "schema_version",
    "run_id",
    "rank",
    "case_id",
    "lane",
    "case_type",
    "feature_family_id",
    "sample_stem",
    "production_safety_status",
    "review_utility_status",
    "issue_class",
    "severity_score",
    "evidence_source",
    "recommended_action",
    "requires_manual_review",
    "reason",
)
DISAGREEMENT_COLUMNS = (
    "schema_version",
    "run_id",
    "disagreement_id",
    "external_tool",
    "external_run_id",
    "mapping_status",
    "sample_id",
    "sample_stem",
    "feature_family_id",
    "external_feature_id",
    "mz_delta",
    "rt_delta_min",
    "classification",
    "reason",
)

__all__ = [
    "ArtifactDescriptor",
    "ArtifactManifest",
    "DISAGREEMENT_COLUMNS",
    "FreshnessDecision",
    "MappingStatus",
    "ManifestValidationResult",
    "MissingEvidenceCode",
    "NO_AUTHORITY_STATEMENT",
    "PRODUCT_GATE_NO_AUTHORITY_STATEMENT",
    "RequiredAction",
    "SCHEMA_VERSION",
    "SENTINEL_COLUMNS",
    "Status",
    "SUMMARY_COLUMNS",
    "artifact_descriptor",
    "build_artifact_manifest",
    "freshness_decision",
]


@dataclass(frozen=True)
class ArtifactDescriptor:
    schema_version: str
    run_id: str
    path: str
    relpath: str
    size_bytes: int
    sha256: str
    row_count: int
    generation_context: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ArtifactManifest:
    schema_version: str
    run_id: str
    generation_context: str
    artifacts: tuple[ArtifactDescriptor, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ManifestValidationResult:
    run_ok: bool
    gate_ok: bool
    status: Status
    reason: str
    missing_evidence_code: MissingEvidenceCode
    manifest: ArtifactManifest | None


@dataclass(frozen=True)
class FreshnessDecision:
    change_class: str
    required_action: RequiredAction
    reason: str


def artifact_descriptor(
    path: Path,
    *,
    root: Path,
    run_id: str,
    generation_context: str,
) -> ArtifactDescriptor:
    resolved = path.resolve()
    _, rows = read_tsv_with_header(resolved, encoding="utf-8-sig")
    relpath = _relpath(resolved, root=root)
    return ArtifactDescriptor(
        schema_version=SCHEMA_VERSION,
        run_id=text_value(run_id),
        path=str(resolved),
        relpath=relpath,
        size_bytes=resolved.stat().st_size,
        sha256=file_sha256(resolved),
        row_count=len(rows),
        generation_context=text_value(generation_context),
    )


def build_artifact_manifest(
    required_paths: Mapping[str, Path],
    *,
    root: Path,
    run_id: str,
    generation_context: str,
) -> ManifestValidationResult:
    run_id_text = text_value(run_id)
    generation_context_text = text_value(generation_context)
    if not required_paths:
        return ManifestValidationResult(
            run_ok=False,
            gate_ok=False,
            status="INCONCLUSIVE",
            reason="required artifact set is empty",
            missing_evidence_code="missing_required_artifact",
            manifest=None,
        )
    if not run_id_text or generation_context_text.lower() == "unknown":
        return ManifestValidationResult(
            run_ok=False,
            gate_ok=False,
            status="INCONCLUSIVE",
            reason="run_id and known generation_context are required",
            missing_evidence_code="stale_artifact_manifest",
            manifest=None,
        )

    descriptors: list[ArtifactDescriptor] = []
    for label, path in required_paths.items():
        resolved = Path(path)
        if not resolved.is_file():
            return ManifestValidationResult(
                run_ok=False,
                gate_ok=False,
                status="INCONCLUSIVE",
                reason=f"{label}: missing required artifact {resolved}",
                missing_evidence_code="missing_required_artifact",
                manifest=None,
            )
        try:
            descriptors.append(
                artifact_descriptor(
                    resolved,
                    root=root,
                    run_id=run_id_text,
                    generation_context=generation_context_text,
                ),
            )
        except ValueError as exc:
            return ManifestValidationResult(
                run_ok=False,
                gate_ok=False,
                status="INCONCLUSIVE",
                reason=f"{label}: {exc}",
                missing_evidence_code="stale_artifact_manifest",
                manifest=None,
            )

    return ManifestValidationResult(
        run_ok=True,
        gate_ok=True,
        status="PASS",
        reason="required artifacts are manifest-bound and root-bound",
        missing_evidence_code="",
        manifest=ArtifactManifest(
            schema_version=SCHEMA_VERSION,
            run_id=run_id_text,
            generation_context=generation_context_text,
            artifacts=tuple(descriptors),
        ),
    )


def freshness_decision(change_class: str) -> FreshnessDecision:
    normalized = text_value(change_class).lower()
    if normalized in {"docs_only", "report_wording", "spec_only"}:
        return FreshnessDecision(
            change_class=change_class,
            required_action="no_rerun",
            reason="no generator changed",
        )
    if normalized in {
        "benchmark_reader",
        "benchmark_metric_logic",
        "benchmark_schema",
        "benchmark_join_logic",
        "canonical_panel_mapping",
        "fail_closed_logic",
    }:
        return FreshnessDecision(
            change_class=change_class,
            required_action="artifact_only_rerun",
            reason="only benchmark interpretation changed",
        )
    if normalized in {
        "alignment_generation_code",
        "discovery_generation_code",
        "extraction_generation_code",
        "backfill_generation_code",
        "scoring_generation_code",
        "matrix_writer_generation_code",
        "publication_check_generation_code",
        "config_or_input_universe",
    }:
        return FreshnessDecision(
            change_class=change_class,
            required_action="fresh_fixture_cohort_required",
            reason="artifact generation behavior or input universe changed",
        )
    if normalized in {
        "product_gate_packet",
        "maturity_tier_change",
        "writer_authority_change",
        "selected_value_or_counting_change",
        "full_cohort_stress_claim",
    }:
        return FreshnessDecision(
            change_class=change_class,
            required_action="fresh_full_cohort_required_after_fixture_cohort",
            reason="large-sample product or stress evidence is claimed",
        )
    return FreshnessDecision(
        change_class=change_class,
        required_action="inconclusive",
        reason="unknown change class",
    )


def _relpath(path: Path, *, root: Path) -> str:
    resolved_root = root.resolve()
    try:
        return path.relative_to(resolved_root).as_posix()
    except ValueError as exc:
        raise ValueError(f"{path} is outside root {resolved_root}") from exc

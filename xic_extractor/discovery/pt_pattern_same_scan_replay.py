"""Bounded replay of Pt same-scan evidence against current hypotheses."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.discovery.pt_pattern_same_scan_evidence import (
    CandidateFeatureClaims,
    PtPatternSeed,
    ResolvedConflictComponent,
    SameScanEnvelopeEvidence,
    SameScanSettings,
    build_conflict_components,
    evaluate_same_scan_envelope,
    resolve_conflict_components,
)
from xic_extractor.discovery.pt_pattern_same_scan_report import (
    render_same_scan_replay_html,
)
from xic_extractor.ms1_scan_index import (
    MS1Scan,
    MS1ScanIndexProvenance,
    validate_ms1_scan_index,
)
from xic_extractor.numeric_validation import require_finite_real
from xic_extractor.tabular_io import (
    file_sha256,
    optional_float,
    read_delimited_rows,
    text_value,
    write_delimited_rows,
)

SCHEMA_VERSION = "pt_pattern_same_scan_replay.v2"
READINESS_TIER = "diagnostic_only"
EVIDENCE_OWNER = "xic_extractor.discovery.pt_pattern_same_scan_evidence"
D3_CONTROL_NAME = "d3-dG-C8-MeIQx"
REFERENCE_CP_NAME = "195CP-d(GpG)"

_CANDIDATE_COLUMNS = (
    "sample_stem",
    "candidate_id",
    "base_mz",
    "base_rt_apex_min",
    "base_feature_id",
    "pt_plus_1_feature_id",
    "pt_plus_2_feature_id",
    "pt_plus_4_feature_id",
)
_FEATURE_ID_COLUMNS = (
    "base_feature_id",
    "pt_plus_1_feature_id",
    "pt_plus_2_feature_id",
    "pt_plus_4_feature_id",
)
_EVIDENCE_INPUT_COLUMNS = ("candidate_id", "trace_rule_call")
_KNOWN_COLUMNS = ("compound", "expected_base_mz", "diagnostic_status")
EVIDENCE_COLUMNS = (
    "schema_version",
    "readiness_tier",
    "evidence_owner",
    "report_id",
    "seed_id",
    "source_set",
    "expected_class",
    "compound",
    "candidate_id",
    "base_mz",
    "rt_center_min",
    "contract_a_current_feature_accept",
    "contract_b_strict_full_same_scan",
    "contract_c_core_plus4_conditional",
    "selected_scan_count",
    "core_centroid_scan_count",
    "full_centroid_scan_count",
    "core_above_noise_scan_count",
    "full_above_noise_scan_count",
    "longest_consecutive_core_scans",
    "longest_consecutive_full_scans",
    "plus4_support_status",
    "shared_apex_scan_number",
    "shared_apex_rt_min",
    "shared_apex_base_over_plus1",
    "shared_apex_plus2_over_plus1",
    "shared_apex_plus4_over_plus1",
    "core_sum_base_over_plus1",
    "core_sum_plus2_over_plus1",
    "core_sum_plus4_over_plus1",
    "core_min_pairwise_pearson",
    "median_core_ppm_spread",
    "median_full_ppm_spread",
    "conflict_component_id",
    "conflict_resolution_status",
    "conflict_preferred_candidate_id",
)
OBSERVATION_COLUMNS = (
    "report_id",
    "seed_id",
    "source_set",
    "scan_number",
    "rt_min",
    "core_centroid_present",
    "full_centroid_present",
    "core_above_noise",
    "full_above_noise",
    "base_observed_mz",
    "base_intensity",
    "base_signed_ppm_error",
    "pt_plus_1_observed_mz",
    "pt_plus_1_intensity",
    "pt_plus_1_signed_ppm_error",
    "pt_plus_2_observed_mz",
    "pt_plus_2_intensity",
    "pt_plus_2_signed_ppm_error",
    "pt_plus_4_observed_mz",
    "pt_plus_4_intensity",
    "pt_plus_4_signed_ppm_error",
)
CONFLICT_COLUMNS = (
    "report_id",
    "component_id",
    "candidate_count",
    "candidate_ids",
    "shared_feature_count",
    "shared_feature_ids",
    "strict_supported_candidate_ids",
    "core_supported_candidate_ids",
    "resolution_status",
    "preferred_candidate_id",
)


@dataclass(frozen=True, slots=True)
class _SeedContext:
    seed: PtPatternSeed
    expected_class: str
    compound: str
    candidate_id: str
    current_feature_accept: bool
    feature_ids: tuple[str, ...]


def run_same_scan_replay(
    *,
    index: Sequence[MS1Scan],
    candidates_csv: Path,
    evidence_csv: Path,
    known_diagnosis_csv: Path,
    output_dir: Path,
    report_id: str,
    source_sample_stem: str,
    index_input: Path,
    settings: SameScanSettings = SameScanSettings(),
    candidate_rt_half_window_sec: float = 45.0,
    index_source: str = "provided_index",
    index_provenance: MS1ScanIndexProvenance | None = None,
) -> dict[str, Path]:
    """Compare feature-first and same-scan contracts on bounded seed sets."""

    if not report_id.strip():
        raise ValueError("report_id must be non-empty")
    candidate_window = require_finite_real(
        candidate_rt_half_window_sec,
        name="candidate_rt_half_window_sec",
    )
    if candidate_window <= 0:
        raise ValueError("candidate_rt_half_window_sec must be finite and positive")
    validate_ms1_scan_index(index, require_nonempty=True)
    contexts = (
        *_candidate_contexts(
            candidates_csv,
            evidence_csv,
            rt_half_window_sec=candidate_rt_half_window_sec,
            expected_sample_stem=source_sample_stem,
        ),
        *_known_contexts(known_diagnosis_csv),
    )
    evaluations = tuple(
        evaluate_same_scan_envelope(index, context.seed, settings)
        for context in contexts
    )
    evaluation_by_seed = {
        evaluation.seed.seed_id: evaluation for evaluation in evaluations
    }
    candidate_contexts = tuple(context for context in contexts if context.candidate_id)
    components = build_conflict_components(
        tuple(
            CandidateFeatureClaims(context.candidate_id, context.feature_ids)
            for context in candidate_contexts
        )
    )
    resolved = resolve_conflict_components(
        components,
        strict_support_by_candidate={
            context.candidate_id: evaluation_by_seed[
                context.seed.seed_id
            ].strict_full_supported
            for context in candidate_contexts
        },
        core_support_by_candidate={
            context.candidate_id: evaluation_by_seed[
                context.seed.seed_id
            ].core_plus4_conditional_supported
            for context in candidate_contexts
        },
    )
    conflict_by_candidate = {
        candidate_id: item for item in resolved for candidate_id in item.candidate_ids
    }
    evidence_rows = [
        _evidence_row(
            context,
            evaluation_by_seed[context.seed.seed_id],
            conflict_by_candidate.get(context.candidate_id),
            report_id=report_id,
        )
        for context in contexts
    ]
    observation_rows = [
        _observation_row(evaluation, observation, report_id=report_id)
        for evaluation in evaluations
        for observation in evaluation.observations
    ]
    conflict_rows = [
        _conflict_row(
            item,
            evaluation_by_seed=evaluation_by_seed,
            report_id=report_id,
        )
        for item in resolved
    ]
    summary = _summary(
        evidence_rows,
        conflict_rows,
        index=index,
        settings=settings,
        report_id=report_id,
        index_source=index_source,
        candidate_rt_half_window_sec=candidate_rt_half_window_sec,
        inputs=(candidates_csv, evidence_csv, known_diagnosis_csv),
        source_sample_stem=source_sample_stem,
        index_input=index_input,
        index_provenance=index_provenance,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "evidence_csv": output_dir / "pt_pattern_same_scan_evidence.csv",
        "observations_csv": output_dir / "pt_pattern_same_scan_observations.csv",
        "conflicts_csv": output_dir / "pt_pattern_conflict_components.csv",
        "summary_json": output_dir / "pt_pattern_same_scan_summary.json",
        "html": output_dir / "pt_pattern_same_scan_report.html",
    }
    write_delimited_rows(outputs["evidence_csv"], evidence_rows, EVIDENCE_COLUMNS)
    write_delimited_rows(
        outputs["observations_csv"],
        observation_rows,
        OBSERVATION_COLUMNS,
    )
    write_delimited_rows(outputs["conflicts_csv"], conflict_rows, CONFLICT_COLUMNS)
    outputs["summary_json"].write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    outputs["html"].write_text(
        render_same_scan_replay_html(
            summary=summary,
            evidence_rows=evidence_rows,
            conflict_rows=conflict_rows,
        ),
        encoding="utf-8",
    )
    return outputs


def _candidate_contexts(
    candidates_csv: Path,
    evidence_csv: Path,
    *,
    rt_half_window_sec: float,
    expected_sample_stem: str | None = None,
) -> tuple[_SeedContext, ...]:
    candidates = read_delimited_rows(
        candidates_csv,
        required_columns=_CANDIDATE_COLUMNS,
        delimiter=",",
        encoding="utf-8-sig",
    )
    evidence = read_delimited_rows(
        evidence_csv,
        required_columns=_EVIDENCE_INPUT_COLUMNS,
        delimiter=",",
        encoding="utf-8-sig",
    )
    accepted_id_rows = [
        text_value(row["candidate_id"])
        for row in evidence
        if text_value(row["trace_rule_call"]).lower() == "accept"
    ]
    if any(not candidate_id for candidate_id in accepted_id_rows):
        raise ValueError("accepted evidence candidate_id must be non-empty")
    accepted_counts = Counter(accepted_id_rows)
    duplicate_evidence_ids = sorted(
        candidate_id for candidate_id, count in accepted_counts.items() if count > 1
    )
    if duplicate_evidence_ids:
        raise ValueError(
            "duplicate accepted candidate_id values in evidence CSV: "
            + ", ".join(duplicate_evidence_ids)
        )
    accepted_ids = set(accepted_id_rows)
    if expected_sample_stem is not None:
        expected = expected_sample_stem.strip()
        if not expected:
            raise ValueError("source sample stem must be non-empty")
        accepted_sample_stems = sorted(
            {
                text_value(row["sample_stem"])
                for row in candidates
                if text_value(row["candidate_id"]) in accepted_ids
            }
        )
        if accepted_sample_stems and accepted_sample_stems != [expected]:
            raise ValueError(
                f"candidate sample stems {accepted_sample_stems!r} do not match "
                f"source sample {expected!r}"
            )
    candidate_counts = Counter(
        text_value(row["candidate_id"])
        for row in candidates
        if text_value(row["candidate_id"]) in accepted_ids
    )
    missing_ids = sorted(accepted_ids - candidate_counts.keys())
    duplicate_candidate_ids = sorted(
        candidate_id for candidate_id, count in candidate_counts.items() if count > 1
    )
    if missing_ids or duplicate_candidate_ids:
        problems = []
        if missing_ids:
            problems.append("missing accepted ids: " + ", ".join(missing_ids))
        if duplicate_candidate_ids:
            problems.append(
                "duplicate accepted ids: " + ", ".join(duplicate_candidate_ids)
            )
        raise ValueError(
            "accepted candidate join must be exactly one-to-one; " + "; ".join(problems)
        )

    contexts = []
    for row in candidates:
        candidate_id = text_value(row["candidate_id"])
        if candidate_id not in accepted_ids:
            continue
        base_mz = _required_float(row["base_mz"], "base_mz", candidate_id)
        rt_min = _required_float(
            row["base_rt_apex_min"],
            "base_rt_apex_min",
            candidate_id,
        )
        feature_ids = tuple(
            text_value(row[column])
            for column in _FEATURE_ID_COLUMNS
            if text_value(row[column])
        )
        contexts.append(
            _SeedContext(
                seed=PtPatternSeed(
                    seed_id=candidate_id,
                    source_set="current_feature_hypothesis",
                    base_mz=base_mz,
                    rt_center_min=rt_min,
                    rt_half_window_sec=rt_half_window_sec,
                ),
                expected_class="unlabeled_current_hypothesis",
                compound="",
                candidate_id=candidate_id,
                current_feature_accept=True,
                feature_ids=feature_ids,
            )
        )
    return tuple(contexts)


def _known_contexts(path: Path) -> tuple[_SeedContext, ...]:
    rows = read_delimited_rows(
        path,
        required_columns=_KNOWN_COLUMNS,
        delimiter=",",
        encoding="utf-8-sig",
    )
    contexts = []
    for row in rows:
        compound = text_value(row["compound"])
        if compound == D3_CONTROL_NAME:
            expected_class = "non_pt_control"
        elif "CP" in compound:
            expected_class = "expected_positive"
        else:
            expected_class = "excluded_unknown"
        contexts.append(
            _SeedContext(
                seed=PtPatternSeed(
                    seed_id=f"known:{compound}",
                    source_set="known_named",
                    base_mz=_required_float(
                        row["expected_base_mz"],
                        "expected_base_mz",
                        compound,
                    ),
                ),
                expected_class=expected_class,
                compound=compound,
                candidate_id="",
                current_feature_accept=(
                    text_value(row["diagnostic_status"]).split(";", 1)[0].strip()
                    == "accepted_candidate"
                ),
                feature_ids=(),
            )
        )
    return tuple(contexts)


def _evidence_row(
    context: _SeedContext,
    evaluation: SameScanEnvelopeEvidence,
    conflict: ResolvedConflictComponent | None,
    *,
    report_id: str,
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "readiness_tier": READINESS_TIER,
        "evidence_owner": EVIDENCE_OWNER,
        "report_id": report_id,
        "seed_id": context.seed.seed_id,
        "source_set": context.seed.source_set,
        "expected_class": context.expected_class,
        "compound": context.compound,
        "candidate_id": context.candidate_id,
        "base_mz": context.seed.base_mz,
        "rt_center_min": context.seed.rt_center_min,
        "contract_a_current_feature_accept": context.current_feature_accept,
        "contract_b_strict_full_same_scan": evaluation.strict_full_supported,
        "contract_c_core_plus4_conditional": (
            evaluation.core_plus4_conditional_supported
        ),
        "selected_scan_count": evaluation.selected_scan_count,
        "core_centroid_scan_count": evaluation.core_centroid_scan_count,
        "full_centroid_scan_count": evaluation.full_centroid_scan_count,
        "core_above_noise_scan_count": evaluation.core_above_noise_scan_count,
        "full_above_noise_scan_count": evaluation.full_above_noise_scan_count,
        "longest_consecutive_core_scans": (evaluation.longest_consecutive_core_scans),
        "longest_consecutive_full_scans": (evaluation.longest_consecutive_full_scans),
        "plus4_support_status": evaluation.plus4_support_status,
        "shared_apex_scan_number": evaluation.shared_apex_scan_number,
        "shared_apex_rt_min": evaluation.shared_apex_rt_min,
        "shared_apex_base_over_plus1": evaluation.shared_apex_base_over_plus1,
        "shared_apex_plus2_over_plus1": evaluation.shared_apex_plus2_over_plus1,
        "shared_apex_plus4_over_plus1": evaluation.shared_apex_plus4_over_plus1,
        "core_sum_base_over_plus1": evaluation.core_sum_base_over_plus1,
        "core_sum_plus2_over_plus1": evaluation.core_sum_plus2_over_plus1,
        "core_sum_plus4_over_plus1": evaluation.core_sum_plus4_over_plus1,
        "core_min_pairwise_pearson": evaluation.core_min_pairwise_pearson,
        "median_core_ppm_spread": evaluation.median_core_ppm_spread,
        "median_full_ppm_spread": evaluation.median_full_ppm_spread,
        "conflict_component_id": conflict.component_id if conflict else "",
        "conflict_resolution_status": (
            conflict.resolution_status if conflict else "not_in_conflict"
        ),
        "conflict_preferred_candidate_id": (
            conflict.preferred_candidate_id if conflict else ""
        ),
    }


def _observation_row(
    evaluation: SameScanEnvelopeEvidence,
    observation,
    *,
    report_id: str,
) -> dict[str, object]:
    row: dict[str, object] = {
        "report_id": report_id,
        "seed_id": evaluation.seed.seed_id,
        "source_set": evaluation.seed.source_set,
        "scan_number": observation.scan_number,
        "rt_min": observation.rt_min,
        "core_centroid_present": observation.core_centroid_present,
        "full_centroid_present": observation.full_centroid_present,
        "core_above_noise": observation.core_above_noise,
        "full_above_noise": observation.full_above_noise,
    }
    for label in ("base", "pt_plus_1", "pt_plus_2", "pt_plus_4"):
        peak = observation.peaks.get(label)
        row[f"{label}_observed_mz"] = peak.mz if peak else None
        row[f"{label}_intensity"] = peak.intensity if peak else None
        row[f"{label}_signed_ppm_error"] = peak.signed_ppm_error if peak else None
    return row


def _conflict_row(
    resolved: ResolvedConflictComponent,
    *,
    evaluation_by_seed: Mapping[str, SameScanEnvelopeEvidence],
    report_id: str,
) -> dict[str, object]:
    component = resolved.component
    strict = tuple(
        candidate_id
        for candidate_id in component.candidate_ids
        if evaluation_by_seed[candidate_id].strict_full_supported
    )
    core = tuple(
        candidate_id
        for candidate_id in component.candidate_ids
        if evaluation_by_seed[candidate_id].core_plus4_conditional_supported
    )
    return {
        "report_id": report_id,
        "component_id": component.component_id,
        "candidate_count": len(component.candidate_ids),
        "candidate_ids": ";".join(component.candidate_ids),
        "shared_feature_count": len(component.shared_feature_ids),
        "shared_feature_ids": ";".join(component.shared_feature_ids),
        "strict_supported_candidate_ids": ";".join(strict),
        "core_supported_candidate_ids": ";".join(core),
        "resolution_status": resolved.resolution_status,
        "preferred_candidate_id": resolved.preferred_candidate_id,
    }


def _summary(
    evidence_rows: Sequence[Mapping[str, object]],
    conflict_rows: Sequence[Mapping[str, object]],
    *,
    index: Sequence[MS1Scan],
    settings: SameScanSettings,
    report_id: str,
    index_source: str,
    candidate_rt_half_window_sec: float,
    inputs: Sequence[Path],
    source_sample_stem: str = "",
    index_input: Path | None = None,
    index_provenance: MS1ScanIndexProvenance | None = None,
) -> dict[str, object]:
    current = [
        row
        for row in evidence_rows
        if text_value(row["source_set"]) == "current_feature_hypothesis"
    ]
    cp_rows = [
        row
        for row in evidence_rows
        if text_value(row["expected_class"]) == "expected_positive"
    ]
    d3 = next(
        (
            row
            for row in evidence_rows
            if text_value(row["expected_class"]) == "non_pt_control"
        ),
        None,
    )
    conflict_counts = Counter(
        text_value(row["resolution_status"]) for row in conflict_rows
    )
    discovery_resolved_statuses = {"unique_strict_support"}
    reference = next(
        (
            row
            for row in evidence_rows
            if text_value(row["compound"]) == REFERENCE_CP_NAME
        ),
        None,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "readiness_tier": READINESS_TIER,
        "evidence_owner": EVIDENCE_OWNER,
        "report_id": report_id,
        "index_source": index_source,
        "source_sample_stem": source_sample_stem,
        "index_input": (
            {
                "path": str(index_input),
                "sha256": file_sha256(index_input),
                "source_provenance": (
                    {
                        "sample_stem": index_provenance.sample_stem,
                        "source_path": index_provenance.source_path,
                        "source_sha256": index_provenance.source_sha256,
                    }
                    if index_provenance is not None
                    else None
                ),
            }
            if index_input is not None
            else None
        ),
        "opens_raw": index_source == "raw",
        "changes_detector_rule": False,
        "changes_counted_detection": False,
        "changes_matrix": False,
        "changes_export": False,
        "mass_tolerance_ppm": settings.mass_tolerance_ppm,
        "noise_level": settings.noise_level,
        "minimum_joint_scans": settings.minimum_joint_scans,
        "candidate_rt_half_window_sec": candidate_rt_half_window_sec,
        "ms1_scan_count": len(index),
        "centroid_peak_count": sum(len(scan.masses) for scan in index),
        "current_hypothesis_count": len(current),
        "current_strict_contract_supported_count": _count_true(
            current,
            "contract_b_strict_full_same_scan",
        ),
        "current_core_contract_supported_count": _count_true(
            current,
            "contract_c_core_plus4_conditional",
        ),
        "known_cp_expected_positive_count": len(cp_rows),
        "known_non_pt_control_count": 1 if d3 is not None else 0,
        "known_cp_strict_contract_supported_count": _count_true(
            cp_rows,
            "contract_b_strict_full_same_scan",
        ),
        "known_cp_core_contract_supported_count": _count_true(
            cp_rows,
            "contract_c_core_plus4_conditional",
        ),
        "known_cp_strict_rescue_count": sum(
            bool(row["contract_b_strict_full_same_scan"])
            and not bool(row["contract_a_current_feature_accept"])
            for row in cp_rows
        ),
        "d3_strict_contract_supported": bool(
            d3 and d3["contract_b_strict_full_same_scan"]
        ),
        "d3_core_contract_supported": bool(
            d3 and d3["contract_c_core_plus4_conditional"]
        ),
        "reference_cp_strict_supported": bool(
            reference and reference["contract_b_strict_full_same_scan"]
        ),
        "reference_cp_core_supported": bool(
            reference and reference["contract_c_core_plus4_conditional"]
        ),
        "conflict_component_count": len(conflict_rows),
        "conflict_resolution_counts": dict(sorted(conflict_counts.items())),
        "uniquely_resolved_conflict_component_count": sum(
            count
            for status, count in conflict_counts.items()
            if status in discovery_resolved_statuses
        ),
        "inputs": {
            path.name: {"path": str(path), "sha256": file_sha256(path)}
            for path in inputs
        },
    }


def _count_true(rows: Sequence[Mapping[str, object]], column: str) -> int:
    return sum(bool(row[column]) for row in rows)


def _required_float(value: object, field: str, label: str) -> float:
    parsed = optional_float(value)
    if parsed is None:
        raise ValueError(f"{label} has invalid {field}: {value!r}")
    return parsed

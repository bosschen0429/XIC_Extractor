from __future__ import annotations

import csv
import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Protocol, Sequence

from .schema import (
    BLAST_RADIUS_MANIFEST_SCHEMA_VERSION,
    BLAST_RADIUS_SUMMARY_COLUMNS,
    BLAST_RADIUS_SUMMARY_SCHEMA_VERSION,
    validate_token,
)

REVIEW_REQUIRED_FIELDS = frozenset(
    {
        "feature_family_id",
        "identity_decision",
        "identity_reason",
        "row_flags",
    }
)

CELLS_REQUIRED_FIELDS = frozenset(
    {
        "feature_family_id",
        "sample_stem",
        "status",
        "apex_rt",
        "peak_start_rt",
        "peak_end_rt",
        "rt_delta_sec",
        "trace_quality",
        "scan_support_score",
        "reason",
    }
)

OPTIONAL_ARTIFACT_ROLES = frozenset(
    {
        "candidate_gate_fixture_cohort",
        "candidate_gate_full_cohort",
        "tier2_trace_fixture_cohort",
        "tier2_coherence_fixture_cohort",
        "identity_diagnostic_context",
    }
)

OPTIONAL_ARTIFACT_ROLE_TO_MANIFEST_ROLE = {
    "candidate_gate_fixture_cohort": "blast_radius_context",
    "candidate_gate_full_cohort": "blast_radius_context",
    "tier2_trace_fixture_cohort": "tier2_trace_sidecar",
    "tier2_coherence_fixture_cohort": "blast_radius_context",
    "identity_diagnostic_context": "identity_diagnostic",
}

RESERVED_SAMPLE_IDS = frozenset({"__scope_rule__", "__family_context__"})

POSITIVE_MACHINE_LABELS = frozenset(
    {
        "detected",
        "rescued",
        "selected",
        "present",
        "provisional_discovery",
    }
)
ABSENT_MACHINE_LABELS = frozenset(
    {
        "absent",
        "missing",
        "no_match",
        "not_available",
        "not_detected",
        "unchecked",
    }
)
AMBIGUOUS_MACHINE_LABELS = frozenset({"duplicate_assigned", "ambiguous_ms1_owner"})

REQUIRED_BLAST_RADIUS_SURFACE_IDS = (
    "fixture_cohort_alignment_review",
    "fixture_cohort_alignment_cells",
    "full_cohort_alignment_review",
    "full_cohort_alignment_cells",
)


@dataclass(frozen=True)
class BlastRadiusClassProfile:
    evidence_gap_class: str
    seed_oracle_row_ids: tuple[str, ...]
    context_oracle_row_ids: tuple[str, ...]
    seed_feature_family_ids: tuple[str, ...]
    seed_sample_keys: tuple[str, ...]
    machine_prerequisites: tuple[str, ...]
    manual_prerequisites: tuple[str, ...]


def build_blast_radius_manifest(
    *,
    manual_oracle_tsv: Path,
    slice0_explanations_tsv: Path,
    slice0_evidence_vectors_tsv: Path,
    fixture_cohort_run_dir: Path,
    full_cohort_run_dir: Path,
    expected_manifest_tsv: Path | None = None,
    optional_artifacts: Mapping[str, Path] | None = None,
) -> tuple[dict[str, str], ...]:
    expected_hashes = _expected_hashes_from_slice0_evidence_vectors(
        slice0_evidence_vectors_tsv
    )
    if expected_manifest_tsv is not None:
        _merge_expected_hashes(
            expected_hashes,
            _expected_hashes_from_manifest(expected_manifest_tsv),
        )

    artifact_specs = (
        _ArtifactSpec(
            artifact_id="manual_oracle_fixture",
            artifact_role="manual_oracle_fixture",
            path=manual_oracle_tsv,
            required_fields=frozenset(),
        ),
        _ArtifactSpec(
            artifact_id="slice0_explanations",
            artifact_role="blast_radius_context",
            path=slice0_explanations_tsv,
            required_fields=frozenset(),
        ),
        _ArtifactSpec(
            artifact_id="slice0_evidence_vectors",
            artifact_role="blast_radius_context",
            path=slice0_evidence_vectors_tsv,
            required_fields=frozenset(),
        ),
        _ArtifactSpec(
            artifact_id="fixture_cohort_alignment_review",
            artifact_role="alignment_review",
            path=fixture_cohort_run_dir / "alignment_review.tsv",
            required_fields=REVIEW_REQUIRED_FIELDS,
        ),
        _ArtifactSpec(
            artifact_id="fixture_cohort_alignment_cells",
            artifact_role="alignment_cells",
            path=fixture_cohort_run_dir / "alignment_cells.tsv",
            required_fields=CELLS_REQUIRED_FIELDS,
        ),
        _ArtifactSpec(
            artifact_id="full_cohort_alignment_review",
            artifact_role="alignment_review",
            path=full_cohort_run_dir / "alignment_review.tsv",
            required_fields=REVIEW_REQUIRED_FIELDS,
        ),
        _ArtifactSpec(
            artifact_id="full_cohort_alignment_cells",
            artifact_role="alignment_cells",
            path=full_cohort_run_dir / "alignment_cells.tsv",
            required_fields=CELLS_REQUIRED_FIELDS,
        ),
    )
    rows = [
        _manifest_row(spec, expected_hashes=expected_hashes) for spec in artifact_specs
    ]

    for role, path in (optional_artifacts or {}).items():
        if role not in OPTIONAL_ARTIFACT_ROLES:
            raise ValueError(f"unknown optional artifact role: {role}")
        rows.append(
            _manifest_row(
                _ArtifactSpec(
                    artifact_id=role,
                    artifact_role=OPTIONAL_ARTIFACT_ROLE_TO_MANIFEST_ROLE[role],
                    path=path,
                    required_fields=frozenset(),
                ),
                expected_hashes=expected_hashes,
            )
        )

    return tuple(rows)


def build_class_profiles(
    explanations: Sequence[Mapping[str, str]],
    evidence_vectors: Sequence[Mapping[str, str]],
) -> dict[str, BlastRadiusClassProfile]:
    evidence_by_oracle: dict[str, list[Mapping[str, str]]] = {}
    for row in evidence_vectors:
        evidence_by_oracle.setdefault(row.get("oracle_row_id", ""), []).append(row)

    builders: dict[str, _ClassProfileBuilder] = {}
    for explanation in explanations:
        evidence_gap_class = explanation["evidence_gap_class"]
        builder = builders.setdefault(
            evidence_gap_class,
            _ClassProfileBuilder(evidence_gap_class=evidence_gap_class),
        )
        oracle_row_id = explanation.get("oracle_row_id", "")
        if _is_context_explanation(explanation):
            builder.context_oracle_row_ids.append(oracle_row_id)
            builder.machine_prerequisites.append("context_only")
            builder.manual_prerequisites.extend(_manual_prerequisites(explanation))
            continue

        family = explanation.get("feature_family_id", "")
        sample = explanation.get("sample_id", "")
        builder.seed_oracle_row_ids.append(oracle_row_id)
        if family:
            builder.seed_feature_family_ids.append(family)
        if family and sample:
            builder.seed_sample_keys.append(_sample_key(family, sample))
        builder.manual_prerequisites.extend(_manual_prerequisites(explanation))
        builder.machine_prerequisites.extend(
            _machine_prerequisites(explanation, evidence_gap_class=evidence_gap_class)
        )
        for evidence_row in evidence_by_oracle.get(oracle_row_id, ()):
            builder.machine_prerequisites.extend(
                _machine_prerequisites(
                    evidence_row,
                    evidence_gap_class=evidence_gap_class,
                    explanation=explanation,
                )
            )

    return {
        evidence_gap_class: builder.build()
        for evidence_gap_class, builder in sorted(builders.items())
    }


def build_blast_radius_summary(
    *,
    class_profiles: Mapping[str, BlastRadiusClassProfile],
    manifest_rows: Sequence[Mapping[str, str]],
    fixture_cohort_run_dir: Path,
    full_cohort_run_dir: Path,
) -> tuple[dict[str, str], ...]:
    manifest_by_id = {row.get("artifact_id", ""): row for row in manifest_rows}
    _validate_required_manifest_paths(
        manifest_by_id,
        fixture_cohort_run_dir=fixture_cohort_run_dir,
        full_cohort_run_dir=full_cohort_run_dir,
    )
    required_surfaces_current = _required_cell_surfaces_current(manifest_by_id)
    global_seed_sample_keys = {
        seed_sample_key
        for profile in class_profiles.values()
        for seed_sample_key in profile.seed_sample_keys
    }
    stats = {
        (profile.evidence_gap_class, scope): _BlastRadiusStats()
        for profile in class_profiles.values()
        for scope in (
            "seed",
            "non_seed_same_family",
            "all_available_fixture_cohort",
            "all_available_full_cohort",
            "overall",
        )
    }

    for profile in class_profiles.values():
        seed_stats = stats[(profile.evidence_gap_class, "seed")]
        seed_count = len(profile.seed_oracle_row_ids)
        if seed_count:
            seed_stats.assessed_row_count = seed_count
            seed_stats.all_available_row_count = seed_count
            seed_stats.compatible_row_count = seed_count

    _scan_alignment_cells(
        fixture_cohort_run_dir / "alignment_cells.tsv",
        dataset_scope="all_available_fixture_cohort",
        class_profiles=class_profiles,
        global_seed_sample_keys=global_seed_sample_keys,
        stats=stats,
    )
    _scan_alignment_cells(
        full_cohort_run_dir / "alignment_cells.tsv",
        dataset_scope="all_available_full_cohort",
        class_profiles=class_profiles,
        global_seed_sample_keys=global_seed_sample_keys,
        stats=stats,
    )

    rows: list[dict[str, str]] = []
    for evidence_gap_class, profile in sorted(class_profiles.items()):
        for scope in (
            "seed",
            "non_seed_same_family",
            "all_available_fixture_cohort",
            "all_available_full_cohort",
            "overall",
        ):
            artifact_id = _artifact_id_for_scope(scope)
            scope_stats = stats[(evidence_gap_class, scope)]
            rows.append(
                _summary_row(
                    profile=profile,
                    scope=scope,
                    artifact_id=artifact_id,
                    stats=scope_stats,
                    required_surfaces_current=required_surfaces_current,
                )
            )
    return tuple(rows)


def preflight_tsv_artifact(
    source: Path | Iterable[str],
    *,
    required_fields: frozenset[str],
    sample_row_limit: int,
) -> dict[str, str]:
    if sample_row_limit < 0:
        raise ValueError("sample_row_limit must be non-negative")
    if isinstance(source, Path):
        with source.open("r", encoding="utf-8", newline="") as handle:
            return _inspect_tsv(
                handle,
                required_fields=required_fields,
                sample_row_limit=sample_row_limit,
            )
    return _inspect_tsv(
        source,
        required_fields=required_fields,
        sample_row_limit=sample_row_limit,
    )


class _ClassProfileBuilder:
    def __init__(self, *, evidence_gap_class: str) -> None:
        self.evidence_gap_class = evidence_gap_class
        self.seed_oracle_row_ids: list[str] = []
        self.context_oracle_row_ids: list[str] = []
        self.seed_feature_family_ids: list[str] = []
        self.seed_sample_keys: list[str] = []
        self.machine_prerequisites: list[str] = []
        self.manual_prerequisites: list[str] = []

    def build(self) -> BlastRadiusClassProfile:
        return BlastRadiusClassProfile(
            evidence_gap_class=self.evidence_gap_class,
            seed_oracle_row_ids=_unique_sorted(self.seed_oracle_row_ids),
            context_oracle_row_ids=_unique_sorted(self.context_oracle_row_ids),
            seed_feature_family_ids=_unique_sorted(self.seed_feature_family_ids),
            seed_sample_keys=_unique_sorted(self.seed_sample_keys),
            machine_prerequisites=_unique_sorted(self.machine_prerequisites),
            manual_prerequisites=_unique_sorted(self.manual_prerequisites),
        )


@dataclass
class _BlastRadiusStats:
    assessed_row_count: int = 0
    all_available_row_count: int = 0
    compatible_row_count: int = 0
    unavailable_field_count: int = 0
    contradictory_count: int = 0
    ambiguous_machine_match_count: int = 0
    non_seed_same_family_count: int = 0
    has_missing_required_fields: bool = False


@dataclass(frozen=True, slots=True)
class _CellFacts:
    feature_family_id: str
    sample_key: str
    status: str
    trace_quality: str
    scan_support_score: float | None
    rt_delta_sec: float
    tokens: frozenset[str]


def _is_context_explanation(row: Mapping[str, str]) -> bool:
    sample_id = row.get("sample_id", "")
    return (
        row.get("manual_label") == "not_applicable" or sample_id in RESERVED_SAMPLE_IDS
    )


def _manual_prerequisites(row: Mapping[str, str]) -> tuple[str, ...]:
    prerequisites = [
        f"manual_label:{row['manual_label']}",
        f"manual_scope:{row.get('manual_scope', '')}",
    ]
    label_source = row.get("manual_label_source", "")
    if label_source:
        prerequisites.append(f"manual_label_source:{label_source}")
    prerequisites.extend(
        f"manual_tag:{tag}"
        for tag in _split_semicolon(row.get("manual_reason_tags", ""))
    )
    return tuple(prerequisite for prerequisite in prerequisites if prerequisite)


def _machine_prerequisites(
    row: Mapping[str, str],
    *,
    evidence_gap_class: str,
    explanation: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    explanation = explanation or row
    prerequisites: list[str] = []
    label = row.get("machine_current_label") or explanation.get(
        "machine_current_label",
        "",
    )
    if _is_positive_label(label):
        prerequisites.append("positive_machine_cell")
    if _is_absent_label(label):
        prerequisites.append("absent_machine_cell")
    if (
        row.get("machine_match_status") or explanation.get("machine_match_status")
    ) == "ambiguous_multiple_matches":
        prerequisites.append("ambiguous_machine_match")
    tokens = _row_text_tokens(row, explanation)
    if "ambiguous_ms1_owner" in tokens or _is_ambiguous_label(label):
        prerequisites.append("ambiguous_machine_match")
    if (
        row.get("rt_context_status") == "conflicting"
        or row.get("pattern_conflict_status") == "rt_pattern_conflict"
        or evidence_gap_class == "machine_too_permissive_rt_pattern_conflict"
    ):
        prerequisites.append("rt_pattern_conflict")
    if (
        row.get("intensity_status") in {"low_but_visible", "too_low_to_assess"}
        or row.get("dda_opportunity_status") == "low_intensity_stochastic_not_observed"
        or "no_local_ms1_owner" in tokens
        or "weak_scan_support" in tokens
    ):
        prerequisites.append("low_opportunity_machine_context")
    if evidence_gap_class == "machine_too_permissive_scope_rule_conflict":
        prerequisites.append("scope_rule_conflict")
    if not prerequisites and not row.get("manual_label"):
        prerequisites.append("machine_context_observed")
    return tuple(prerequisites)


def _scan_alignment_cells(
    path: Path,
    *,
    dataset_scope: str,
    class_profiles: Mapping[str, BlastRadiusClassProfile],
    global_seed_sample_keys: set[str],
    stats: dict[tuple[str, str], _BlastRadiusStats],
) -> None:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fieldnames = frozenset(reader.fieldnames or ())
        missing_required_fields = frozenset(CELLS_REQUIRED_FIELDS - fieldnames)
        pending_facts: dict[str, _CellFacts] = {}
        duplicate_keys: set[str] = set()
        for row in reader:
            facts = _cell_facts_from_row(row)
            sample_key = facts.sample_key
            if sample_key in duplicate_keys:
                _accumulate_scanned_cell_facts(
                    facts,
                    duplicate_machine_row=True,
                    dataset_scope=dataset_scope,
                    class_profiles=class_profiles,
                    global_seed_sample_keys=global_seed_sample_keys,
                    stats=stats,
                    missing_required_fields=missing_required_fields,
                )
                continue
            if sample_key in pending_facts:
                first_facts = pending_facts.pop(sample_key)
                duplicate_keys.add(sample_key)
                for duplicate_facts in (first_facts, facts):
                    _accumulate_scanned_cell_facts(
                        duplicate_facts,
                        duplicate_machine_row=True,
                        dataset_scope=dataset_scope,
                        class_profiles=class_profiles,
                        global_seed_sample_keys=global_seed_sample_keys,
                        stats=stats,
                        missing_required_fields=missing_required_fields,
                    )
                continue
            pending_facts[sample_key] = facts

        for facts in pending_facts.values():
            _accumulate_scanned_cell_facts(
                facts,
                duplicate_machine_row=False,
                dataset_scope=dataset_scope,
                class_profiles=class_profiles,
                global_seed_sample_keys=global_seed_sample_keys,
                stats=stats,
                missing_required_fields=missing_required_fields,
            )


def _cell_facts_from_row(row: Mapping[str, str]) -> _CellFacts:
    family = row.get("feature_family_id", "")
    sample = row.get("sample_stem", "")
    return _CellFacts(
        feature_family_id=family,
        sample_key=_sample_key(family, sample),
        status=row.get("status", ""),
        trace_quality=row.get("trace_quality", ""),
        scan_support_score=_float_or_none(row.get("scan_support_score")),
        rt_delta_sec=_abs_float(row.get("rt_delta_sec")),
        tokens=frozenset(_row_text_tokens(row)),
    )


def _accumulate_scanned_cell_facts(
    facts: _CellFacts,
    *,
    duplicate_machine_row: bool,
    dataset_scope: str,
    class_profiles: Mapping[str, BlastRadiusClassProfile],
    global_seed_sample_keys: set[str],
    stats: dict[tuple[str, str], _BlastRadiusStats],
    missing_required_fields: frozenset[str],
) -> None:
    for profile in class_profiles.values():
        if not profile.seed_oracle_row_ids:
            continue
        _accumulate_cell_row(
            stats[(profile.evidence_gap_class, dataset_scope)],
            profile=profile,
            facts=facts,
            missing_required_fields=missing_required_fields,
            duplicate_machine_row=duplicate_machine_row,
        )
        _accumulate_cell_row(
            stats[(profile.evidence_gap_class, "overall")],
            profile=profile,
            facts=facts,
            missing_required_fields=missing_required_fields,
            duplicate_machine_row=duplicate_machine_row,
        )
        if (
            facts.feature_family_id in profile.seed_feature_family_ids
            and facts.sample_key not in global_seed_sample_keys
        ):
            same_family_stats = stats[
                (profile.evidence_gap_class, "non_seed_same_family")
            ]
            same_family_stats.non_seed_same_family_count += 1
            _accumulate_cell_row(
                same_family_stats,
                profile=profile,
                facts=facts,
                missing_required_fields=missing_required_fields,
                duplicate_machine_row=duplicate_machine_row,
            )


def _accumulate_cell_row(
    stats: _BlastRadiusStats,
    *,
    profile: BlastRadiusClassProfile,
    facts: _CellFacts,
    missing_required_fields: frozenset[str],
    duplicate_machine_row: bool,
) -> None:
    stats.assessed_row_count += 1
    if missing_required_fields:
        stats.unavailable_field_count += 1
        stats.has_missing_required_fields = True
        return
    stats.all_available_row_count += 1
    if _is_ambiguous_machine_row(facts, duplicate_machine_row=duplicate_machine_row):
        stats.ambiguous_machine_match_count += 1
        return
    if _is_compatible_machine_row(profile, facts):
        stats.compatible_row_count += 1
    elif _is_contradictory_machine_row(profile, facts):
        stats.contradictory_count += 1


def _summary_row(
    *,
    profile: BlastRadiusClassProfile,
    scope: str,
    artifact_id: str,
    stats: _BlastRadiusStats,
    required_surfaces_current: bool,
) -> dict[str, str]:
    assessed = stats.assessed_row_count
    compatible_fraction = _fraction(stats.compatible_row_count, assessed)
    contradictory_fraction = _fraction(stats.contradictory_count, assessed)
    ambiguous_fraction = _fraction(stats.ambiguous_machine_match_count, assessed)
    unavailable_fraction = _fraction(stats.unavailable_field_count, assessed)
    values = {
        "summary_schema_version": BLAST_RADIUS_SUMMARY_SCHEMA_VERSION,
        "scope": scope,
        "artifact_id": artifact_id,
        "evidence_gap_class": profile.evidence_gap_class,
        "seed_count": str(len(profile.seed_oracle_row_ids)),
        "context_row_count": str(len(profile.context_oracle_row_ids)),
        "non_seed_same_family_count": str(stats.non_seed_same_family_count),
        "assessed_row_count": str(stats.assessed_row_count),
        "all_available_row_count": str(stats.all_available_row_count),
        "compatible_row_count": str(stats.compatible_row_count),
        "unavailable_field_count": str(stats.unavailable_field_count),
        "contradictory_count": str(stats.contradictory_count),
        "ambiguous_machine_match_count": str(stats.ambiguous_machine_match_count),
        "compatible_fraction": compatible_fraction,
        "contradictory_fraction": contradictory_fraction,
        "ambiguous_fraction": ambiguous_fraction,
        "unavailable_fraction": unavailable_fraction,
        "overfit_risk": _overfit_risk(
            profile=profile,
            stats=stats,
            required_surfaces_current=required_surfaces_current,
        ),
        "example_oracle_row_ids": ";".join(profile.seed_oracle_row_ids[:5]),
        "example_feature_family_ids": ";".join(profile.seed_feature_family_ids[:5]),
    }
    return {column: values[column] for column in BLAST_RADIUS_SUMMARY_COLUMNS}


def _overfit_risk(
    *,
    profile: BlastRadiusClassProfile,
    stats: _BlastRadiusStats,
    required_surfaces_current: bool,
) -> str:
    seed_count = len(profile.seed_oracle_row_ids)
    if seed_count == 0:
        return "none"
    if (
        not required_surfaces_current
        or stats.has_missing_required_fields
        or stats.assessed_row_count == 0
    ):
        return "unassessed"
    assessed = stats.assessed_row_count
    compatible_fraction = stats.compatible_row_count / assessed
    contradictory_fraction = stats.contradictory_count / assessed
    ambiguous_fraction = stats.ambiguous_machine_match_count / assessed
    unavailable_fraction = stats.unavailable_field_count / assessed
    sufficient_denominator = assessed >= max(50, 5 * seed_count)
    if contradictory_fraction >= 0.50:
        return "high"
    if (
        sufficient_denominator
        and stats.compatible_row_count == 0
        and unavailable_fraction < 0.20
        and ambiguous_fraction < 0.20
    ):
        return "high"
    if (
        not sufficient_denominator
        or 0 < compatible_fraction < 0.01
        or 0.10 <= contradictory_fraction < 0.50
        or ambiguous_fraction >= 0.20
        or unavailable_fraction >= 0.20
    ):
        return "medium"
    if (
        compatible_fraction >= 0.01
        and contradictory_fraction < 0.10
        and ambiguous_fraction < 0.20
        and unavailable_fraction < 0.20
    ):
        return "low"
    return "medium"


def _is_compatible_machine_row(
    profile: BlastRadiusClassProfile,
    facts: _CellFacts,
) -> bool:
    evidence_gap_class = profile.evidence_gap_class
    status = facts.status
    tokens = facts.tokens
    if evidence_gap_class == "machine_too_conservative_low_opportunity":
        return _is_absent_label(status) and (
            "no_local_ms1_owner" in tokens
            or "weak_scan_support" in tokens
            or facts.scan_support_score not in (None, 1.0)
            and (facts.scan_support_score or 0.0) < 0.5
        )
    if evidence_gap_class == "machine_too_conservative_shape_or_pattern_unmodeled":
        return _is_positive_label(status) and not _is_ambiguous_machine_row(
            facts,
            duplicate_machine_row=False,
        )
    if evidence_gap_class == "machine_too_permissive_rt_pattern_conflict":
        return _is_positive_label(status) and (
            "rt_pattern_conflict" in tokens
            or "pattern_conflict" in tokens
            or "pattern_mismatch" in tokens
            or facts.rt_delta_sec >= 60.0
        )
    if evidence_gap_class == "machine_too_permissive_scope_rule_conflict":
        return _is_positive_label(status) and (
            "scope_rule" in tokens or "unmentioned" in tokens
        )
    if evidence_gap_class == "human_unjudgeable_shape_bad":
        return facts.trace_quality.lower() in {
            "low",
            "poor",
            "noisy",
            "flat",
        } or _is_absent_label(status)
    if evidence_gap_class == "rt_drift_policy_gap":
        return _is_positive_label(status) and (
            "drift" in tokens or facts.rt_delta_sec >= 30.0
        )
    if evidence_gap_class == "boundary_reference_ambiguous":
        return "boundary" in tokens or "ambiguous" in tokens
    if evidence_gap_class == "machine_agrees_with_manual":
        if "positive_machine_cell" in profile.machine_prerequisites:
            return _is_positive_label(status)
        if "absent_machine_cell" in profile.machine_prerequisites:
            return _is_absent_label(status)
    return False


def _is_contradictory_machine_row(
    profile: BlastRadiusClassProfile,
    facts: _CellFacts,
) -> bool:
    tokens = facts.tokens
    if (
        "rt_pattern_conflict" in profile.machine_prerequisites
        and _is_positive_label(facts.status)
        and "supported_without_conflict" in tokens
    ):
        return True
    if (
        "scope_rule_conflict" in profile.machine_prerequisites
        and _is_positive_label(facts.status)
        and "scope_allowed" in tokens
    ):
        return True
    return False


def _is_ambiguous_machine_row(
    facts: _CellFacts,
    *,
    duplicate_machine_row: bool,
) -> bool:
    if duplicate_machine_row or _is_ambiguous_label(facts.status):
        return True
    tokens = facts.tokens
    return (
        "ambiguous_ms1_owner" in tokens
        or "ambiguous_multiple_matches" in tokens
        or "duplicate_ms1_peak_claim" in tokens
        or "duplicate" in tokens
    )


def _required_cell_surfaces_current(
    manifest_by_id: Mapping[str, Mapping[str, str]],
) -> bool:
    for artifact_id in REQUIRED_BLAST_RADIUS_SURFACE_IDS:
        row = manifest_by_id.get(artifact_id)
        if row is None:
            return False
        if row.get("artifact_status") != "present_current":
            return False
        if row.get("missing_required_fields"):
            return False
    return True


def _validate_required_manifest_paths(
    manifest_by_id: Mapping[str, Mapping[str, str]],
    *,
    fixture_cohort_run_dir: Path,
    full_cohort_run_dir: Path,
) -> None:
    expected_paths = {
        "fixture_cohort_alignment_review": fixture_cohort_run_dir
        / "alignment_review.tsv",
        "fixture_cohort_alignment_cells": fixture_cohort_run_dir
        / "alignment_cells.tsv",
        "full_cohort_alignment_review": full_cohort_run_dir / "alignment_review.tsv",
        "full_cohort_alignment_cells": full_cohort_run_dir / "alignment_cells.tsv",
    }
    for artifact_id, actual_path in expected_paths.items():
        row = manifest_by_id.get(artifact_id)
        manifest_path_value = (row or {}).get("artifact_path", "")
        if not manifest_path_value:
            raise ValueError(
                "required manifest artifact path missing: "
                f"artifact_id={artifact_id}; actual_path={actual_path.resolve()}"
            )
        manifest_path = Path(manifest_path_value)
        if manifest_path.resolve() != actual_path.resolve():
            raise ValueError(
                "required manifest artifact path mismatch: "
                f"artifact_id={artifact_id}; "
                f"manifest_path={manifest_path.resolve()}; "
                f"actual_path={actual_path.resolve()}"
            )


def _artifact_id_for_scope(scope: str) -> str:
    return {
        "seed": "slice0_explanations",
        "non_seed_same_family": "combined_alignment_cells",
        "all_available_fixture_cohort": "fixture_cohort_alignment_cells",
        "all_available_full_cohort": "full_cohort_alignment_cells",
        "overall": "combined_alignment_cells",
    }[scope]


def _sample_key(family: str, sample: str) -> str:
    return f"{family}|{sample}"


def _is_positive_label(value: str) -> bool:
    return value.lower() in POSITIVE_MACHINE_LABELS


def _is_absent_label(value: str) -> bool:
    return value.lower() in ABSENT_MACHINE_LABELS


def _is_ambiguous_label(value: str) -> bool:
    return value.lower() in AMBIGUOUS_MACHINE_LABELS


def _row_text_tokens(*rows: Mapping[str, str]) -> set[str]:
    tokens: set[str] = set()
    for row in rows:
        for field in (
            "reason",
            "machine_reason",
            "machine_blockers",
            "row_flags",
            "status",
        ):
            value = row.get(field, "")
            normalized = (
                value.lower().replace(" ", "_").replace(";", "_").replace("-", "_")
            )
            tokens.add(normalized)
            tokens.update(part for part in normalized.split("_") if part)
    return tokens


def _split_semicolon(value: str) -> tuple[str, ...]:
    return tuple(part for part in str(value or "").split(";") if part)


def _unique_sorted(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({value for value in values if value}))


def _fraction(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "0.000000"
    return f"{numerator / denominator:.6f}"


def _abs_float(value: str | None) -> float:
    parsed = _float_or_none(value)
    return abs(parsed) if parsed is not None else 0.0


def _float_or_none(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).lstrip("'"))
    except ValueError:
        return None


class _ArtifactSpec:
    def __init__(
        self,
        *,
        artifact_id: str,
        artifact_role: str,
        path: Path,
        required_fields: frozenset[str],
    ) -> None:
        self.artifact_id = artifact_id
        self.artifact_role = artifact_role
        self.path = path
        self.required_fields = required_fields


def _manifest_row(
    spec: _ArtifactSpec,
    *,
    expected_hashes: Mapping[str, tuple[str, str]],
) -> dict[str, str]:
    expected_hash, freshness_basis = _expected_hash_for(
        spec.path,
        artifact_id=spec.artifact_id,
        expected_hashes=expected_hashes,
    )
    if not spec.path.exists():
        return {
            "manifest_schema_version": BLAST_RADIUS_MANIFEST_SCHEMA_VERSION,
            "artifact_id": spec.artifact_id,
            "artifact_role": spec.artifact_role,
            "artifact_path": str(spec.path),
            "artifact_sha256": "",
            "expected_artifact_sha256": expected_hash,
            "freshness_basis": freshness_basis,
            "artifact_schema_version": "",
            "artifact_status": "missing",
            "row_count": "0",
            "sample_count": "0",
            "family_count": "0",
            "available_required_fields": "",
            "missing_required_fields": _join_fields(spec.required_fields),
            "generated_from_existing_artifact": "TRUE",
            "notes": "artifact missing",
        }

    inspection = _inspect_tsv_path_for_manifest(
        spec.path,
        required_fields=spec.required_fields,
    )
    artifact_hash = inspection["artifact_sha256"]
    missing_required_fields = inspection["missing_required_fields"]
    if missing_required_fields:
        status = "present_missing_required_fields"
    elif not expected_hash:
        status = "present_hash_unpinned"
    elif artifact_hash != expected_hash:
        status = "present_stale_hash_mismatch"
    else:
        status = "present_current"

    return {
        "manifest_schema_version": BLAST_RADIUS_MANIFEST_SCHEMA_VERSION,
        "artifact_id": spec.artifact_id,
        "artifact_role": spec.artifact_role,
        "artifact_path": str(spec.path),
        "artifact_sha256": artifact_hash,
        "expected_artifact_sha256": expected_hash,
        "freshness_basis": freshness_basis,
        "artifact_schema_version": "",
        "artifact_status": status,
        "row_count": inspection["row_count"],
        "sample_count": inspection["sample_count"],
        "family_count": inspection["family_count"],
        "available_required_fields": inspection["available_required_fields"],
        "missing_required_fields": missing_required_fields,
        "generated_from_existing_artifact": "TRUE",
        "notes": "",
    }


_NO_SAMPLE_LIMIT = 2**63 - 1


def _inspect_tsv_path_for_manifest(
    path: Path,
    *,
    required_fields: frozenset[str],
) -> dict[str, str]:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        inspection = _inspect_tsv(
            _hashed_text_lines(handle, digest),
            required_fields=required_fields,
            sample_row_limit=_NO_SAMPLE_LIMIT,
        )
    return {
        "artifact_sha256": digest.hexdigest().upper(),
        **inspection,
    }


class _Digest(Protocol):
    def update(self, data: bytes) -> object: ...


def _hashed_text_lines(source: Iterable[bytes], digest: _Digest) -> Iterable[str]:
    for line in source:
        digest.update(line)
        yield line.decode("utf-8")


def _inspect_tsv(
    source: Iterable[str],
    *,
    required_fields: frozenset[str],
    sample_row_limit: int,
) -> dict[str, str]:
    reader = csv.DictReader(source, delimiter="\t")
    fieldnames = tuple(reader.fieldnames or ())
    missing_required_fields = tuple(
        field for field in sorted(required_fields) if field not in fieldnames
    )
    available_required_fields = tuple(
        field for field in sorted(required_fields) if field in fieldnames
    )

    row_count = 0
    samples: set[str] = set()
    families: set[str] = set()
    for row in islice(reader, sample_row_limit):
        row_count += 1
        sample = row.get("sample_stem") or row.get("sample_id") or ""
        family = row.get("feature_family_id") or ""
        if sample:
            samples.add(sample)
        if family:
            families.add(family)

    artifact_status = (
        "present_missing_required_fields"
        if missing_required_fields
        else "present_hash_unpinned"
    )
    return {
        "artifact_status": artifact_status,
        "row_count": str(row_count),
        "sample_count": str(len(samples)),
        "family_count": str(len(families)),
        "available_required_fields": _join_fields(available_required_fields),
        "missing_required_fields": _join_fields(missing_required_fields),
    }


def _expected_hashes_from_slice0_evidence_vectors(
    evidence_vectors_tsv: Path,
) -> dict[str, tuple[str, str]]:
    if not evidence_vectors_tsv.exists():
        return {}
    expected: dict[str, tuple[str, str]] = {}
    with evidence_vectors_tsv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            artifact = (row.get("source_artifact") or "").strip()
            artifact_hash = (row.get("source_artifact_sha256") or "").strip().upper()
            if artifact and artifact_hash:
                _add_expected_hash(
                    expected,
                    key=artifact,
                    value=artifact_hash,
                    freshness_basis="slice0_evidence_vector",
                )
    return expected


def _expected_hashes_from_manifest(
    manifest_tsv: Path,
) -> dict[str, tuple[str, str]]:
    expected: dict[str, tuple[str, str]] = {}
    with manifest_tsv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            artifact_role = (row.get("artifact_role") or "").strip()
            if not artifact_role:
                raise ValueError(f"{manifest_tsv}: missing artifact_role")
            validate_token(artifact_role, "artifact_role")
            artifact_hash = (row.get("expected_artifact_sha256") or "").strip().upper()
            if not artifact_hash:
                continue
            if (
                not (row.get("artifact_id") or "").strip()
                and not (row.get("artifact_path") or "").strip()
            ):
                raise ValueError(
                    f"{manifest_tsv}: expected_artifact_sha256 requires "
                    "artifact_id or artifact_path"
                )
            for key_field in ("artifact_id", "artifact_path"):
                key = (row.get(key_field) or "").strip()
                if key:
                    _add_expected_hash(
                        expected,
                        key=key,
                        value=artifact_hash,
                        freshness_basis="expected_blast_radius_manifest",
                    )
    return expected


def _expected_hash_for(
    path: Path,
    *,
    artifact_id: str,
    expected_hashes: Mapping[str, tuple[str, str]],
) -> tuple[str, str]:
    keys = (
        artifact_id,
        str(path),
        str(path.resolve()),
    )
    for key in keys:
        expected = expected_hashes.get(_normalize_key(key))
        if expected is not None:
            return expected
    return "", "not_available"


def _merge_expected_hashes(
    expected: dict[str, tuple[str, str]],
    incoming: Mapping[str, tuple[str, str]],
) -> None:
    for key, (value, freshness_basis) in incoming.items():
        _add_expected_hash(
            expected,
            key=key,
            value=value,
            freshness_basis=freshness_basis,
        )


def _add_expected_hash(
    expected: dict[str, tuple[str, str]],
    *,
    key: str,
    value: str,
    freshness_basis: str,
) -> None:
    normalized_key = _normalize_key(key)
    existing = expected.get(normalized_key)
    if existing is not None:
        existing_hash, existing_basis = existing
        if existing_hash != value:
            raise ValueError(
                "conflicting expected hash for "
                f"{normalized_key}: {existing_hash} ({existing_basis}) vs "
                f"{value} ({freshness_basis})"
            )
        return
    expected[normalized_key] = (value, freshness_basis)


def _normalize_key(value: str) -> str:
    return value.replace("/", "\\").lower()


def _join_fields(fields: Iterable[str]) -> str:
    return ";".join(fields)

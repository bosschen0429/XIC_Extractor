"""Opt-in no-RAW CID-NL path from shared evidence to QuantMatrixVersion.

This orchestration translates only owner-backed facts.  It does not read RAW,
recompute evidence, write files, or activate the default Matrix.  Its terminal
adapter revalidates the exact file-level ProductionAcceptanceManifest, canonical
digest, and referenced source artifact before entering QuantMatrixVersion.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import InitVar, asdict, dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import cast, final

from xic_extractor.alignment.cid_nl_evidence_spine import (
    CidNlReferenceSpine,
    build_cid_nl_reference_spine,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.conservative_backfill import (
    ConservativeBackfillCellDecision,
    ConservativeBackfillGroupFacts,
    ConservativeBackfillRecoveryCandidateUniverse,
    ModeTrackConservativeBackfillGroupFacts,
    assess_untargeted_conservative_backfill_group,
    map_conservative_backfill_candidates,
    mint_conservative_backfill_recovery_candidate_universe,
)
from xic_extractor.alignment.matrix_output_contract import canonical_mz_text
from xic_extractor.alignment.mode_track import (
    ModeTrackAssignment,
)
from xic_extractor.alignment.production_acceptance import (
    CheckedProductionAcceptanceManifest,
    ProductionAcceptanceManifestError,
    build_quant_matrix_version_from_checked_manifest,
    load_checked_production_acceptance_manifest,
)
from xic_extractor.alignment.quant_matrix_version import (
    QuantMatrixVersionRows,
    production_acceptance_manifest_sha256,
)
from xic_extractor.discovery.cid_nl_evidence_projection import (
    UntargetedCidNlProjectionRef,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
    is_unsearched_cid_nl_discovery_assessment,
)
from xic_extractor.peak_detection.cid_nl_method_subject import CidNlMethodSubject
from xic_extractor.peak_detection.observations import (
    canonical_peak_observation_ref_bytes,
)
from xic_extractor.processing_identity import ProcessingOwnerReferences
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

VALIDATION_STATUS = "production_candidate"
PRODUCT_AUTHORITY = "none_until_checked_manifest"
SOURCE_SCHEMA_VERSION = "untargeted_cid_nl_golden_path_source_v1"
CID_NL_ALIGNMENT_CONFIG = AlignmentConfig(anchor_min_seed_events=1)
_GOLDEN_PATH_PROJECTION_MINT_TOKEN = object()


class UntargetedCidNlGoldenPathError(ValueError):
    """Raised when the bounded Untargeted path would lose or invent authority."""


@dataclass(frozen=True, slots=True)
class UntargetedCidNlAssessmentInput:
    """One sample assessment plus exact Discovery projections for its evidence."""

    sample: ResolvedCanonicalSampleIdentity
    assessment: CidNlMethodAssessmentRef
    discovery_projections: tuple[UntargetedCidNlProjectionRef, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.sample, ResolvedCanonicalSampleIdentity):
            raise UntargetedCidNlGoldenPathError(
                "sample input requires an owner-resolved canonical sample"
            )
        if not isinstance(self.assessment, CidNlMethodAssessmentRef):
            raise UntargetedCidNlGoldenPathError(
                "sample input requires an owner-backed method assessment"
            )
        if not isinstance(self.discovery_projections, tuple) or any(
            not isinstance(item, UntargetedCidNlProjectionRef)
            for item in self.discovery_projections
        ):
            raise UntargetedCidNlGoldenPathError(
                "discovery_projections must contain exact Untargeted projection refs"
            )
        sample_id = self.sample.identity.sample_identity_id
        if self.assessment.content.canonical_sample_identity_id != sample_id:
            raise UntargetedCidNlGoldenPathError(
                "sample and assessment reference different canonical samples"
            )
        projection_sample_ids = {
            item.canonical_sample_identity_id for item in self.discovery_projections
        }
        if projection_sample_ids.difference({sample_id}):
            raise UntargetedCidNlGoldenPathError(
                "Discovery projection references a different canonical sample"
            )
        evidence_artifact_ids = tuple(
            sorted(
                evidence.artifact.evidence_artifact_id
                for evidence in self.assessment.evidence_refs
            )
        )
        projected_artifact_ids = tuple(
            sorted(
                projection.canonical_evidence_artifact_id
                for projection in self.discovery_projections
            )
        )
        if projected_artifact_ids != evidence_artifact_ids:
            raise UntargetedCidNlGoldenPathError(
                "Discovery projections must cover every assessment evidence ref "
                "exactly once"
            )
        object.__setattr__(
            self,
            "discovery_projections",
            tuple(
                sorted(
                    self.discovery_projections,
                    key=lambda projection: projection.artifact.evidence_artifact_id,
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class UntargetedCidNlRecoveryInput:
    """One eligible missing cell's complete owner-backed revisit assessment."""

    sample: ResolvedCanonicalSampleIdentity
    candidate_assessment: CidNlMethodAssessmentRef

    def __post_init__(self) -> None:
        if not isinstance(self.sample, ResolvedCanonicalSampleIdentity):
            raise UntargetedCidNlGoldenPathError(
                "recovery input requires an owner-resolved canonical sample"
            )
        if not isinstance(self.candidate_assessment, CidNlMethodAssessmentRef):
            raise UntargetedCidNlGoldenPathError(
                "recovery input requires an owner-backed assessment"
            )
        if self.candidate_assessment.content.canonical_sample_identity_id != (
            self.sample.identity.sample_identity_id
        ):
            raise UntargetedCidNlGoldenPathError(
                "recovery assessment references a different sample"
            )
        if self.candidate_assessment.content.method_result != "HOLD":
            raise UntargetedCidNlGoldenPathError(
                "recovery input must be a completed HOLD assessment"
            )
        if not self.candidate_assessment.candidate_peaks:
            raise UntargetedCidNlGoldenPathError(
                "recovery input must retain its complete non-empty candidate universe"
            )


@final
@dataclass(frozen=True, slots=True)
class UntargetedCidNlGoldenPathProjection:
    """Prepared owner facts immediately before the file-level manifest gate."""

    method_subject: CidNlMethodSubject
    discovery_projection_refs: tuple[UntargetedCidNlProjectionRef, ...]
    spine: CidNlReferenceSpine
    backfill_facts: ConservativeBackfillGroupFacts
    backfill_decisions: tuple[ConservativeBackfillCellDecision, ...]
    recovery_universes: tuple[
        ConservativeBackfillRecoveryCandidateUniverse,
        ...,
    ]
    matrix_header: tuple[str, ...]
    input_quant_matrix_rows: tuple[Mapping[str, str], ...]
    input_matrix_identity_rows: tuple[Mapping[str, str], ...]
    pending_acceptance_rows: tuple[Mapping[str, str], ...]
    expected_diff_rows: tuple[Mapping[str, str], ...]
    source_artifact_relpath: str
    source_artifact_bytes: bytes
    source_artifact_sha256: str
    _mint_token: InitVar[object] = field(kw_only=True)
    validation_status: str = VALIDATION_STATUS
    product_authority: str = PRODUCT_AUTHORITY

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("UntargetedCidNlGoldenPathProjection is final")

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _GOLDEN_PATH_PROJECTION_MINT_TOKEN:
            raise UntargetedCidNlGoldenPathError(
                "golden-path projections must be minted by their orchestration owner"
            )
        if self.validation_status != VALIDATION_STATUS:
            raise UntargetedCidNlGoldenPathError(
                f"validation_status must be {VALIDATION_STATUS!r}"
            )
        if self.product_authority != PRODUCT_AUTHORITY:
            raise UntargetedCidNlGoldenPathError(
                "prepared golden path cannot self-authorize Matrix writes"
            )
        expected_sha = hashlib.sha256(self.source_artifact_bytes).hexdigest().upper()
        if self.source_artifact_sha256 != expected_sha:
            raise UntargetedCidNlGoldenPathError(
                "source artifact SHA does not match its exact bytes"
            )


@final
@dataclass(frozen=True, slots=True)
class UntargetedCidNlModeTrackProjection:
    """Prepared multi-row ModeTrack facts immediately before the checked manifest."""

    mode_track_facts: tuple[ModeTrackConservativeBackfillGroupFacts, ...]
    backfill_decisions: tuple[ConservativeBackfillCellDecision, ...]
    matrix_header: tuple[str, ...]
    input_quant_matrix_rows: tuple[Mapping[str, str], ...]
    input_matrix_identity_rows: tuple[Mapping[str, str], ...]
    pending_acceptance_rows: tuple[Mapping[str, str], ...]
    expected_diff_rows: tuple[Mapping[str, str], ...]
    source_artifact_relpath: str
    source_artifact_bytes: bytes
    source_artifact_sha256: str
    _mint_token: InitVar[object] = field(kw_only=True)
    validation_status: str = VALIDATION_STATUS
    product_authority: str = PRODUCT_AUTHORITY

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _GOLDEN_PATH_PROJECTION_MINT_TOKEN:
            raise UntargetedCidNlGoldenPathError(
                "projection requires the untargeted CID-NL golden-path projection owner"
            )
        expected = hashlib.sha256(self.source_artifact_bytes).hexdigest().upper()
        if self.source_artifact_sha256 != expected or self.backfill_decisions:
            raise UntargetedCidNlGoldenPathError("invalid ModeTrack projection")


_Projection = UntargetedCidNlGoldenPathProjection | UntargetedCidNlModeTrackProjection


def build_untargeted_cid_nl_golden_path(
    *,
    processing_owners: ProcessingOwnerReferences,
    method_subject: CidNlMethodSubject,
    sample_inputs: tuple[UntargetedCidNlAssessmentInput, ...],
    recovery_inputs: tuple[UntargetedCidNlRecoveryInput, ...] = (),
    source_artifact_relpath: str,
) -> UntargetedCidNlGoldenPathProjection:
    """Prepare one exact Untargeted group and its manifest-gated Matrix inputs."""

    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise UntargetedCidNlGoldenPathError(
            "processing_owners must be validated ProcessingOwnerReferences"
        )
    if (
        not isinstance(method_subject, CidNlMethodSubject)
        or method_subject.subject_kind != "untargeted_precursor"
    ):
        raise UntargetedCidNlGoldenPathError(
            "golden path requires a request-pinned Untargeted CID-NL subject"
        )
    source_artifact_relpath = _validate_source_relpath(source_artifact_relpath)
    ordered_inputs = _canonical_sample_inputs(sample_inputs, processing_owners)
    if any(
        item.assessment.content.method_subject != method_subject
        for item in ordered_inputs
    ):
        raise UntargetedCidNlGoldenPathError(
            "sample assessments do not share the exact method subject"
        )
    spine = build_cid_nl_reference_spine(
        processing_owners=processing_owners,
        method_subject=method_subject,
        sample_assessments=tuple(
            (item.sample, item.assessment) for item in ordered_inputs
        ),
        config=CID_NL_ALIGNMENT_CONFIG,
    )
    if spine.group is None or not spine.cells:
        raise UntargetedCidNlGoldenPathError(
            "golden path requires at least one direct SUPPORT to establish a group"
        )
    discovery_projection_refs = tuple(
        sorted(
            (
                projection
                for item in ordered_inputs
                for projection in item.discovery_projections
            ),
            key=lambda projection: projection.artifact.evidence_artifact_id,
        )
    )
    sample_population = tuple(
        sorted(
            processing_owners.sample_resolutions,
            key=lambda sample: sample.identity.sample_identity_id,
        )
    )
    facts = assess_untargeted_conservative_backfill_group(
        spine.untargeted_projections,
        sample_population=sample_population,
    )
    if not facts.group_frozen:
        raise UntargetedCidNlGoldenPathError(
            "golden path requires one canonical cell for every request sample"
        )
    recoveries = _canonical_recovery_inputs(
        recovery_inputs,
        processing_owners=processing_owners,
        method_subject=method_subject,
    )
    cell_by_sample_id = {
        cell.content.canonical_sample_identity_id: cell for cell in spine.cells
    }
    universes: list[ConservativeBackfillRecoveryCandidateUniverse] = []
    universe_by_sample_id: dict[str, ConservativeBackfillRecoveryCandidateUniverse] = {}
    for recovery in recoveries:
        sample_id = recovery.sample.identity.sample_identity_id
        cell = cell_by_sample_id[sample_id]
        if not (
            cell.content.method_result == "HOLD"
            or is_unsearched_cid_nl_discovery_assessment(cell.method_assessment)
        ):
            raise UntargetedCidNlGoldenPathError(
                "bounded recovery requires an existing HOLD "
                "or unsearched Discovery cell"
            )
        universe = mint_conservative_backfill_recovery_candidate_universe(
            processing_owners=processing_owners,
            sample=recovery.sample,
            group_facts=(facts,),
            candidate_assessment=recovery.candidate_assessment,
        )
        universes.append(universe)
        universe_by_sample_id[sample_id] = universe

    ordered_samples = _samples_in_injection_order(processing_owners)
    decisions = tuple(
        map_conservative_backfill_candidates(
            (facts,),
            sample=sample,
            candidate_universes=(
                universe_by_sample_id[sample.identity.sample_identity_id],
            )
            if sample.identity.sample_identity_id in universe_by_sample_id
            else (),
        )[0]
        for sample in ordered_samples
    )
    source_bytes, source_rows = _source_artifact_bytes(
        processing_owners=processing_owners,
        method_subject=method_subject,
        sample_inputs=ordered_inputs,
        spine=spine,
        decisions=decisions,
    )
    source_sha = hashlib.sha256(source_bytes).hexdigest().upper()
    group = spine.group
    peak_hypothesis_id = group.group_hypothesis_id
    feature_family_id = group.group_hypothesis_id
    matrix_header = (
        "Mz",
        "RT",
        *(sample.metadata.raw_stem for sample in ordered_samples),
    )
    matrix_row = {
        "Mz": canonical_mz_text(group.group_center_mz),
        "RT": _format_number(group.group_center_rt),
    }
    cell_by_sample_id = {
        cell.content.canonical_sample_identity_id: cell for cell in spine.cells
    }
    for sample in ordered_samples:
        cell = cell_by_sample_id[sample.identity.sample_identity_id]
        matrix_row[sample.metadata.raw_stem] = _direct_support_area(cell)
    identity_row = {
        "matrix_row_index": "1",
        "Mz": canonical_mz_text(group.group_center_mz),
        "RT": matrix_row["RT"],
        "peak_hypothesis_id": peak_hypothesis_id,
        "row_identity_basis": "cid_nl_method_subject_group",
        "source_feature_family_ids": feature_family_id,
    }
    decision_by_sample_id = {
        decision.target_cell.content.canonical_sample_identity_id: decision
        for decision in decisions
    }
    accepted_sample_values = {
        sample.metadata.raw_stem: _mapped_backfill_area(
            decision_by_sample_id[sample.identity.sample_identity_id]
        )
        for sample in ordered_samples
        if decision_by_sample_id[sample.identity.sample_identity_id].disposition
        == "candidate_mapped"
    }
    detected_count = sum(
        1 for sample_stem in matrix_header[2:] if matrix_row.get(sample_stem, "")
    )
    backfilled_count = len(accepted_sample_values)
    available_count = detected_count + backfilled_count
    missing_count = len(ordered_samples) - available_count
    backfill_fraction = (
        0.0 if available_count == 0 else backfilled_count / available_count
    )
    source_row_sha_by_sample = {
        row["sample_stem"]: hashlib.sha256(
            json.dumps(
                row,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        )
        .hexdigest()
        .upper()
        for row in source_rows
    }
    acceptance_rows = tuple(
        {
            "schema_version": "production_acceptance_manifest_v1",
            "peak_hypothesis_id": peak_hypothesis_id,
            "sample_stem": sample_stem,
            "feature_family_id": feature_family_id,
            "acceptance_decision": "accept_basic_backfill",
            "acceptance_basis": "machine_basic",
            "truth_status": "not_truth_claimed",
            "shadow_only": "FALSE",
            "write_authority": "TRUE",
            "matrix_write_allowed": "TRUE",
            "quant_value": value,
            "quant_value_source": "owner_peak_observation_raw_area",
            "matrix_area_source": (
                "peak_observation.integration.area_raw_counts_seconds"
            ),
            "detected_count": str(detected_count),
            "backfilled_count": str(backfilled_count),
            "quant_available_count": str(available_count),
            "missing_count": str(missing_count),
            "backfill_fraction": f"{backfill_fraction:.6f}",
            "prevalence_flags": "",
            "hard_blocker_rule_ids": "",
            "triggered_risk_rule_ids": "",
            "closure_rule_ids": "",
            "decision_reason": "owner_mapped_unique_recovery_candidate",
            "next_evidence_needed": "",
            "doublet_status": "no_doublet_claim",
            "reference_side": "not_applicable",
            "doublet_allowed": "TRUE",
            "doublet_source_relpath": source_artifact_relpath,
            "doublet_source_sha256": source_sha,
            "source_artifact_relpath": source_artifact_relpath,
            "source_artifact_sha256": source_sha,
            "source_row_sha256": source_row_sha_by_sample[sample_stem],
            "manifest_sha256": "",
            "acceptance_contract_version": (
                "production_acceptance_manifest_contract_v1"
            ),
        }
        for sample_stem, value in sorted(accepted_sample_values.items())
    )
    expected_diff_rows = tuple(
        {
            "schema_version": "quant_matrix_version_expected_diff_v1",
            "peak_hypothesis_id": peak_hypothesis_id,
            "sample_stem": sample_stem,
            "baseline_value": "",
            "activated_value": value,
            "expected_matrix_effect": "write_accepted_backfill",
            "expected_reason": "untargeted_cid_nl_owner_mapped_recovery",
        }
        for sample_stem, value in sorted(accepted_sample_values.items())
    )
    return UntargetedCidNlGoldenPathProjection(
        method_subject=method_subject,
        discovery_projection_refs=discovery_projection_refs,
        spine=spine,
        backfill_facts=facts,
        backfill_decisions=decisions,
        recovery_universes=tuple(universes),
        matrix_header=matrix_header,
        input_quant_matrix_rows=_freeze_rows((matrix_row,)),
        input_matrix_identity_rows=_freeze_rows((identity_row,)),
        pending_acceptance_rows=_freeze_rows(acceptance_rows),
        expected_diff_rows=_freeze_rows(expected_diff_rows),
        source_artifact_relpath=source_artifact_relpath,
        source_artifact_bytes=source_bytes,
        source_artifact_sha256=source_sha,
        _mint_token=_GOLDEN_PATH_PROJECTION_MINT_TOKEN,
    )


def build_untargeted_cid_nl_quant_matrix_version(
    projection: _Projection,
    *,
    production_acceptance_manifest_path: Path,
    manifest_root: Path,
) -> QuantMatrixVersionRows:
    """Revalidate the manifest file before entering the QuantMatrix owner."""

    if not isinstance(projection, _Projection):
        raise UntargetedCidNlGoldenPathError(
            "QuantMatrix activation requires a prepared golden-path projection"
        )
    checked = _load_terminal_acceptance_manifest(
        projection=projection,
        manifest_path=production_acceptance_manifest_path,
        manifest_root=manifest_root,
    )
    rows = build_quant_matrix_version_from_checked_manifest(
        matrix_header=projection.matrix_header,
        input_quant_matrix_rows=projection.input_quant_matrix_rows,
        input_matrix_identity_rows=projection.input_matrix_identity_rows,
        checked_manifest=checked,
        expected_diff_rows=projection.expected_diff_rows,
    )
    _validate_terminal_rows(projection, rows)
    return rows


def build_untargeted_cid_nl_mode_track_projection(
    *,
    processing_owners: ProcessingOwnerReferences,
    mode_track_facts: tuple[ModeTrackConservativeBackfillGroupFacts, ...],
    source_artifact_relpath: str,
) -> UntargetedCidNlModeTrackProjection:
    """Render Backfill-approved exact owners without recomputing assignment or S/K."""

    source_artifact_relpath = _validate_source_relpath(source_artifact_relpath)
    if not mode_track_facts:
        raise UntargetedCidNlGoldenPathError(
            "ModeTrack projection requires Backfill activation facts"
        )
    model_set = mode_track_facts[0].model_set
    expected_groups = model_set.family_context.mode_tracks
    if len(mode_track_facts) != len(expected_groups) or any(
        fact.model_set is not model_set or fact.group is not group
        for fact, group in zip(mode_track_facts, expected_groups, strict=True)
    ):
        raise UntargetedCidNlGoldenPathError("incomplete ModeTrack group population")
    owner_key = (
        processing_owners.request.processing_request_id,
        processing_owners.attempt.processing_attempt_id,
    )
    if owner_key != (model_set.processing_request_id, model_set.processing_attempt_id):
        raise UntargetedCidNlGoldenPathError("ModeTrack facts crossed owners")
    owner_population = tuple(
        sorted(
            processing_owners.sample_resolutions,
            key=lambda sample: sample.identity.sample_identity_id,
        )
    )
    if any(
        len(fact.sample_population) != len(owner_population)
        or any(
            declared is not owned
            for declared, owned in zip(
                fact.sample_population,
                owner_population,
                strict=True,
            )
        )
        for fact in mode_track_facts
    ):
        raise UntargetedCidNlGoldenPathError(
            "ModeTrack facts crossed the processing-owner sample population"
        )
    samples = _samples_in_injection_order(processing_owners)
    sample_by_id = {sample.identity.sample_identity_id: sample for sample in samples}
    matrix_header = ("Mz", "RT", *(sample.metadata.raw_stem for sample in samples))
    matrix_rows = []
    identity_rows = []
    proposals = []
    render_facts = tuple(
        sorted(
            mode_track_facts,
            key=lambda fact: (
                fact.group.public_family_id,
                fact.group.group_center_mz,
                fact.group.group_center_rt,
                fact.group.group_hypothesis_id,
            ),
        )
    )
    for row_index, fact in enumerate(render_facts, start=1):
        group_id = fact.group.group_hypothesis_id
        direct = {
            item.canonical_sample_identity_id: item
            for item in fact.direct_support_assignments
        }
        candidates = {
            item.canonical_sample_identity_id: item
            for item in fact.exact_owner_candidate_assignments
        }
        if len(candidates) != len(fact.exact_owner_candidate_assignments) or set(
            direct
        ).intersection(candidates):
            raise UntargetedCidNlGoldenPathError("conflicting ModeTrack observations")
        row = {
            "Mz": canonical_mz_text(fact.group.group_center_mz),
            "RT": _format_number(fact.group.group_center_rt),
        }
        for sample_id, sample in sample_by_id.items():
            assignment = direct.get(sample_id)
            row[sample.metadata.raw_stem] = (
                "" if assignment is None else _mode_track_assignment_area(assignment)
            )
        matrix_rows.append(row)
        identity_rows.append(
            {
                "matrix_row_index": str(row_index),
                "Mz": canonical_mz_text(fact.group.group_center_mz),
                "RT": row["RT"],
                "peak_hypothesis_id": group_id,
                "row_identity_basis": "cid_nl_mode_track_group",
                "source_feature_family_ids": group_id,
            }
        )
        accepted = candidates if fact.may_attempt_recovery else {}
        detected_count, backfilled_count = len(direct), len(accepted)
        available_count = detected_count + backfilled_count
        for sample_id, assignment in accepted.items():
            value = _mode_track_assignment_area(assignment)
            hypothesis = assignment.peak_hypothesis
            if hypothesis is None:
                raise UntargetedCidNlGoldenPathError(
                    "ModeTrack source assignment lost its PeakHypothesis"
                )
            observation = hypothesis.peak_observation_refs[0]
            proposals.append(
                {
                    "group_hypothesis_id": group_id,
                    "sample_identity_id": sample_id,
                    "sample_stem": sample_by_id[sample_id].metadata.raw_stem,
                    "physical_ids": [
                        assignment.assignment_id,
                        assignment.peak_hypothesis_id,
                        assignment.peak_observation_content_id,
                    ],
                    "peak_observation_artifact_id": (
                        observation.artifact.evidence_artifact_id
                    ),
                    "peak_observation_artifact_content_id": (
                        observation.artifact.content_identity.artifact_content_id
                    ),
                    "peak_observation": json.loads(
                        canonical_peak_observation_ref_bytes(
                            observation.content,
                            observation.generation_provenance,
                        )
                    ),
                    "quant_value": value,
                    "detected_count": detected_count,
                    "backfilled_count": backfilled_count,
                    "available_count": available_count,
                    "owner_ids": [
                        *owner_key,
                        model_set.family_context_id,
                        model_set.model_set_id,
                    ],
                    "acquisition_ids": [
                        assignment.acquisition_source_id,
                        assignment.injection_order,
                    ],
                    "activation": [
                        fact.sample_count_n,
                        fact.minimum_support_k,
                        fact.direct_support_count_s,
                    ],
                    "direct_support_assignment_ids": [
                        item.assignment_id for item in fact.direct_support_assignments
                    ],
                }
            )
    source_bytes = _canonical_json_bytes({
        "schema_version": "mode_track_projection_source_v2", "rows": proposals
    })
    source_sha = hashlib.sha256(source_bytes).hexdigest().upper()
    acceptance_rows = tuple(
        _mode_track_acceptance_row(
            proposal=proposal,
            sample_count=len(samples),
            source_relpath=source_artifact_relpath,
            source_sha=source_sha,
        )
        for proposal in proposals
    )
    expected_diff_rows = tuple(
        {
            "schema_version": "quant_matrix_version_expected_diff_v1",
            "peak_hypothesis_id": row["peak_hypothesis_id"],
            "sample_stem": row["sample_stem"],
            "baseline_value": "",
            "activated_value": row["quant_value"],
            "expected_matrix_effect": "write_accepted_backfill",
            "expected_reason": "untargeted_cid_nl_exact_mode_track_owner",
        }
        for row in acceptance_rows
    )
    return UntargetedCidNlModeTrackProjection(
        mode_track_facts=render_facts,
        backfill_decisions=(),
        matrix_header=matrix_header,
        input_quant_matrix_rows=_freeze_rows(matrix_rows),
        input_matrix_identity_rows=_freeze_rows(identity_rows),
        pending_acceptance_rows=_freeze_rows(acceptance_rows),
        expected_diff_rows=_freeze_rows(expected_diff_rows),
        source_artifact_relpath=source_artifact_relpath,
        source_artifact_bytes=source_bytes,
        source_artifact_sha256=source_sha,
        _mint_token=_GOLDEN_PATH_PROJECTION_MINT_TOKEN,
    )


def _load_terminal_acceptance_manifest(
    *,
    projection: _Projection,
    manifest_path: Path,
    manifest_root: Path,
) -> CheckedProductionAcceptanceManifest:
    try:
        checked_manifest = load_checked_production_acceptance_manifest(
            manifest_path=manifest_path,
            repo_root=manifest_root,
        )
    except ProductionAcceptanceManifestError as exc:
        raise UntargetedCidNlGoldenPathError(
            "ProductionAcceptanceManifest failed canonical authority validation: "
            + str(exc)
        ) from exc
    checked = checked_manifest.rows
    if len(checked) != len(projection.pending_acceptance_rows):
        raise UntargetedCidNlGoldenPathError(
            "checked manifest row count differs from owner-prepared acceptance"
        )
    for pending, accepted in zip(
        projection.pending_acceptance_rows,
        checked,
        strict=True,
    ):
        for field_name, expected in pending.items():
            if field_name == "manifest_sha256":
                continue
            if accepted.get(field_name, "") != expected:
                raise UntargetedCidNlGoldenPathError(
                    "checked manifest rewrites owner-prepared field "
                    f"{field_name!r}"
                )
    if checked_manifest.canonical_manifest_sha256 != (
        production_acceptance_manifest_sha256(checked)
    ):
        raise UntargetedCidNlGoldenPathError(
            "checked manifest SHA-256 does not match its canonical file rows"
        )
    return checked_manifest


def _canonical_sample_inputs(
    value: object,
    processing_owners: ProcessingOwnerReferences,
) -> tuple[UntargetedCidNlAssessmentInput, ...]:
    if (
        not isinstance(value, tuple)
        or not value
        or any(not isinstance(item, UntargetedCidNlAssessmentInput) for item in value)
    ):
        raise UntargetedCidNlGoldenPathError(
            "sample_inputs must contain one immutable input per request sample"
        )
    by_sample_id = {item.sample.identity.sample_identity_id: item for item in value}
    owner_sample_ids = {
        sample.identity.sample_identity_id
        for sample in processing_owners.sample_resolutions
    }
    if len(by_sample_id) != len(value) or set(by_sample_id) != owner_sample_ids:
        raise UntargetedCidNlGoldenPathError(
            "sample_inputs must close the exact processing-request population"
        )
    return tuple(by_sample_id[sample_id] for sample_id in sorted(by_sample_id))


def _canonical_recovery_inputs(
    value: object,
    *,
    processing_owners: ProcessingOwnerReferences,
    method_subject: CidNlMethodSubject,
) -> tuple[UntargetedCidNlRecoveryInput, ...]:
    if not isinstance(value, tuple) or any(
        not isinstance(item, UntargetedCidNlRecoveryInput) for item in value
    ):
        raise UntargetedCidNlGoldenPathError(
            "recovery_inputs must be an immutable tuple"
        )
    owner_sample_ids = {
        sample.identity.sample_identity_id
        for sample in processing_owners.sample_resolutions
    }
    by_sample_id: dict[str, UntargetedCidNlRecoveryInput] = {}
    for item in value:
        sample_id = item.sample.identity.sample_identity_id
        if sample_id not in owner_sample_ids:
            raise UntargetedCidNlGoldenPathError(
                "recovery input sample is outside the processing request"
            )
        if sample_id in by_sample_id:
            raise UntargetedCidNlGoldenPathError(
                "one sample may provide at most one recovery candidate universe"
            )
        if item.candidate_assessment.content.method_subject != method_subject:
            raise UntargetedCidNlGoldenPathError(
                "recovery assessment references a different method subject"
            )
        by_sample_id[sample_id] = item
    return tuple(by_sample_id[sample_id] for sample_id in sorted(by_sample_id))


def _samples_in_injection_order(
    processing_owners: ProcessingOwnerReferences,
) -> tuple[ResolvedCanonicalSampleIdentity, ...]:
    order_by_key = {
        binding.adapter_input_key: binding.injection_order
        for binding in processing_owners.request.acquisition_bindings
    }
    return tuple(
        sorted(
            processing_owners.sample_resolutions,
            key=lambda sample: order_by_key[sample.lookup_token],
        )
    )


def _direct_support_area(cell) -> str:
    if cell.content.method_result != "SUPPORT":
        return ""
    observation = cell.peak_observation
    if observation is None:
        raise UntargetedCidNlGoldenPathError(
            "SUPPORT cell lost its owner-backed physical observation"
        )
    return _format_number(observation.content.integration.area_raw_counts_seconds)


def _mapped_backfill_area(decision: ConservativeBackfillCellDecision) -> str:
    if decision.disposition != "candidate_mapped":
        raise UntargetedCidNlGoldenPathError(
            "only candidate_mapped recovery may prepare a numeric Backfill value"
        )
    candidate = decision.matched_candidate
    if candidate is None:
        raise UntargetedCidNlGoldenPathError(
            "candidate_mapped recovery lost its exact physical observation"
        )
    return _format_number(
        candidate.peak_observation.content.integration.area_raw_counts_seconds
    )


def _source_artifact_bytes(
    *,
    processing_owners: ProcessingOwnerReferences,
    method_subject: CidNlMethodSubject,
    sample_inputs: tuple[UntargetedCidNlAssessmentInput, ...],
    spine: CidNlReferenceSpine,
    decisions: tuple[ConservativeBackfillCellDecision, ...],
) -> tuple[bytes, tuple[dict[str, str], ...]]:
    if not decisions or any(
        decision.group_facts is not decisions[0].group_facts for decision in decisions
    ):
        raise UntargetedCidNlGoldenPathError(
            "source artifact requires one exact frozen Backfill fact set"
        )
    facts = decisions[0].group_facts
    input_by_sample_id = {
        item.sample.identity.sample_identity_id: item for item in sample_inputs
    }
    rows = tuple(
        {
            "sample_stem": _sample_stem_for_decision(decision),
            "cell_artifact_id": decision.target_cell.artifact.evidence_artifact_id,
            "assessment_artifact_id": (
                decision.target_cell.method_assessment.artifact.evidence_artifact_id
            ),
            "canonical_evidence_artifact_ids": ";".join(
                evidence.artifact.evidence_artifact_id
                for evidence in decision.target_cell.method_assessment.evidence_refs
            ),
            "discovery_projection_artifact_ids": ";".join(
                projection.artifact.evidence_artifact_id
                for projection in input_by_sample_id[
                    decision.target_cell.content.canonical_sample_identity_id
                ].discovery_projections
            ),
            "method_result": decision.target_cell.content.method_result or "",
            "backfill_disposition": decision.disposition,
            "backfill_reason_codes": ";".join(decision.reason_codes),
            "recovery_universe_artifact_id": ""
            if decision.candidate_universe is None
            else decision.candidate_universe.artifact.evidence_artifact_id,
            "recovery_assessment_artifact_id": ""
            if decision.candidate_universe is None
            else (
                decision.candidate_universe.candidate_assessment.artifact.evidence_artifact_id
            ),
            "matched_peak_observation_content_id": ""
            if decision.matched_candidate is None
            else (
                decision.matched_candidate.peak_observation.content.peak_observation_content_id
            ),
            "numeric_value_kind": _numeric_value_kind(decision),
            "raw_area_counts_seconds": _decision_raw_area(decision),
        }
        for decision in decisions
    )
    payload = {
        "schema_version": SOURCE_SCHEMA_VERSION,
        "validation_status": VALIDATION_STATUS,
        "product_authority": PRODUCT_AUTHORITY,
        "processing_request_id": processing_owners.request.processing_request_id,
        "processing_attempt_id": processing_owners.attempt.processing_attempt_id,
        "method_subject": method_subject.request_payload(),
        "group_hypothesis_id": spine.group.group_hypothesis_id
        if spine.group is not None
        else None,
        "group_owner_ids": [] if spine.group is None else list(spine.group.owner_ids),
        "alignment_config": asdict(facts.group_context.config),
        "backfill_policy": {
            "policy_version": facts.policy_version,
            "sample_count_n": facts.sample_count_n,
            "minimum_support_k": facts.minimum_support_k,
            "direct_support_count_s": facts.direct_support_count_s,
            "activation_eligible_sample_ids": list(
                facts.activation_eligible_sample_ids
            ),
            "direct_support_sample_ids": list(facts.direct_support_sample_ids),
            "activation_eligible_support_sample_ids": list(
                facts.activation_eligible_support_sample_ids
            ),
            "support_trace_artifact_ids": [
                trace.artifact.evidence_artifact_id
                for trace in facts.support_trace_refs
            ],
        },
        "rows": rows,
    }
    return (
        (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8"),
        rows,
    )


def _sample_stem_for_decision(
    decision: ConservativeBackfillCellDecision,
) -> str:
    sample_id = decision.target_cell.content.canonical_sample_identity_id
    matches = tuple(
        sample.metadata.raw_stem
        for sample in decision.group_facts.sample_population
        if sample.identity.sample_identity_id == sample_id
    )
    if len(matches) != 1:
        raise UntargetedCidNlGoldenPathError(
            "decision sample is outside the frozen group"
        )
    return matches[0]


def _numeric_value_kind(decision: ConservativeBackfillCellDecision) -> str:
    if decision.target_cell.content.method_result == "SUPPORT":
        return "direct_support"
    if decision.disposition == "candidate_mapped":
        return "accepted_backfill_input"
    return "blank"


def _decision_raw_area(decision: ConservativeBackfillCellDecision) -> str:
    if decision.target_cell.content.method_result == "SUPPORT":
        return _direct_support_area(decision.target_cell)
    if decision.disposition == "candidate_mapped":
        return _mapped_backfill_area(decision)
    return ""


def _validate_source_relpath(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise UntargetedCidNlGoldenPathError(
            "source_artifact_relpath must be normalized non-empty text"
        )
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or PureWindowsPath(value).drive or ".." in path.parts:
        raise UntargetedCidNlGoldenPathError(
            "source_artifact_relpath must stay under the manifest repo root"
        )
    return normalized


def _format_number(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UntargetedCidNlGoldenPathError("numeric projection requires a number")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0.0:
        raise UntargetedCidNlGoldenPathError(
            "numeric projection requires a finite non-negative value"
        )
    return f"{parsed:.12g}"


def _mode_track_assignment_area(assignment: ModeTrackAssignment) -> str:
    hypothesis = assignment.peak_hypothesis
    if hypothesis is None:
        raise UntargetedCidNlGoldenPathError("ModeTrack assignment has no hypothesis")
    return _format_number(hypothesis.integration.area_raw_counts_seconds)


def _canonical_json_bytes(value: object) -> bytes:
    text = json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )
    return f"{text}\n".encode("utf-8")


def _mode_track_acceptance_row(
    *,
    proposal: Mapping[str, object],
    sample_count: int,
    source_relpath: str,
    source_sha: str,
) -> Mapping[str, str]:
    detected = cast(int, proposal["detected_count"])
    backfilled = cast(int, proposal["backfilled_count"])
    available = cast(int, proposal["available_count"])
    return {
        "schema_version": "production_acceptance_manifest_v1",
        "peak_hypothesis_id": str(proposal["group_hypothesis_id"]),
        "sample_stem": str(proposal["sample_stem"]),
        "feature_family_id": str(proposal["group_hypothesis_id"]),
        "acceptance_decision": "accept_basic_backfill",
        "acceptance_basis": "machine_basic",
        "truth_status": "not_truth_claimed",
        "shadow_only": "FALSE",
        "write_authority": "TRUE",
        "matrix_write_allowed": "TRUE",
        "quant_value": str(proposal["quant_value"]),
        "quant_value_source": "owner_peak_observation_raw_area",
        "matrix_area_source": "peak_observation.integration.area_raw_counts_seconds",
        "detected_count": str(detected),
        "backfilled_count": str(backfilled),
        "quant_available_count": str(available),
        "missing_count": str(sample_count - available),
        "backfill_fraction": f"{backfilled / available:.6f}",
        "prevalence_flags": "",
        "hard_blocker_rule_ids": "",
        "triggered_risk_rule_ids": "",
        "closure_rule_ids": "",
        "decision_reason": "exact_mode_track_owner_after_backfill_activation",
        "next_evidence_needed": "",
        "doublet_status": "no_doublet_claim",
        "reference_side": "not_applicable",
        "doublet_allowed": "TRUE",
        "doublet_source_relpath": source_relpath,
        "doublet_source_sha256": source_sha,
        "source_artifact_relpath": source_relpath,
        "source_artifact_sha256": source_sha,
        "source_row_sha256": hashlib.sha256(_canonical_json_bytes(proposal))
        .hexdigest()
        .upper(),
        "manifest_sha256": "",
        "acceptance_contract_version": "production_acceptance_manifest_contract_v1",
    }


def _freeze_rows(
    rows: Sequence[Mapping[str, str]],
) -> tuple[Mapping[str, str], ...]:
    """Detach and deep-freeze string-only rows at the owner mint boundary."""

    return tuple(MappingProxyType(dict(row)) for row in rows)


def _validate_terminal_rows(
    projection: _Projection,
    rows: QuantMatrixVersionRows,
) -> None:
    if isinstance(projection, UntargetedCidNlModeTrackProjection):
        if len(rows.quant_matrix_rows) != len(projection.input_quant_matrix_rows):
            raise UntargetedCidNlGoldenPathError("ModeTrack Matrix rows changed")
        accepted = {
            (row["peak_hypothesis_id"], row["sample_stem"])
            for row in projection.pending_acceptance_rows
        }
        for identity, baseline, result in zip(
            projection.input_matrix_identity_rows,
            projection.input_quant_matrix_rows,
            rows.quant_matrix_rows,
            strict=True,
        ):
            group_id = identity["peak_hypothesis_id"]
            expected = {
                sample
                for sample in projection.matrix_header[2:]
                if baseline.get(sample, "") or (group_id, sample) in accepted
            }
            actual = {
                sample
                for sample in projection.matrix_header[2:]
                if result.get(sample, "")
            }
            if actual != expected:
                raise UntargetedCidNlGoldenPathError("unauthorized ModeTrack value")
        return
    if len(rows.quant_matrix_rows) != 1:
        raise UntargetedCidNlGoldenPathError(
            "golden path must produce exactly one QuantMatrix group row"
        )
    matrix_row = rows.quant_matrix_rows[0]
    accepted_samples = {
        row["sample_stem"] for row in projection.pending_acceptance_rows
    }
    direct_samples = {
        sample_stem
        for sample_stem in projection.matrix_header[2:]
        if projection.input_quant_matrix_rows[0].get(sample_stem, "")
    }
    numeric_samples = {
        sample_stem
        for sample_stem in projection.matrix_header[2:]
        if matrix_row.get(sample_stem, "")
    }
    if numeric_samples != direct_samples | accepted_samples:
        raise UntargetedCidNlGoldenPathError(
            "terminal Matrix contains an unresolved, rejected, or technical value"
        )
    provenance_by_sample = {
        row["sample_stem"]: row for row in rows.cell_provenance_rows
    }
    if any(
        provenance_by_sample[sample]["cell_status"] != "detected"
        for sample in direct_samples
    ) or any(
        provenance_by_sample[sample]["cell_status"] != "accepted_backfill"
        for sample in accepted_samples
    ):
        raise UntargetedCidNlGoldenPathError(
            "terminal provenance collapses direct and Backfill values"
        )


__all__ = [
    "PRODUCT_AUTHORITY",
    "SOURCE_SCHEMA_VERSION",
    "CID_NL_ALIGNMENT_CONFIG",
    "UntargetedCidNlAssessmentInput",
    "UntargetedCidNlGoldenPathError",
    "UntargetedCidNlGoldenPathProjection",
    "UntargetedCidNlModeTrackProjection",
    "UntargetedCidNlRecoveryInput",
    "VALIDATION_STATUS",
    "build_untargeted_cid_nl_golden_path",
    "build_untargeted_cid_nl_mode_track_projection",
    "build_untargeted_cid_nl_quant_matrix_version",
]

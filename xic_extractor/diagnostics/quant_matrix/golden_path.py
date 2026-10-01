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
from typing import final

from xic_extractor.alignment.cid_nl_evidence_spine import (
    CidNlReferenceSpine,
    build_cid_nl_reference_spine,
)
from xic_extractor.alignment.cid_nl_inputs import (
    CID_NL_ALIGNMENT_CONFIG,
    UntargetedCidNlAssessmentInput,
    UntargetedCidNlBackfillInput,
    UntargetedCidNlGoldenPathError,
)
from xic_extractor.alignment.conservative_backfill import (
    ConservativeBackfillCandidateUniverse,
    ConservativeBackfillCellDecision,
    ConservativeBackfillGroupFacts,
    assess_untargeted_conservative_backfill_group,
    map_conservative_backfill_candidates,
    mint_conservative_backfill_candidate_universe,
)
from xic_extractor.diagnostics.quant_matrix.acceptance import (
    CheckedProductionAcceptanceManifest,
    ProductionAcceptanceManifestError,
    build_quant_matrix_version_from_checked_manifest,
    load_checked_production_acceptance_manifest,
)
from xic_extractor.diagnostics.quant_matrix.version import (
    QuantMatrixVersionRows,
    production_acceptance_manifest_sha256,
)
from xic_extractor.discovery.cid_nl_evidence_projection import (
    UntargetedCidNlProjectionRef,
)
from xic_extractor.output.alignment.matrix_output_contract import canonical_mz_text
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    is_unsearched_cid_nl_discovery_assessment,
)
from xic_extractor.peak_detection.cid_nl_method_subject import CidNlMethodSubject
from xic_extractor.processing_identity import ProcessingOwnerReferences
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

VALIDATION_STATUS = "production_candidate"
PRODUCT_AUTHORITY = "none_until_checked_manifest"
SOURCE_SCHEMA_VERSION = "untargeted_cid_nl_golden_path_source"
_GOLDEN_PATH_PROJECTION_MINT_TOKEN = object()


@final
@dataclass(frozen=True, slots=True)
class UntargetedCidNlGoldenPathProjection:
    """Prepared owner facts immediately before the file-level manifest gate."""

    method_subject: CidNlMethodSubject
    discovery_projection_refs: tuple[UntargetedCidNlProjectionRef, ...]
    spine: CidNlReferenceSpine
    backfill_facts: ConservativeBackfillGroupFacts
    backfill_decisions: tuple[ConservativeBackfillCellDecision, ...]
    backfill_universes: tuple[
        ConservativeBackfillCandidateUniverse,
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


def build_untargeted_cid_nl_golden_path(
    *,
    processing_owners: ProcessingOwnerReferences,
    method_subject: CidNlMethodSubject,
    sample_inputs: tuple[UntargetedCidNlAssessmentInput, ...],
    backfill_inputs: tuple[UntargetedCidNlBackfillInput, ...] = (),
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
    backfills = _canonical_backfill_inputs(
        backfill_inputs,
        processing_owners=processing_owners,
        method_subject=method_subject,
    )
    cell_by_sample_id = {
        cell.content.canonical_sample_identity_id: cell for cell in spine.cells
    }
    universes: list[ConservativeBackfillCandidateUniverse] = []
    universe_by_sample_id: dict[str, ConservativeBackfillCandidateUniverse] = {}
    for backfill in backfills:
        sample_id = backfill.sample.identity.sample_identity_id
        cell = cell_by_sample_id[sample_id]
        if not (
            cell.content.method_result == "HOLD"
            or is_unsearched_cid_nl_discovery_assessment(cell.method_assessment)
        ):
            raise UntargetedCidNlGoldenPathError(
                "bounded backfill requires an existing HOLD "
                "or unsearched Discovery cell"
            )
        universe = mint_conservative_backfill_candidate_universe(
            processing_owners=processing_owners,
            sample=backfill.sample,
            group_facts=(facts,),
            candidate_assessment=backfill.candidate_assessment,
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
            "schema_version": "production_acceptance_manifest",
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
                "production_acceptance_manifest_contract"
            ),
        }
        for sample_stem, value in sorted(accepted_sample_values.items())
    )
    expected_diff_rows = tuple(
        {
            "schema_version": "quant_matrix_version_expected_diff",
            "peak_hypothesis_id": peak_hypothesis_id,
            "sample_stem": sample_stem,
            "baseline_value": "",
            "activated_value": value,
            "expected_matrix_effect": "write_accepted_backfill",
            "expected_reason": "untargeted_cid_nl_owner_mapped_backfill",
        }
        for sample_stem, value in sorted(accepted_sample_values.items())
    )
    return UntargetedCidNlGoldenPathProjection(
        method_subject=method_subject,
        discovery_projection_refs=discovery_projection_refs,
        spine=spine,
        backfill_facts=facts,
        backfill_decisions=decisions,
        backfill_universes=tuple(universes),
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
    projection: UntargetedCidNlGoldenPathProjection,
    *,
    production_acceptance_manifest_path: Path,
    manifest_root: Path,
) -> QuantMatrixVersionRows:
    """Revalidate the manifest file before entering the QuantMatrix owner."""

    if not isinstance(projection, UntargetedCidNlGoldenPathProjection):
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


def _load_terminal_acceptance_manifest(
    *,
    projection: UntargetedCidNlGoldenPathProjection,
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
                    f"checked manifest rewrites owner-prepared field {field_name!r}"
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


def _canonical_backfill_inputs(
    value: object,
    *,
    processing_owners: ProcessingOwnerReferences,
    method_subject: CidNlMethodSubject,
) -> tuple[UntargetedCidNlBackfillInput, ...]:
    if not isinstance(value, tuple) or any(
        not isinstance(item, UntargetedCidNlBackfillInput) for item in value
    ):
        raise UntargetedCidNlGoldenPathError(
            "backfill_inputs must be an immutable tuple"
        )
    owner_sample_ids = {
        sample.identity.sample_identity_id
        for sample in processing_owners.sample_resolutions
    }
    by_sample_id: dict[str, UntargetedCidNlBackfillInput] = {}
    for item in value:
        sample_id = item.sample.identity.sample_identity_id
        if sample_id not in owner_sample_ids:
            raise UntargetedCidNlGoldenPathError(
                "backfill input sample is outside the processing request"
            )
        if sample_id in by_sample_id:
            raise UntargetedCidNlGoldenPathError(
                "one sample may provide at most one backfill candidate universe"
            )
        if item.candidate_assessment.content.method_subject != method_subject:
            raise UntargetedCidNlGoldenPathError(
                "backfill assessment references a different method subject"
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
            "only candidate_mapped backfill may prepare a numeric Backfill value"
        )
    candidate = decision.matched_candidate
    if candidate is None:
        raise UntargetedCidNlGoldenPathError(
            "candidate_mapped backfill lost its exact physical observation"
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
            "backfill_assessment_artifact_id": ""
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


def _freeze_rows(
    rows: Sequence[Mapping[str, str]],
) -> tuple[Mapping[str, str], ...]:
    """Detach and deep-freeze string-only rows at the owner mint boundary."""

    return tuple(MappingProxyType(dict(row)) for row in rows)


def _validate_terminal_rows(
    projection: UntargetedCidNlGoldenPathProjection,
    rows: QuantMatrixVersionRows,
) -> None:
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
    "UntargetedCidNlGoldenPathProjection",
    "VALIDATION_STATUS",
    "build_untargeted_cid_nl_golden_path",
    "build_untargeted_cid_nl_quant_matrix_version",
]

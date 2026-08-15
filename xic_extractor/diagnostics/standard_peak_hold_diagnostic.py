"""No-write Standard Peak explanations over owner-validated Backfill HOLD cells."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import InitVar, dataclass, field
from typing import Literal, cast, final

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.matrix import AlignedCell, AlignmentMatrix
from xic_extractor.alignment.production_decisions import (
    ProductionCellDecision,
    ProductionDecisionSet,
    alignment_decision_config_projection,
    build_production_decisions,
)
from xic_extractor.alignment.promotion_policy import (
    BACKFILL_MS1_PATTERN_BLOCKED_REASON,
    BACKFILL_RT_EXPLANATION_BLOCKED_REASON,
    LOW_MS1_COVERAGE_BLOCKED_REASON,
    MISSING_BACKFILL_EVIDENCE_BLOCKED_REASON,
    NEIGHBOR_INTERFERENCE_BLOCKED_REASON,
)
from xic_extractor.processing_run_spec import ProcessingRunSpec

EvidenceState = Literal[
    "supports_explanation",
    "contradicts_explanation",
    "unavailable",
]
DiagnosticStatus = Literal["explained", "inconclusive"]

_EVIDENCE_STATES = frozenset(
    {"supports_explanation", "contradicts_explanation", "unavailable"}
)
_DIAGNOSTIC_ELIGIBLE_HOLD_REASONS = frozenset(
    {
        "rt_outside_preferred",
        BACKFILL_RT_EXPLANATION_BLOCKED_REASON,
        BACKFILL_MS1_PATTERN_BLOCKED_REASON,
        LOW_MS1_COVERAGE_BLOCKED_REASON,
        NEIGHBOR_INTERFERENCE_BLOCKED_REASON,
        MISSING_BACKFILL_EVIDENCE_BLOCKED_REASON,
    }
)
_SUBJECT_MINT_TOKEN = object()
_RESULT_MINT_TOKEN = object()
_EVIDENCE_FIELDS = (
    ("dda_missingness", "possible_dda_missingness"),
    ("multiple_peaks", "multiple_peaks"),
    ("wrong_peak", "wrong_peak"),
    ("mode_conflict", "mode_conflict"),
    ("identity_conflict", "identity_conflict"),
    ("shape_or_shift", "shape_or_shift_ambiguity"),
)


@final
@dataclass(frozen=True, slots=True)
class StandardPeakHoldDiagnosticSubject:
    """Diagnostic routing identity derived from a complete live Owner replay."""

    diagnostic_subject_id: str
    processing_run_spec_id: str
    processing_run_spec_hash: str
    profile_id: str
    alignment_decision_config_id: str
    feature_family_id: str
    sample_stem: str
    owner_production_status: Literal["review_rescue"]
    owner_hold_reason: str
    _mint_token: InitVar[object] = field(kw_only=True)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("StandardPeakHoldDiagnosticSubject is final")

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _SUBJECT_MINT_TOKEN:
            raise ValueError(
                "StandardPeakHoldDiagnosticSubject must come from live Owner replay"
            )
        if self.owner_production_status != "review_rescue":
            raise ValueError("diagnostic subject must be a terminal HOLD")
        if self.owner_hold_reason not in _DIAGNOSTIC_ELIGIBLE_HOLD_REASONS:
            raise ValueError("diagnostic subject is not diagnostic_eligible")


@final
@dataclass(frozen=True, slots=True)
class StandardPeakHoldDiagnosticPlan:
    processing_run_spec_id: str
    processing_run_spec_hash: str
    profile_id: str
    alignment_decision_config_id: str
    source_population_size: int
    terminal_hold_count: int
    subjects: tuple[StandardPeakHoldDiagnosticSubject, ...]

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("StandardPeakHoldDiagnosticPlan is final")


@final
@dataclass(frozen=True, slots=True)
class StandardPeakDiagnosticEvidence:
    """Expensive evidence attached to one owner-minted diagnostic subject."""

    subject: StandardPeakHoldDiagnosticSubject
    dda_missingness: EvidenceState = "unavailable"
    multiple_peaks: EvidenceState = "unavailable"
    wrong_peak: EvidenceState = "unavailable"
    mode_conflict: EvidenceState = "unavailable"
    identity_conflict: EvidenceState = "unavailable"
    shape_or_shift: EvidenceState = "unavailable"
    source_artifact_ids: tuple[str, ...] = ()

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("StandardPeakDiagnosticEvidence is final")

    def __post_init__(self) -> None:
        if not isinstance(self.subject, StandardPeakHoldDiagnosticSubject):
            raise TypeError("subject must be an owner-minted diagnostic subject")
        states = tuple(getattr(self, name) for name, _label in _EVIDENCE_FIELDS)
        if any(state not in _EVIDENCE_STATES for state in states):
            raise ValueError("unsupported Standard Peak diagnostic evidence state")
        if not isinstance(self.source_artifact_ids, tuple):
            raise TypeError("source_artifact_ids must be a tuple")
        if any(not _required_text(value) for value in self.source_artifact_ids):
            raise ValueError("source_artifact_ids must contain non-empty identities")
        if len(self.source_artifact_ids) != len(set(self.source_artifact_ids)):
            raise ValueError("duplicate source_artifact_ids are not allowed")
        if any(state != "unavailable" for state in states) and not (
            self.source_artifact_ids
        ):
            raise ValueError(
                "source_artifact_ids are required for available diagnostic evidence"
            )


@final
@dataclass(frozen=True, slots=True)
class StandardPeakDiagnosticResult:
    """Explanation-only result; deliberately contains no product decision or value."""

    diagnostic_subject_id: str
    status: DiagnosticStatus
    explanation_labels: tuple[str, ...]
    contradicted_labels: tuple[str, ...]
    unavailable_labels: tuple[str, ...]
    source_artifact_ids: tuple[str, ...]
    _mint_token: InitVar[object] = field(kw_only=True)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("StandardPeakDiagnosticResult is final")

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _RESULT_MINT_TOKEN:
            raise ValueError(
                "StandardPeakDiagnosticResult must come from the diagnostic classifier"
            )


def build_standard_peak_hold_diagnostic_plan(
    *,
    matrix: AlignmentMatrix,
    config: AlignmentConfig,
    production_decisions: ProductionDecisionSet,
    processing_run_spec: ProcessingRunSpec,
    requested_cell_keys: Sequence[tuple[str, str]],
) -> StandardPeakHoldDiagnosticPlan:
    """Replay the Owner and mint only requested diagnostic-eligible HOLD subjects."""

    if not isinstance(matrix, AlignmentMatrix):
        raise TypeError("matrix must be an AlignmentMatrix")
    if not isinstance(config, AlignmentConfig):
        raise TypeError("config must be an AlignmentConfig")
    if not isinstance(production_decisions, ProductionDecisionSet):
        raise TypeError("production_decisions must be a ProductionDecisionSet")
    if not isinstance(processing_run_spec, ProcessingRunSpec):
        raise TypeError("processing_run_spec must be a ProcessingRunSpec")
    _validate_complete_owner_population(matrix, processing_run_spec)
    replayed = build_production_decisions(matrix, config)
    if replayed != production_decisions:
        raise ValueError(
            "production_decisions differ from the complete live Owner Backfill replay"
        )

    requested = tuple(_cell_key(value) for value in requested_cell_keys)
    if len(requested) != len(set(requested)):
        raise ValueError("duplicate requested diagnostic cell key")
    cells_by_key = {(cell.cluster_id, cell.sample_stem): cell for cell in matrix.cells}
    config_projection = alignment_decision_config_projection(config)
    subjects: list[StandardPeakHoldDiagnosticSubject] = []
    for key in requested:
        cell = cells_by_key.get(key)
        decision = replayed.cells.get(key)
        if cell is None or decision is None:
            raise ValueError(f"unknown owner Backfill cell key: {key[0]}/{key[1]}")
        _validate_diagnostic_eligible_hold(decision)
        subjects.append(
            _mint_subject(
                cell=cell,
                decision=decision,
                processing_run_spec=processing_run_spec,
                alignment_decision_config_id=(
                    config_projection.alignment_decision_config_id
                ),
            )
        )

    return StandardPeakHoldDiagnosticPlan(
        processing_run_spec_id=processing_run_spec.run_spec_id,
        processing_run_spec_hash=processing_run_spec.run_spec_hash,
        profile_id=processing_run_spec.profile_id,
        alignment_decision_config_id=config_projection.alignment_decision_config_id,
        source_population_size=len(replayed.cells),
        terminal_hold_count=sum(
            decision.production_status == "review_rescue"
            for decision in replayed.cells.values()
        ),
        subjects=tuple(subjects),
    )


def explain_standard_peak_hold(
    evidence: StandardPeakDiagnosticEvidence,
) -> StandardPeakDiagnosticResult:
    """Classify expensive evidence without changing the Owner terminal decision."""

    if not isinstance(evidence, StandardPeakDiagnosticEvidence):
        raise TypeError("evidence must be StandardPeakDiagnosticEvidence")
    explanations: list[str] = []
    contradicted: list[str] = []
    unavailable: list[str] = []
    for field_name, label in _EVIDENCE_FIELDS:
        state = getattr(evidence, field_name)
        if state == "supports_explanation":
            explanations.append(label)
        elif state == "contradicts_explanation":
            contradicted.append(label)
        else:
            unavailable.append(label)
    return StandardPeakDiagnosticResult(
        diagnostic_subject_id=evidence.subject.diagnostic_subject_id,
        status="explained" if explanations else "inconclusive",
        explanation_labels=tuple(explanations),
        contradicted_labels=tuple(contradicted),
        unavailable_labels=tuple(unavailable),
        source_artifact_ids=evidence.source_artifact_ids,
        _mint_token=_RESULT_MINT_TOKEN,
    )


def _validate_complete_owner_population(
    matrix: AlignmentMatrix,
    processing_run_spec: ProcessingRunSpec,
) -> None:
    run_samples = tuple(
        item.sample_stem for item in processing_run_spec.acquisition_inputs
    )
    if matrix.sample_order != run_samples:
        raise ValueError(
            "ProcessingRunSpec must describe the complete owner population in order"
        )
    keys = tuple((cell.cluster_id, cell.sample_stem) for cell in matrix.cells)
    if len(keys) != len(set(keys)):
        raise ValueError("complete owner population contains duplicate cell keys")
    samples = set(matrix.sample_order)
    if any(cell.sample_stem not in samples for cell in matrix.cells):
        raise ValueError("owner cell falls outside the complete owner population")


def _validate_diagnostic_eligible_hold(decision: ProductionCellDecision) -> None:
    if (
        decision.production_status != "review_rescue"
        or decision.rescue_tier != "review_rescue"
        or decision.write_matrix_value
        or decision.matrix_value is not None
        or decision.blank_reason not in _DIAGNOSTIC_ELIGIBLE_HOLD_REASONS
    ):
        raise ValueError(
            "requested cell is not a diagnostic_eligible terminal HOLD"
        )


def _mint_subject(
    *,
    cell: AlignedCell,
    decision: ProductionCellDecision,
    processing_run_spec: ProcessingRunSpec,
    alignment_decision_config_id: str,
) -> StandardPeakHoldDiagnosticSubject:
    basis = {
        "schema_version": "standard_peak_hold_diagnostic_subject_v1",
        "processing_run_spec_id": processing_run_spec.run_spec_id,
        "processing_run_spec_hash": processing_run_spec.run_spec_hash,
        "profile_id": processing_run_spec.profile_id,
        "alignment_decision_config_id": alignment_decision_config_id,
        "feature_family_id": decision.feature_family_id,
        "sample_stem": decision.sample_stem,
        "owner_production_status": decision.production_status,
        "owner_hold_reason": decision.blank_reason,
        "raw_status": decision.raw_status,
        "cell_status": cell.status,
        "cell_reason": cell.reason,
    }
    digest = hashlib.sha256(
        json.dumps(
            basis,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return StandardPeakHoldDiagnosticSubject(
        diagnostic_subject_id=f"standard-peak-hold-diagnostic.v1:{digest}",
        processing_run_spec_id=processing_run_spec.run_spec_id,
        processing_run_spec_hash=processing_run_spec.run_spec_hash,
        profile_id=processing_run_spec.profile_id,
        alignment_decision_config_id=alignment_decision_config_id,
        feature_family_id=decision.feature_family_id,
        sample_stem=decision.sample_stem,
        owner_production_status=cast(
            Literal["review_rescue"], decision.production_status
        ),
        owner_hold_reason=decision.blank_reason,
        _mint_token=_SUBJECT_MINT_TOKEN,
    )


def _cell_key(value: object) -> tuple[str, str]:
    if not isinstance(value, tuple) or len(value) != 2:
        raise TypeError(
            "requested diagnostic cell keys must be (family, sample) tuples"
        )
    family, sample = value
    return _required_text(family), _required_text(sample)


def _required_text(value: object) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError("diagnostic identities must be non-empty strings")
    return value.strip()


__all__ = [
    "StandardPeakDiagnosticEvidence",
    "StandardPeakDiagnosticResult",
    "StandardPeakHoldDiagnosticPlan",
    "StandardPeakHoldDiagnosticSubject",
    "build_standard_peak_hold_diagnostic_plan",
    "explain_standard_peak_hold",
]

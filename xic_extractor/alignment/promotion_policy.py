from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from xic_extractor.alignment.identity_gates import (
    EXTREME_BACKFILL_REASON,
    WEAK_SEED_BACKFILL_REASON,
    WEAK_SEED_TOLERATED_REASON,
    SeedQualitySummary,
    classify_single_dr_backfill_dependency,
    is_dr_neutral_loss_tag,
)
from xic_extractor.alignment.matrix import AlignedCell

if TYPE_CHECKING:
    from xic_extractor.alignment.cell_quality import CellQualityDecision

CELL_EVIDENCE_SUPPORTED_REASON = "cell_evidence_supported_backfill"
DDA_LIMITED_MS2_SHAPE_REASON = "dda_limited_ms2_but_ms1_shape_supported"
NEIGHBOR_INTERFERENCE_BLOCKED_REASON = "neighboring_ms1_interference_blocked"
LOW_MS1_COVERAGE_BLOCKED_REASON = "low_ms1_assessable_coverage_blocked"
MISSING_BACKFILL_EVIDENCE_BLOCKED_REASON = (
    "missing_independent_backfill_identity_evidence"
)
BACKFILL_OWNER_REHYDRATION_UNAVAILABLE_REASON = (
    "backfill_owner_rehydration_unavailable"
)
BACKFILL_RT_EXPLANATION_BLOCKED_REASON = "backfill_rt_not_explained"
BACKFILL_MS1_PATTERN_BLOCKED_REASON = "backfill_ms1_pattern_not_supportive"
BACKFILL_MS2_CONTEXT_BLOCKED_REASON = "backfill_ms2_context_not_supportive"
BACKFILL_MS2_CONFLICT_REASON = "backfill_ms2_pattern_conflict"
BACKFILL_HYPOTHESIS_BLOCKED_REASON = "backfill_wrong_peak_or_hypothesis_blocked"
BACKFILL_ONLY_BLOCKED_REASON = "rescue_only_blocked"
HIGH_BACKFILL_CAPPED_FLAG = "high_backfill_dependency_capped"
BACKFILL_CELL_EVIDENCE_REQUIRED_FLAG = "backfill_cell_evidence_required"
BACKFILL_REVIEW_ONLY_FLAG = "backfill_rescue_review_only"
PRIMARY_IDENTITY_RETAINED_BACKFILL_REVIEW_REASON = (
    "primary_identity_retained_backfill_review_only"
)

PromotionState = Literal["not_applicable", "supported", "blocked", "unavailable"]

_POLICY_BACKFILL_REASONS = {
    EXTREME_BACKFILL_REASON,
    WEAK_SEED_BACKFILL_REASON,
    WEAK_SEED_TOLERATED_REASON,
}
_SUPPORTED_PRIMARY_EVIDENCE = {
    "owner_complete_link",
    "owner_identity",
    "cid_nl_only",
    "multi_sample_detected",
}
_TRACE_CONTINUITY_LABELS = {
    "clean",
    "good",
    "family_centered",
}
_CHEMICAL_MARKERS = (
    "neutral_loss",
    "nl_match",
    "product",
    "fragment",
    "chemical",
    "ms2",
)
_INTERFERENCE_MARKERS = ("neighbor", "interference")
_INTERFERENCE_NOT_ASSESSED_MARKERS = (
    "neighbor_interference_not_assessed",
    "neighboring_interference_not_assessed",
)
_INTERFERING_LOCAL_MIXTURE_DIAGNOSTICS = frozenset(
    {
        "ambiguous_adjacent_split",
        "two_peak_supported",
        "competing_boundary_model",
    },
)
_LOW_COVERAGE_LOCAL_MIXTURE_DIAGNOSTICS = frozenset({"insufficient_evidence"})
_LOW_COVERAGE_MARKERS = (
    "low_scan_support",
    "skipped_low_scan_support",
    "coverage",
    "unassessable",
)
_MIN_SCAN_SUPPORT = 0.5
_LOW_SCAN_SUPPORT_MAX = 0.2
_PREFERRED_RT_DELTA_SEC = 60.0
_MAX_RT_DELTA_SEC = 180.0
_HYPOTHESIS_BLOCKING_CLAIM_STATES = {
    "duplicate_loser",
    "review_only_duplicate_loser",
}
_HYPOTHESIS_BLOCKING_CONSOLIDATION_STATES = {
    "primary_loser",
    "consolidation_no_go",
}
_HYPOTHESIS_BLOCKING_MARKERS = (
    "wrong_peak_conflict",
    "wrong_peak",
    "duplicate_loser",
    "cross_mode_rescue_blocked",
    "mode_split_required",
    "consolidation_no_go",
)
_BACKFILL_CELL_ONLY_BLOCK_REASONS = frozenset(
    {
        BACKFILL_MS1_PATTERN_BLOCKED_REASON,
        BACKFILL_HYPOTHESIS_BLOCKED_REASON,
        BACKFILL_MS2_CONTEXT_BLOCKED_REASON,
        BACKFILL_MS2_CONFLICT_REASON,
        BACKFILL_RT_EXPLANATION_BLOCKED_REASON,
        LOW_MS1_COVERAGE_BLOCKED_REASON,
        MISSING_BACKFILL_EVIDENCE_BLOCKED_REASON,
        NEIGHBOR_INTERFERENCE_BLOCKED_REASON,
    },
)


@dataclass(frozen=True)
class BackfillCellEvidence:
    sample_stem: str
    status: str
    quality_status: str = ""
    quality_reason: str = ""
    area: float | None = None
    apex_rt: float | None = None
    height: float | None = None
    peak_start_rt: float | None = None
    peak_end_rt: float | None = None
    rt_delta_sec: float | None = None
    trace_quality: str = ""
    scan_support_score: float | None = None
    source_candidate_id: str = ""
    reason: str = ""
    region_local_mixture_diagnostic: str = ""
    region_local_mixture_reason: str = ""
    region_review_reason: str = ""
    region_shadow_status: str = ""
    region_shadow_verdict: str = ""
    group_hypothesis_id: str = ""
    group_claim_state: str = ""
    claim_winner_group_hypothesis_id: str = ""
    claim_source_group_hypothesis_id: str = ""
    consolidation_state: str = ""
    consolidation_winner_group_hypothesis_id: str = ""
    consolidation_source_group_hypothesis_id: str = ""
    peak_hypothesis_status: str = ""
    product_selection_blocker: str = ""
    rt_mode_status: str = ""
    backfill_ms2_trigger_scan_count: int | None = None
    backfill_strict_nl_scan_count: int | None = None
    backfill_ms2_trace_strength: str = ""
    backfill_dda_missing_nl_policy_status: str = ""
    backfill_family_ms2_required_tag_status: str = ""
    backfill_evidence_reason: str = ""

    @property
    def is_backfilled_quantifiable(self) -> bool:
        if self.quality_status:
            return self.quality_status == "rescue_quantifiable"
        return (
            self.status == "backfilled"
            and _positive(self.area)
            and (
                self.local_apex_supported
            )
        )

    @property
    def has_complete_peak(self) -> bool:
        return all(
            _finite(value)
            for value in (
                self.area,
                self.apex_rt,
                self.height,
                self.peak_start_rt,
                self.peak_end_rt,
            )
        )

    @property
    def selected_peak_geometry_supported(self) -> bool:
        if not self.has_complete_peak:
            return False
        assert self.apex_rt is not None
        assert self.peak_start_rt is not None
        assert self.peak_end_rt is not None
        return self.peak_start_rt <= self.apex_rt <= self.peak_end_rt

    @property
    def local_apex_supported(self) -> bool:
        if not self.selected_peak_geometry_supported:
            return False
        return (
            self.rt_delta_sec is not None
            and abs(self.rt_delta_sec) <= _MAX_RT_DELTA_SEC
        )

    @property
    def scan_support(self) -> bool:
        return (
            self.scan_support_score is not None
            and self.scan_support_score >= _MIN_SCAN_SUPPORT
        )

    @property
    def low_scan_support(self) -> bool:
        return (
            self.scan_support_score is not None
            and self.scan_support_score <= _LOW_SCAN_SUPPORT_MAX
        )

    @property
    def trace_continuity(self) -> bool:
        return _normalize(self.trace_quality) in _TRACE_CONTINUITY_LABELS

    @property
    def chemical_support(self) -> bool:
        return _contains_marker(self.reason, _CHEMICAL_MARKERS)

    @property
    def selected_peak_dominance(self) -> bool:
        text = " ".join(
            (
                self.region_local_mixture_diagnostic,
                self.region_local_mixture_reason,
                self.region_review_reason,
                self.region_shadow_status,
                self.region_shadow_verdict,
            ),
        ).lower()
        return (
            "one_envelope_supported" in text
            or "selected_apex" in text
            or "selected_region" in text
            or "dominant" in text
        )

    @property
    def high_neighbor_interference(self) -> bool:
        if (
            _normalize(self.region_local_mixture_diagnostic)
            in _INTERFERING_LOCAL_MIXTURE_DIAGNOSTICS
        ):
            return True
        text = _normalize(
            " ".join(
                (
                    self.reason,
                    self.region_local_mixture_diagnostic,
                    self.region_local_mixture_reason,
                    self.region_review_reason,
                    self.region_shadow_status,
                    self.region_shadow_verdict,
                ),
            ),
        )
        for marker in _INTERFERENCE_NOT_ASSESSED_MARKERS:
            text = text.replace(marker, "")
        return any(marker in text for marker in _INTERFERENCE_MARKERS)

    @property
    def low_assessable_coverage(self) -> bool:
        if (
            _normalize(self.region_local_mixture_diagnostic)
            in _LOW_COVERAGE_LOCAL_MIXTURE_DIAGNOSTICS
        ):
            return True
        text = " ".join(
            (
                self.reason,
                self.region_local_mixture_diagnostic,
                self.region_local_mixture_reason,
                self.region_review_reason,
                self.region_shadow_status,
                self.region_shadow_verdict,
            ),
        ).lower()
        return self.low_scan_support or any(
            marker in text for marker in _LOW_COVERAGE_MARKERS
        )

    @property
    def hypothesis_or_claim_blocked(self) -> bool:
        if (
            _normalize(self.group_claim_state) in _HYPOTHESIS_BLOCKING_CLAIM_STATES
            and not self.claim_resolved_by_current_winner
        ):
            return True
        if (
            _normalize(self.consolidation_state)
            in _HYPOTHESIS_BLOCKING_CONSOLIDATION_STATES
        ):
            return True
        text = " ".join(
            (
                self.reason,
                self.region_review_reason,
                self.region_shadow_status,
                self.region_shadow_verdict,
                self.backfill_evidence_reason,
                self.peak_hypothesis_status,
                self.product_selection_blocker,
                self.rt_mode_status,
            ),
        ).lower()
        return any(marker in text for marker in _HYPOTHESIS_BLOCKING_MARKERS)

    @property
    def claim_resolved_by_current_winner(self) -> bool:
        current = _normalize(self.group_hypothesis_id)
        winner = _normalize(self.claim_winner_group_hypothesis_id)
        if not current or not winner or current != winner:
            return False
        return _normalize(self.consolidation_state) == "moved_to_primary_winner"

    @property
    def additional_ms1_support(self) -> bool:
        return (
            self.scan_support
            or self.trace_continuity
            or self.selected_peak_dominance
        )

    @property
    def rt_evidence_supported(self) -> bool:
        if self.rt_delta_sec is None:
            return False
        return abs(self.rt_delta_sec) <= _PREFERRED_RT_DELTA_SEC

    @property
    def backfill_identity_block_reason(self) -> str:
        if not self.selected_peak_geometry_supported:
            return LOW_MS1_COVERAGE_BLOCKED_REASON
        if self.hypothesis_or_claim_blocked:
            return BACKFILL_HYPOTHESIS_BLOCKED_REASON
        if not self.local_apex_supported:
            return BACKFILL_RT_EXPLANATION_BLOCKED_REASON
        if not self.rt_evidence_supported:
            return BACKFILL_RT_EXPLANATION_BLOCKED_REASON
        return BACKFILL_MS1_PATTERN_BLOCKED_REASON

    @property
    def supported_for_backfill(self) -> bool:
        return False


@dataclass(frozen=True)
class BackfillPromotionEvidence:
    neutral_loss_tag: str
    primary_evidence: str
    q_detected: int
    q_backfill: int
    cell_count: int
    duplicate_count: int
    ambiguous_count: int
    backfill_dependency: str | None
    seed_quality: SeedQualitySummary | None
    held_backfill_count: int = 0
    unrehydrated_review_backfill_count: int | None = 0
    cells: tuple[BackfillCellEvidence, ...] = ()


@dataclass(frozen=True)
class BackfillPromotionDecision:
    state: PromotionState
    reason: str = ""
    confidence: str = ""
    flags: tuple[str, ...] = ()
    supported_backfill_count: int = 0
    assessed_backfill_count: int = 0

    @property
    def supported(self) -> bool:
        return self.state == "supported"

    @property
    def blocked(self) -> bool:
        return self.state == "blocked"

    @property
    def unavailable(self) -> bool:
        return self.state == "unavailable"


def evidence_from_alignment(
    *,
    neutral_loss_tag: str,
    primary_evidence: str,
    q_detected: int,
    q_backfill: int,
    duplicate_count: int,
    ambiguous_count: int,
    held_backfill_count: int,
    backfill_dependency: str | None,
    seed_quality: SeedQualitySummary | None,
    cells: Sequence[AlignedCell],
    cell_quality: Sequence[CellQualityDecision],
) -> BackfillPromotionEvidence:
    quality_by_sample = {decision.sample_stem: decision for decision in cell_quality}
    return BackfillPromotionEvidence(
        neutral_loss_tag=neutral_loss_tag,
        primary_evidence=primary_evidence,
        q_detected=q_detected,
        q_backfill=q_backfill,
        cell_count=len(cell_quality),
        duplicate_count=duplicate_count,
        ambiguous_count=ambiguous_count,
        backfill_dependency=backfill_dependency,
        seed_quality=seed_quality,
        held_backfill_count=held_backfill_count,
        cells=tuple(
            _cell_from_aligned(cell, quality_by_sample.get(cell.sample_stem))
            for cell in cells
        ),
    )


def cell_evidence_from_aligned(
    cell: AlignedCell,
    quality: CellQualityDecision | None,
) -> BackfillCellEvidence:
    return _cell_from_aligned(cell, quality)


def cell_backfill_write_block_reason(cell: AlignedCell) -> str:
    evidence = cell_evidence_from_aligned(cell, None)
    if evidence.hypothesis_or_claim_blocked:
        return BACKFILL_HYPOTHESIS_BLOCKED_REASON
    if evidence.high_neighbor_interference:
        return NEIGHBOR_INTERFERENCE_BLOCKED_REASON
    if evidence.low_assessable_coverage:
        return LOW_MS1_COVERAGE_BLOCKED_REASON
    return ""


def evidence_from_tsv_rows(
    review_row: Mapping[str, str],
    cell_rows: Sequence[Mapping[str, str]],
    *,
    seed_quality: SeedQualitySummary | None,
    sample_count: int,
) -> BackfillPromotionEvidence:
    q_detected = _int_value(
        review_row.get("quantifiable_detected_count", "")
        or review_row.get("detected_count", ""),
    )
    q_backfill = _int_value(
        review_row.get("quantifiable_backfill_count", "")
        or review_row.get("accepted_backfill_count", ""),
    )
    duplicate_count = _int_value(review_row.get("duplicate_assigned_count", ""))
    ambiguous_count = _int_value(review_row.get("ambiguous_ms1_owner_count", ""))
    cell_count = sample_count or len(cell_rows)
    backfill_dependency = classify_single_dr_backfill_dependency(
        neutral_loss_tag=review_row.get("neutral_loss_tag", ""),
        q_detected=q_detected,
        q_backfill=q_backfill,
        cell_count=cell_count,
        seed_quality=seed_quality,
    )
    return BackfillPromotionEvidence(
        neutral_loss_tag=review_row.get("neutral_loss_tag", ""),
        primary_evidence=review_row.get("primary_evidence", ""),
        q_detected=q_detected,
        q_backfill=q_backfill,
        cell_count=cell_count,
        duplicate_count=duplicate_count,
        ambiguous_count=ambiguous_count,
        backfill_dependency=backfill_dependency,
        seed_quality=seed_quality,
        unrehydrated_review_backfill_count=_nonnegative_int_value(
            review_row.get("review_backfill_count", ""),
        ),
        cells=tuple(_cell_from_tsv(row) for row in cell_rows),
    )


def classify_backfill_promotion(
    evidence: BackfillPromotionEvidence,
) -> BackfillPromotionDecision:
    backfilled = tuple(
        cell for cell in evidence.cells if cell.is_backfilled_quantifiable
    )
    owner_blocked = tuple(
        cell
        for cell in evidence.cells
        if cell.quality_status == "review_backfill"
        and cell.quality_reason
        in {
            BACKFILL_HYPOTHESIS_BLOCKED_REASON,
            LOW_MS1_COVERAGE_BLOCKED_REASON,
            NEIGHBOR_INTERFERENCE_BLOCKED_REASON,
        }
    )
    assessed_backfills = backfilled + owner_blocked
    candidate_backfill_count = evidence.q_backfill + evidence.held_backfill_count
    review_backfill_count = evidence.unrehydrated_review_backfill_count
    if (
        (review_backfill_count is None or review_backfill_count > 0)
        and is_dr_neutral_loss_tag(evidence.neutral_loss_tag)
    ):
        return BackfillPromotionDecision(
            state="unavailable",
            reason=BACKFILL_OWNER_REHYDRATION_UNAVAILABLE_REASON,
            confidence="review",
            flags=("owner_rehydration_required",),
            assessed_backfill_count=len(backfilled),
        )
    if candidate_backfill_count > 0 and any(
        cell.hypothesis_or_claim_blocked for cell in assessed_backfills
    ):
        return BackfillPromotionDecision(
            state="blocked",
            reason=BACKFILL_HYPOTHESIS_BLOCKED_REASON,
            confidence="review",
            flags=("missing_independent_backfill_identity_evidence",),
            assessed_backfill_count=len(assessed_backfills),
        )
    same_peak_override = _same_peak_policy_override(evidence)
    if not _in_policy_scope(evidence) and not same_peak_override:
        return BackfillPromotionDecision(state="not_applicable")
    if evidence.q_detected <= 0 and candidate_backfill_count > 0:
        return BackfillPromotionDecision(
            state="blocked",
            reason=BACKFILL_ONLY_BLOCKED_REASON,
            confidence="review",
            flags=("rescue_only",),
            assessed_backfill_count=candidate_backfill_count,
        )
    if evidence.q_detected < 2 and not same_peak_override:
        return BackfillPromotionDecision(state="not_applicable")
    if evidence.duplicate_count > evidence.q_detected and not same_peak_override:
        return BackfillPromotionDecision(
            state="blocked",
            reason="duplicate_claim_pressure",
            confidence="review",
            flags=("duplicate_claim_pressure",),
        )

    if any(cell.high_neighbor_interference for cell in assessed_backfills):
        return BackfillPromotionDecision(
            state="blocked",
            reason=NEIGHBOR_INTERFERENCE_BLOCKED_REASON,
            confidence="review",
            flags=("neighboring_ms1_interference",),
            assessed_backfill_count=len(assessed_backfills),
        )

    if any(cell.low_assessable_coverage for cell in assessed_backfills):
        return BackfillPromotionDecision(
            state="blocked",
            reason=LOW_MS1_COVERAGE_BLOCKED_REASON,
            confidence="review",
            flags=("low_ms1_assessable_coverage",),
            assessed_backfill_count=len(assessed_backfills),
        )

    if evidence.held_backfill_count > 0:
        supported = tuple(
            cell for cell in assessed_backfills if cell.supported_for_backfill
        )
        return BackfillPromotionDecision(
            state="blocked",
            reason=BACKFILL_RT_EXPLANATION_BLOCKED_REASON,
            confidence="review",
            flags=("missing_independent_backfill_identity_evidence",),
            supported_backfill_count=len(supported),
            assessed_backfill_count=candidate_backfill_count,
        )

    if evidence.q_backfill > 0 and len(backfilled) < evidence.q_backfill:
        return BackfillPromotionDecision(
            state="blocked",
            reason=LOW_MS1_COVERAGE_BLOCKED_REASON,
            confidence="review",
            flags=("low_ms1_assessable_coverage",),
            assessed_backfill_count=len(backfilled),
        )

    if evidence.q_backfill > 0 and not backfilled:
        return BackfillPromotionDecision(
            state="blocked",
            reason=LOW_MS1_COVERAGE_BLOCKED_REASON,
            confidence="review",
            flags=("low_ms1_assessable_coverage",),
        )

    supported = tuple(cell for cell in backfilled if cell.supported_for_backfill)
    if len(supported) < evidence.q_backfill:
        unsupported = tuple(
            cell for cell in backfilled if not cell.supported_for_backfill
        )
        return BackfillPromotionDecision(
            state="blocked",
            reason=_backfill_identity_block_reason(unsupported),
            confidence="review",
            flags=("missing_independent_backfill_identity_evidence",),
            supported_backfill_count=len(supported),
            assessed_backfill_count=len(backfilled),
        )

    flags = (HIGH_BACKFILL_CAPPED_FLAG,)
    reason = (
        DDA_LIMITED_MS2_SHAPE_REASON
        if _dda_limited_support(evidence)
        else CELL_EVIDENCE_SUPPORTED_REASON
    )
    return BackfillPromotionDecision(
        state="supported",
        reason=reason,
        confidence="medium",
        flags=flags,
        supported_backfill_count=len(supported),
        assessed_backfill_count=len(backfilled),
    )


def is_backfill_cell_only_block_reason(reason: str) -> bool:
    return reason in _BACKFILL_CELL_ONLY_BLOCK_REASONS


def _in_policy_scope(evidence: BackfillPromotionEvidence) -> bool:
    if not is_dr_neutral_loss_tag(evidence.neutral_loss_tag):
        return False
    if evidence.primary_evidence not in _SUPPORTED_PRIMARY_EVIDENCE:
        return False
    if evidence.q_backfill + evidence.held_backfill_count <= 0:
        return False
    return evidence.backfill_dependency in _POLICY_BACKFILL_REASONS


def _same_peak_policy_override(evidence: BackfillPromotionEvidence) -> bool:
    if not (
        evidence.q_detected == 1
        or evidence.duplicate_count > evidence.q_detected
    ):
        return False
    return _same_peak_backfill_support(evidence)


def _same_peak_backfill_support(evidence: BackfillPromotionEvidence) -> bool:
    if not is_dr_neutral_loss_tag(evidence.neutral_loss_tag):
        return False
    if evidence.primary_evidence not in _SUPPORTED_PRIMARY_EVIDENCE:
        return False
    if evidence.q_detected <= 0 or evidence.q_backfill <= 0:
        return False
    if evidence.ambiguous_count > 0:
        return False
    backfilled = tuple(
        cell for cell in evidence.cells if cell.is_backfilled_quantifiable
    )
    if len(backfilled) != evidence.q_backfill:
        return False
    return all(cell.supported_for_backfill for cell in backfilled)


def _dda_limited_support(evidence: BackfillPromotionEvidence) -> bool:
    if evidence.backfill_dependency == WEAK_SEED_BACKFILL_REASON:
        return True
    seed_quality = evidence.seed_quality
    return bool(seed_quality is not None and seed_quality.weak_seed_signal)


def _backfill_identity_block_reason(cells: Sequence[BackfillCellEvidence]) -> str:
    for reason in (
        BACKFILL_HYPOTHESIS_BLOCKED_REASON,
        BACKFILL_RT_EXPLANATION_BLOCKED_REASON,
        BACKFILL_MS1_PATTERN_BLOCKED_REASON,
        LOW_MS1_COVERAGE_BLOCKED_REASON,
    ):
        if any(cell.backfill_identity_block_reason == reason for cell in cells):
            return reason
    return MISSING_BACKFILL_EVIDENCE_BLOCKED_REASON


def _cell_from_aligned(
    cell: AlignedCell,
    quality: CellQualityDecision | None,
) -> BackfillCellEvidence:
    return BackfillCellEvidence(
        sample_stem=cell.sample_stem,
        status=cell.status,
        quality_status="" if quality is None else quality.quality_status,
        quality_reason="" if quality is None else quality.quality_reason,
        area=cell.matrix_area,
        apex_rt=cell.apex_rt,
        height=cell.height,
        peak_start_rt=cell.peak_start_rt,
        peak_end_rt=cell.peak_end_rt,
        rt_delta_sec=cell.rt_delta_sec,
        trace_quality=cell.trace_quality,
        scan_support_score=cell.scan_support_score,
        source_candidate_id=cell.source_candidate_id or "",
        reason=cell.reason,
        region_local_mixture_diagnostic=cell.region_local_mixture_diagnostic,
        region_local_mixture_reason=cell.region_local_mixture_reason,
        region_review_reason=cell.region_review_reason,
        region_shadow_status=cell.region_shadow_status,
        region_shadow_verdict=cell.region_shadow_verdict,
        group_hypothesis_id=cell.group_hypothesis_id,
        group_claim_state=cell.group_claim_state,
        claim_winner_group_hypothesis_id=cell.claim_winner_group_hypothesis_id,
        claim_source_group_hypothesis_id=cell.claim_source_group_hypothesis_id,
        consolidation_state=cell.consolidation_state,
        consolidation_winner_group_hypothesis_id=(
            cell.consolidation_winner_group_hypothesis_id
        ),
        consolidation_source_group_hypothesis_id=(
            cell.consolidation_source_group_hypothesis_id
        ),
        peak_hypothesis_status=cell.peak_hypothesis_status,
        product_selection_blocker=cell.product_selection_blocker,
        rt_mode_status=cell.rt_mode_status,
        backfill_ms2_trigger_scan_count=cell.backfill_ms2_trigger_scan_count,
        backfill_strict_nl_scan_count=cell.backfill_strict_nl_scan_count,
        backfill_ms2_trace_strength=cell.backfill_ms2_trace_strength,
        backfill_dda_missing_nl_policy_status=(
            cell.backfill_dda_missing_nl_policy_status
        ),
        backfill_family_ms2_required_tag_status=(
            cell.backfill_family_ms2_required_tag_status
        ),
        backfill_evidence_reason=cell.backfill_evidence_reason,
    )


def _cell_from_tsv(row: Mapping[str, str]) -> BackfillCellEvidence:
    primary_area = row.get("primary_matrix_area", "")
    return BackfillCellEvidence(
        sample_stem=row.get("sample_stem", ""),
        status=row.get("status", ""),
        area=_float_value(primary_area or row.get("area", "")),
        apex_rt=_float_value(row.get("apex_rt", "")),
        height=_float_value(row.get("height", "")),
        peak_start_rt=_float_value(row.get("peak_start_rt", "")),
        peak_end_rt=_float_value(row.get("peak_end_rt", "")),
        rt_delta_sec=_float_value(row.get("rt_delta_sec", "")),
        trace_quality=row.get("trace_quality", ""),
        scan_support_score=_float_value(row.get("scan_support_score", "")),
        source_candidate_id=row.get("source_candidate_id", ""),
        reason=row.get("reason", ""),
        region_local_mixture_diagnostic=row.get("region_local_mixture_diagnostic", ""),
        region_local_mixture_reason=row.get("region_local_mixture_reason", ""),
        region_review_reason=row.get("region_review_reason", ""),
        region_shadow_status=row.get("region_shadow_status", ""),
        region_shadow_verdict=row.get("region_shadow_verdict", ""),
        group_hypothesis_id=row.get("group_hypothesis_id", ""),
        group_claim_state=row.get("group_claim_state", ""),
        claim_winner_group_hypothesis_id=row.get(
            "claim_winner_group_hypothesis_id",
            "",
        ),
        claim_source_group_hypothesis_id=row.get(
            "claim_source_group_hypothesis_id",
            "",
        ),
        consolidation_state=row.get("consolidation_state", ""),
        consolidation_winner_group_hypothesis_id=row.get(
            "consolidation_winner_group_hypothesis_id",
            "",
        ),
        consolidation_source_group_hypothesis_id=row.get(
            "consolidation_source_group_hypothesis_id",
            "",
        ),
        peak_hypothesis_status=row.get("peak_hypothesis_status", ""),
        product_selection_blocker=row.get("product_selection_blocker", ""),
        rt_mode_status=row.get("rt_mode_status", ""),
        backfill_ms2_trigger_scan_count=_int_optional(
            row.get("backfill_ms2_trigger_scan_count", ""),
        ),
        backfill_strict_nl_scan_count=_int_optional(
            row.get("backfill_strict_nl_scan_count", ""),
        ),
        backfill_ms2_trace_strength=row.get("backfill_ms2_trace_strength", ""),
        backfill_dda_missing_nl_policy_status=row.get(
            "backfill_dda_missing_nl_policy_status",
            "",
        ),
        backfill_family_ms2_required_tag_status=row.get(
            "backfill_family_ms2_required_tag_status",
            "",
        ),
        backfill_evidence_reason=row.get("backfill_evidence_reason", ""),
    )


def _contains_marker(value: str, markers: Sequence[str]) -> bool:
    text = value.lower()
    return any(marker in text for marker in markers)


def _normalize(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


def _positive(value: float | None) -> bool:
    return value is not None and value > 0.0


def _finite(value: float | None) -> bool:
    return (
        value is not None
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _float_value(value: object) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip()
        if value.startswith("'"):
            value = value[1:]
    if not isinstance(value, (str, int, float)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _int_value(value: object) -> int:
    number = _float_value(value)
    return 0 if number is None else int(number)


def _nonnegative_int_value(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if text.startswith("'"):
        text = text[1:]
    if not text or any(character not in "0123456789" for character in text):
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _int_optional(value: object) -> int | None:
    number = _float_value(value)
    return None if number is None else int(number)

"""Immutable application-result closure over completed workflow projections."""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from typing import TYPE_CHECKING, Literal, TypeAlias

from xic_extractor.alignment.cross_sample_reconciliation import (
    CrossSampleFinalReceipt,
)
from xic_extractor.alignment.result.scientific_alignment import (
    FinalizedScientificAlignment,
    FinalizedScientificAlignmentContractError,
    validate_finalized_scientific_alignment,
)
from xic_extractor.peak_detection.search_scope import (
    TargetSearchScope,
    UntargetedSearchScope,
)
from xic_extractor.processing_identity import ProcessingRequestIdentity
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

if TYPE_CHECKING:
    from xic_extractor.alignment.projection.target.post_cell import (
        TargetPostCellProjection,
    )
    from xic_extractor.alignment.projection.untarget.post_cell import (
        UntargetPostCellProjection,
    )


class ApplicationResultContractError(ValueError):
    """Raised when application-result owners cannot close exactly."""


_APPLICATION_RESULT_MINT_TOKEN = object()


@dataclass(frozen=True, slots=True)
class TargetApplicationResultRequest:
    scientific_core: FinalizedScientificAlignment
    completed_projection: TargetPostCellProjection

    def __post_init__(self) -> None:
        _validate_target_request(self)

    @property
    def variant(self) -> Literal["targeted"]:
        return "targeted"


@dataclass(frozen=True, slots=True)
class UntargetApplicationResultRequest:
    scientific_core: FinalizedScientificAlignment
    completed_projection: UntargetPostCellProjection

    def __post_init__(self) -> None:
        _validate_untarget_request(self)

    @property
    def variant(self) -> Literal["untargeted"]:
        return "untargeted"


ApplicationResultRequest: TypeAlias = (
    TargetApplicationResultRequest | UntargetApplicationResultRequest
)


@dataclass(frozen=True, slots=True)
class FinalizedTargetApplicationResult:
    scientific_core: FinalizedScientificAlignment
    completed_projection: TargetPostCellProjection
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        _require_result_mint(_mint_token)
        _validate_target_result(self)

    @property
    def variant(self) -> Literal["targeted"]:
        return "targeted"

    @property
    def processing_request(self) -> ProcessingRequestIdentity:
        return self.scientific_core.processing_request

    @property
    def canonical_samples(
        self,
    ) -> tuple[ResolvedCanonicalSampleIdentity, ...]:
        return self.scientific_core.canonical_samples

    @property
    def completion_receipts(
        self,
    ) -> tuple[CrossSampleFinalReceipt, TargetPostCellProjection]:
        return (
            self.scientific_core.final_cross_sample_receipt,
            self.completed_projection,
        )


@dataclass(frozen=True, slots=True)
class FinalizedUntargetApplicationResult:
    scientific_core: FinalizedScientificAlignment
    completed_projection: UntargetPostCellProjection
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        _require_result_mint(_mint_token)
        _validate_untarget_result(self)

    @property
    def variant(self) -> Literal["untargeted"]:
        return "untargeted"

    @property
    def processing_request(self) -> ProcessingRequestIdentity:
        return self.scientific_core.processing_request

    @property
    def canonical_samples(
        self,
    ) -> tuple[ResolvedCanonicalSampleIdentity, ...]:
        return self.scientific_core.canonical_samples

    @property
    def completion_receipts(
        self,
    ) -> tuple[CrossSampleFinalReceipt, UntargetPostCellProjection]:
        return (
            self.scientific_core.final_cross_sample_receipt,
            self.completed_projection,
        )


FinalizedApplicationResult: TypeAlias = (
    FinalizedTargetApplicationResult | FinalizedUntargetApplicationResult
)


def finalize_alignment_result(
    request: ApplicationResultRequest,
) -> FinalizedApplicationResult:
    """Seal one scientific core and one matching completed projection."""

    if type(request) is TargetApplicationResultRequest:
        _validate_target_request(request)
        core = request.scientific_core
        return FinalizedTargetApplicationResult(
            scientific_core=core,
            completed_projection=request.completed_projection,
            _mint_token=_APPLICATION_RESULT_MINT_TOKEN,
        )
    if type(request) is UntargetApplicationResultRequest:
        _validate_untarget_request(request)
        core = request.scientific_core
        return FinalizedUntargetApplicationResult(
            scientific_core=core,
            completed_projection=request.completed_projection,
            _mint_token=_APPLICATION_RESULT_MINT_TOKEN,
        )
    raise ApplicationResultContractError(
        "application result requires an exact discriminated request variant"
    )


def _validate_target_request(request: TargetApplicationResultRequest) -> None:
    from xic_extractor.alignment.projection.target.post_cell import (
        TargetPostCellProjection,
    )

    if type(request.scientific_core) is not FinalizedScientificAlignment:
        raise ApplicationResultContractError(
            "Target application-result request requires an exact scientific core"
        )
    if type(request.completed_projection) is not TargetPostCellProjection:
        raise ApplicationResultContractError(
            "Target application-result request requires an exact Target projection"
        )


def _validate_untarget_request(request: UntargetApplicationResultRequest) -> None:
    from xic_extractor.alignment.projection.untarget.post_cell import (
        UntargetPostCellProjection,
    )

    if type(request.scientific_core) is not FinalizedScientificAlignment:
        raise ApplicationResultContractError(
            "Untarget application-result request requires an exact scientific core"
        )
    if type(request.completed_projection) is not UntargetPostCellProjection:
        raise ApplicationResultContractError(
            "Untarget application-result request requires an exact Untarget projection"
        )


def _validate_common_result(
    *,
    scientific_core: FinalizedScientificAlignment,
) -> None:
    try:
        validate_finalized_scientific_alignment(scientific_core)
    except FinalizedScientificAlignmentContractError as exc:
        raise ApplicationResultContractError(str(exc)) from exc


def _validate_target_result(result: FinalizedTargetApplicationResult) -> None:
    from xic_extractor.alignment.projection.target.post_cell import (
        TargetPostCellProjection,
        validate_target_post_cell_completion_receipt,
    )
    from xic_extractor.alignment.projection.target.pre_cell import (
        TargetProjectionContractError,
    )

    _validate_common_result(
        scientific_core=result.scientific_core,
    )
    if type(result.scientific_core.scope) is not TargetSearchScope:
        raise ApplicationResultContractError(
            "Target application result requires a Target scientific core"
        )
    if type(result.completed_projection) is not TargetPostCellProjection:
        raise ApplicationResultContractError(
            "Target application result requires an exact completed Target projection"
        )
    try:
        validate_target_post_cell_completion_receipt(result.completed_projection)
    except TargetProjectionContractError as exc:
        raise ApplicationResultContractError(str(exc)) from exc
    projection = result.completed_projection
    pre_cell = projection.pre_cell_projection
    core = result.scientific_core
    if pre_cell.scope is not core.scope:
        raise ApplicationResultContractError(
            "Target completed projection crossed or substituted its scientific core"
        )
    if len(projection.cell_subjects) != len(core.canonical_samples) or any(
        subject.canonical_sample is not sample
        for subject, sample in zip(
            projection.cell_subjects,
            core.canonical_samples,
            strict=True,
        )
    ):
        raise ApplicationResultContractError(
            "Target completed projection crossed the canonical sample universe"
        )
    if any(
        not any(
            subject.terminal_subject is terminal
            for terminal in core.terminal_subjects
        )
        for subject in projection.cell_subjects
    ):
        raise ApplicationResultContractError(
            "Target completed projection substituted a terminal scientific owner"
        )
    if any(
        not any(group is owner_group for owner_group in core.final_population)
        for group in projection.row_binding.eligible_groups
    ):
        raise ApplicationResultContractError(
            "Target completed projection substituted a finalized group owner"
        )


def _validate_untarget_result(result: FinalizedUntargetApplicationResult) -> None:
    from xic_extractor.alignment.projection.untarget.post_cell import (
        UntargetPostCellProjection,
        validate_untarget_post_cell_completion_receipt,
    )
    from xic_extractor.alignment.projection.untarget.pre_cell import (
        UntargetProjectionContractError,
    )

    _validate_common_result(
        scientific_core=result.scientific_core,
    )
    if type(result.scientific_core.scope) is not UntargetedSearchScope:
        raise ApplicationResultContractError(
            "Untarget application result requires an Untargeted scientific core"
        )
    if type(result.completed_projection) is not UntargetPostCellProjection:
        raise ApplicationResultContractError(
            "Untarget application result requires an exact completed "
            "Untarget projection"
        )
    try:
        validate_untarget_post_cell_completion_receipt(result.completed_projection)
    except UntargetProjectionContractError as exc:
        raise ApplicationResultContractError(str(exc)) from exc
    projection = result.completed_projection
    pre_cell = projection.pre_cell_projection
    if pre_cell.finalized_alignment is not result.scientific_core:
        raise ApplicationResultContractError(
            "Untarget completed projection crossed or substituted its scientific core"
        )


def _require_result_mint(value: object) -> None:
    if value is not _APPLICATION_RESULT_MINT_TOKEN:
        raise ApplicationResultContractError(
            "finalized application result must be minted by its owner finalizer"
        )


__all__ = (
    "ApplicationResultContractError",
    "ApplicationResultRequest",
    "FinalizedApplicationResult",
    "FinalizedTargetApplicationResult",
    "FinalizedUntargetApplicationResult",
    "TargetApplicationResultRequest",
    "UntargetApplicationResultRequest",
    "finalize_alignment_result",
)

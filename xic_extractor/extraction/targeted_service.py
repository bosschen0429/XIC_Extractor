from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from xic_extractor.config import ExtractionConfig, Target
from xic_extractor.processing_lineage import AcquisitionSourceRef
from xic_extractor.rt_prior_library import LibraryEntry

if TYPE_CHECKING:
    from xic_extractor.extractor import RunOutput
    from xic_extractor.peak_detection.model_selection import (
        ExpectedDiffApprovalRecord,
    )

TARGETED_APPLICATION_CONTRACT_VERSION = "targeted_application_v1"


class TargetedRunner(Protocol):
    def __call__(
        self,
        config: ExtractionConfig,
        targets: list[Target],
        progress_callback: Callable[[int, int, str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
        injection_order: dict[str, int] | None = None,
        rt_prior_library: dict[tuple[str, str], LibraryEntry] | None = None,
        model_selection_expected_diff_approvals: (
            Mapping[str, ExpectedDiffApprovalRecord] | None
        ) = None,
    ) -> RunOutput: ...


@dataclass(frozen=True)
class TargetedApplicationRequest:
    """Headless application input; callbacks are lifecycle adapters, not science."""

    config: ExtractionConfig
    targets: tuple[Target, ...]
    progress_callback: Callable[[int, int, str], None] | None = None
    should_stop: Callable[[], bool] | None = None
    injection_order: dict[str, int] | None = None
    rt_prior_library: dict[tuple[str, str], LibraryEntry] | None = None
    model_selection_expected_diff_approvals: (
        Mapping[str, ExpectedDiffApprovalRecord] | None
    ) = None

    def __post_init__(self) -> None:
        if not self.targets:
            raise ValueError(
                "Targeted application request requires at least one target"
            )
        labels = tuple(target.label for target in self.targets)
        if any(not label.strip() for label in labels):
            raise ValueError("Targeted application target labels cannot be blank")
        if len(labels) != len(set(labels)):
            raise ValueError("Targeted application target labels must be unique")


@dataclass(frozen=True)
class CanonicalTargetedRequest:
    """Pickle-safe scientific parity key shared by CLI and GUI adapters."""

    contract_version: str
    preset_id: str
    acquisition_source_ids: tuple[str, ...]
    config_hash: str
    target_config_hash: str
    targets: tuple[Target, ...]


@dataclass(frozen=True)
class TargetedApplicationService:
    runner: TargetedRunner

    def execute(self, request: TargetedApplicationRequest) -> RunOutput:
        return self.runner(
            request.config,
            list(request.targets),
            progress_callback=request.progress_callback,
            should_stop=request.should_stop,
            injection_order=request.injection_order,
            rt_prior_library=request.rt_prior_library,
            model_selection_expected_diff_approvals=(
                request.model_selection_expected_diff_approvals
            ),
        )


def build_targeted_application_request(
    config: ExtractionConfig,
    targets: list[Target],
    *,
    progress_callback: Callable[[int, int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    injection_order: dict[str, int] | None = None,
    rt_prior_library: dict[tuple[str, str], LibraryEntry] | None = None,
    model_selection_expected_diff_approvals: (
        Mapping[str, ExpectedDiffApprovalRecord] | None
    ) = None,
) -> TargetedApplicationRequest:
    return TargetedApplicationRequest(
        config=config,
        targets=tuple(targets),
        progress_callback=progress_callback,
        should_stop=should_stop,
        injection_order=injection_order,
        rt_prior_library=rt_prior_library,
        model_selection_expected_diff_approvals=(
            model_selection_expected_diff_approvals
        ),
    )


def build_canonical_targeted_request(
    request: TargetedApplicationRequest,
    *,
    sources: tuple[AcquisitionSourceRef, ...],
    preset_id: str,
) -> CanonicalTargetedRequest:
    if not sources:
        raise ValueError("canonical Targeted request requires identified sources")
    source_ids = tuple(sorted(source.source_id for source in sources))
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("canonical Targeted source identities must be unique")
    if not preset_id.strip():
        raise ValueError("canonical Targeted request requires preset_id")
    if not request.config.config_hash or not request.config.target_config_hash:
        raise ValueError(
            "canonical Targeted request requires effective config and target hashes"
        )
    return CanonicalTargetedRequest(
        contract_version=TARGETED_APPLICATION_CONTRACT_VERSION,
        preset_id=preset_id,
        acquisition_source_ids=source_ids,
        config_hash=request.config.config_hash,
        target_config_hash=request.config.target_config_hash,
        targets=request.targets,
    )

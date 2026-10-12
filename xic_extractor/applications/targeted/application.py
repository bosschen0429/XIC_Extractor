from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypeAlias

from xic_extractor.alignment.result.application_result import (
    FinalizedTargetApplicationResult,
)
from xic_extractor.configuration.models import ConfigError, ExtractionConfig, Target

if TYPE_CHECKING:
    from xic_extractor.applications.targeted.production_workflow import (
        TargetedProductionInputSnapshot,
    )
    from xic_extractor.extraction.result_models import RunOutput
    from xic_extractor.processing_identity import (
        AcquisitionSourceExpectation,
        ProcessingRequestIdentity,
    )
    from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity


class TargetedRunner(Protocol):
    def __call__(
        self,
        config: ExtractionConfig,
        targets: list[Target],
        progress_callback: Callable[[int, int, str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
        injection_order: dict[str, int] | None = None,
        acquisition_sources_by_sample: (
            Mapping[str, AcquisitionSourceExpectation] | None
        ) = None,
    ) -> RunOutput: ...


FinalizedTargetProductionResult: TypeAlias = FinalizedTargetApplicationResult


class TargetedApplicationError(ValueError):
    """Raised when a Targeted application invariant or population contract fails."""


@dataclass(frozen=True)
class TargetedApplicationRequest:
    """Headless application input; callbacks are lifecycle adapters, not science."""

    config: ExtractionConfig
    targets: tuple[Target, ...]
    progress_callback: Callable[[int, int, str], None] | None = None
    should_stop: Callable[[], bool] | None = None
    injection_order: dict[str, int] | None = None

    excel_output_path: Path | None = None
    replace_existing_outputs: bool = False


@dataclass(frozen=True)
class TargetedApplicationExecution:
    """One adapter-neutral acquisition with complete finalized publication."""

    active_output: RunOutput
    finalized_results: tuple[FinalizedTargetProductionResult, ...] = ()
    published_paths: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True)
class TargetedPublication:
    """The committed paths and exact output view consumed by their writers."""

    paths: tuple[Path, ...]
    output: RunOutput


@dataclass(frozen=True)
class TargetedApplicationService:
    runner: TargetedRunner

    def execute(self, request: TargetedApplicationRequest) -> RunOutput:
        """Preserve the existing application API while using the canonical path."""

        return self.execute_application(request).active_output

    def execute_application(
        self,
        request: TargetedApplicationRequest,
    ) -> TargetedApplicationExecution:
        """Acquire once and publish only a complete finalized Target population."""

        resolved_request = _isolate_application_request(request)
        from xic_extractor.applications.targeted.production_workflow import (
            complete_prepared_targeted_production_workflow,
            prepare_targeted_production_workflow,
            snapshot_targeted_production_inputs,
        )
        from xic_extractor.processing_identity import FingerprintCancelledError

        try:
            input_snapshot = snapshot_targeted_production_inputs(resolved_request)
        except FingerprintCancelledError:
            from xic_extractor.extraction.result_models import RunOutput

            return TargetedApplicationExecution(
                active_output=RunOutput(file_results=[], diagnostics=[])
            )
        runner_request = _isolate_application_request(resolved_request)
        active_output = self.runner(
            runner_request.config,
            list(runner_request.targets),
            progress_callback=runner_request.progress_callback,
            should_stop=runner_request.should_stop,
            injection_order=runner_request.injection_order,
            acquisition_sources_by_sample=_acquisition_sources_by_sample(
                input_snapshot
            ),
        )
        if resolved_request.should_stop is not None and resolved_request.should_stop():
            return TargetedApplicationExecution(active_output=active_output)
        if not active_output.file_results:
            raise ConfigError("No RAW files were acquired for Targeted extraction.")
        prepared = prepare_targeted_production_workflow(
            resolved_request,
            active_output,
            expected_inputs=input_snapshot,
        )
        completed_workflow = complete_prepared_targeted_production_workflow(
            resolved_request,
            prepared,
        )
        finalized_results = completed_workflow.finalized_results
        validate_finalized_result_population(
            resolved_request,
            finalized_results,
            expected_processing_request=prepared.owners.request,
            expected_samples=prepared.samples,
        )
        if resolved_request.should_stop is not None and resolved_request.should_stop():
            return TargetedApplicationExecution(
                active_output=active_output,
                finalized_results=finalized_results,
            )
        from xic_extractor.applications.targeted.output_boundary import (
            publish_targeted_outputs,
        )

        publication = publish_targeted_outputs(
            resolved_request,
            completed_workflow,
            active_output,
        )
        return TargetedApplicationExecution(
            active_output=publication.output,
            finalized_results=finalized_results,
            published_paths=publication.paths,
        )


def validate_finalized_result_population(
    request: TargetedApplicationRequest,
    results: tuple[FinalizedTargetProductionResult, ...],
    *,
    expected_processing_request: ProcessingRequestIdentity,
    expected_samples: tuple[ResolvedCanonicalSampleIdentity, ...],
) -> None:
    if type(results) is not tuple or any(
        type(result) is not FinalizedTargetApplicationResult for result in results
    ):
        raise TargetedApplicationError(
            "finalized workflow must return an exact immutable Target result tuple"
        )
    if len(results) != len(request.targets):
        raise TargetedApplicationError(
            "finalized workflow did not cover every declared Target"
        )
    request_ids = {
        result.processing_request.processing_request_id for result in results
    }
    sample_populations = {
        tuple(sample.identity.sample_identity_id for sample in result.canonical_samples)
        for result in results
    }
    if len(request_ids) != 1 or len(sample_populations) != 1:
        raise TargetedApplicationError(
            "finalized Target results crossed request or sample populations"
        )
    expected_sample_population = tuple(
        sorted(sample.identity.sample_identity_id for sample in expected_samples)
    )
    if any(
        result.processing_request != expected_processing_request
        or tuple(
            sorted(
                sample.identity.sample_identity_id
                for sample in result.canonical_samples
            )
        )
        != expected_sample_population
        for result in results
    ):
        raise TargetedApplicationError(
            "finalized Target results do not belong to the current acquisition"
        )
    for target, result in zip(request.targets, results, strict=True):
        finalized_target = (
            result.completed_projection.row_binding.target.definition.target
        )
        if finalized_target != target:
            raise TargetedApplicationError(
                "finalized Target result order differs from the declared Target list"
            )


def _acquisition_sources_by_sample(
    snapshot: TargetedProductionInputSnapshot,
) -> dict[str, AcquisitionSourceExpectation]:
    from xic_extractor.processing_identity import AcquisitionSourceExpectation

    request = snapshot.processing_request
    sources_by_id = {
        source.acquisition_source_id: source for source in request.acquisition_sources
    }
    states_by_stem = {
        path.stem: state
        for path, state in zip(
            snapshot.raw_paths,
            snapshot.raw_file_states,
            strict=True,
        )
    }
    return {
        binding.adapter_input_key: AcquisitionSourceExpectation(
            source=sources_by_id[binding.acquisition_source_id],
            path_identity=states_by_stem[binding.adapter_input_key],
        )
        for binding in request.acquisition_bindings
    }


def build_targeted_application_request(
    config: ExtractionConfig,
    targets: list[Target],
    *,
    progress_callback: Callable[[int, int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    injection_order: dict[str, int] | None = None,
    excel_output_path: Path | None = None,
    replace_existing_outputs: bool = False,
) -> TargetedApplicationRequest:
    return TargetedApplicationRequest(
        config=config,
        targets=tuple(targets),
        progress_callback=progress_callback,
        should_stop=should_stop,
        injection_order=injection_order,
        excel_output_path=excel_output_path,
        replace_existing_outputs=replace_existing_outputs,
    )


def _isolate_application_request(
    request: TargetedApplicationRequest,
) -> TargetedApplicationRequest:
    """Copy mutable data inputs while retaining lifecycle callback adapters."""

    return replace(
        request,
        config=deepcopy(request.config),
        targets=deepcopy(request.targets),
        injection_order=deepcopy(request.injection_order),
    )


def build_canonical_targeted_application_service(
    runner: TargetedRunner,
) -> TargetedApplicationService:
    """Build the formal Targeted application for Python, CLI, and GUI."""

    return TargetedApplicationService(runner)

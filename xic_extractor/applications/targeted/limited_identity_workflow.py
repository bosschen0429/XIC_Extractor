"""Run the existing limited identity method and publish only its gated result."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from xic_extractor.applications.targeted.application import (
    TargetedApplicationExecution,
    TargetedApplicationRequest,
    TargetedApplicationService,
)
from xic_extractor.configuration.models import ConfigError, ExtractionConfig
from xic_extractor.diagnostics.targeted_ms1_shape_identity_auto_diff import (
    TargetedMs1ShapeIdentityAutoDiffOutputs,
    write_targeted_ms1_shape_identity_auto_diff_artifacts,
)
from xic_extractor.diagnostics.targeted_ms1_shape_identity_support_producer import (
    TargetedMs1ShapeIdentitySupportOutputs,
    run_build_targeted_ms1_shape_identity_supports,
)
from xic_extractor.extraction.targeted_inputs import (
    resolve_model_selection_expected_diff_approvals,
)
from xic_extractor.output.method_manifest import MethodManifestContext
from xic_extractor.targeted_ms1_shape_identity_policy import (
    LIMITED_HMDC_MEDC_POLICY,
    LIMITED_HMDC_MEDC_TARGETS,
)


def requires_limited_identity_workflow(config: ExtractionConfig) -> bool:
    """The limited method needs generated support unless support was supplied."""
    return (
        config.targeted_ms1_shape_identity_activation_policy == LIMITED_HMDC_MEDC_POLICY
        and config.targeted_ms1_shape_identity_support_tsv is None
    )


@dataclass(frozen=True)
class LimitedIdentityWorkflowExecution:
    final_execution: TargetedApplicationExecution
    final_config: ExtractionConfig
    baseline_output_dir: Path
    support_outputs: TargetedMs1ShapeIdentitySupportOutputs
    diff_outputs: TargetedMs1ShapeIdentityAutoDiffOutputs


def execute_limited_identity_workflow(
    request: TargetedApplicationRequest,
    *,
    application: TargetedApplicationService,
    output_root: Path,
) -> LimitedIdentityWorkflowExecution:
    """Baseline → admitted-target support → final → expected-diff gate → publish.

    The caller freezes method inputs before entry and excludes replay. Cancellation
    is cooperative between stages and between support RAW reads. Diagnostic
    baseline/support and unverified outputs may remain after failure; no verified
    final is published until the unchanged method-specific gate succeeds.
    """
    context = request.method_manifest_context
    if not isinstance(context, MethodManifestContext):
        raise ConfigError(
            "Limited identity workflow requires a method manifest context"
        )
    output_root = Path(output_root)
    baseline_dir = output_root / "baseline" / "output"
    staging_dir = output_root / "final_unverified" / "output"
    final_dir = output_root / "final" / "output"
    for path in (staging_dir, final_dir):
        if path.exists():
            raise ConfigError(
                f"{path}: limited identity output already exists; "
                "choose a new output directory"
            )
    _check_cancel(request)
    approvals = resolve_model_selection_expected_diff_approvals(
        request.config, request.model_selection_expected_diff_approvals
    )
    baseline_config = replace(
        request.config,
        output_csv=baseline_dir / "xic_results.csv",
        diagnostics_csv=baseline_dir / "xic_diagnostics.csv",
        keep_intermediate_csv=True,
        targeted_ms1_shape_identity_support_tsv=None,
        targeted_ms1_shape_identity_activation_policy=LIMITED_HMDC_MEDC_POLICY,
    )
    baseline_overrides = {
        **(context.settings_overrides or {}),
        "targeted_ms1_shape_identity_activation_policy": LIMITED_HMDC_MEDC_POLICY,
    }
    _stage(request, "Auto limited baseline")
    application.execute_application(
        replace(
            request,
            config=baseline_config,
            model_selection_expected_diff_approvals=approvals,
            excel_output_path=None,
            replace_existing_outputs=False,
            output_projection="acquisition_baseline",
            method_manifest_context=replace(
                context,
                entrypoint=f"{context.entrypoint}-targeted-ms1-auto-baseline",
                settings_overrides=baseline_overrides,
                output_mode="csv_only",
            ),
        )
    )
    _check_cancel(request)
    _stage(request, "Auto limited support")
    support_tsv = output_root / "support" / "targeted_ms1_shape_identity.tsv"
    support_outputs = run_build_targeted_ms1_shape_identity_supports(
        long_csv=baseline_config.output_csv.with_name("xic_results_long.csv"),
        raw_dir=baseline_config.data_dir,
        dll_dir=baseline_config.dll_dir,
        config_dir=context.config_dir,
        targets=request.targets,
        output_tsv=support_tsv,
        target_names=tuple(sorted(LIMITED_HMDC_MEDC_TARGETS)),
        should_cancel=request.should_stop,
    )
    _check_cancel(request)
    final_config = replace(
        request.config,
        output_csv=staging_dir / "xic_results.csv",
        diagnostics_csv=staging_dir / "xic_diagnostics.csv",
        keep_intermediate_csv=True,
        targeted_ms1_shape_identity_support_tsv=support_tsv,
        targeted_ms1_shape_identity_activation_policy=LIMITED_HMDC_MEDC_POLICY,
    )
    final_overrides = {
        **baseline_overrides,
        "targeted_ms1_shape_identity_support_tsv": str(support_tsv.resolve()),
    }
    _stage(request, "Auto limited final")
    final_execution = application.execute_application(
        replace(
            request,
            config=final_config,
            model_selection_expected_diff_approvals=approvals,
            excel_output_path=(
                staging_dir / request.excel_output_path.name
                if request.excel_output_path is not None
                else None
            ),
            replace_existing_outputs=False,
            output_projection="finalized",
            method_manifest_context=replace(
                context,
                entrypoint=f"{context.entrypoint}-targeted-ms1-auto-limited-default",
                settings_overrides=final_overrides,
            ),
        )
    )
    _check_cancel(request)
    _stage(request, "Auto limited expected-diff gate")
    diff_outputs = write_targeted_ms1_shape_identity_auto_diff_artifacts(
        baseline_output_dir=baseline_dir,
        optin_output_dir=staging_dir,
        support_tsv=support_tsv,
        output_dir=output_root,
    )
    _check_cancel(request)
    if not staging_dir.is_dir():
        raise ConfigError(f"{staging_dir}: unverified final output directory missing")
    if final_dir.exists():
        raise ConfigError(f"{final_dir}: verified final output already exists")
    published_paths = tuple(
        final_dir / path.relative_to(staging_dir)
        for path in final_execution.published_paths
    )
    final_dir.parent.mkdir(parents=True, exist_ok=True)
    _check_cancel(request)
    staging_dir.replace(final_dir)
    try:
        staging_dir.parent.rmdir()
    except OSError:
        pass
    # Manifest output descriptors are relative to their containing output folder;
    # input descriptors point to the retained frozen config and support, unchanged.
    final_execution = replace(
        final_execution,
        published_paths=published_paths,
    )
    return LimitedIdentityWorkflowExecution(
        final_execution=final_execution,
        final_config=replace(
            final_config,
            output_csv=final_dir / "xic_results.csv",
            diagnostics_csv=final_dir / "xic_diagnostics.csv",
        ),
        baseline_output_dir=baseline_dir,
        support_outputs=support_outputs,
        diff_outputs=diff_outputs,
    )


def _check_cancel(request: TargetedApplicationRequest) -> None:
    if request.should_stop is not None and request.should_stop():
        raise InterruptedError(
            "Limited identity workflow cancelled; verified final was not published"
        )


def _stage(request: TargetedApplicationRequest, message: str) -> None:
    if request.progress_callback is not None:
        request.progress_callback(0, 0, message)
    _check_cancel(request)

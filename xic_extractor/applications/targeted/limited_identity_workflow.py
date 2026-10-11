"""Run the existing limited identity method and publish only its gated result."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory

from xic_extractor.applications.targeted.application import (
    TargetedApplicationExecution,
    TargetedApplicationRequest,
    TargetedApplicationService,
)
from xic_extractor.configuration.models import ConfigError, ExtractionConfig
from xic_extractor.diagnostics.targeted_ms1_shape_identity_auto_diff import (
    write_targeted_ms1_shape_identity_auto_diff_artifacts,
)
from xic_extractor.diagnostics.targeted_ms1_shape_identity_support_producer import (
    run_build_targeted_ms1_shape_identity_supports,
)
from xic_extractor.extraction.targeted_inputs import (
    resolve_model_selection_expected_diff_approvals,
)
from xic_extractor.output.excel_pipeline import write_excel_from_run_output
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
    support_row_count: int
    expected_diff_row_count: int
    matrix_diff_cell_count: int
    gate_status: str


def execute_limited_identity_workflow(
    request: TargetedApplicationRequest,
    *,
    application: TargetedApplicationService,
    output_root: Path,
) -> LimitedIdentityWorkflowExecution:
    """Publish only the workbook after the limited-identity gate has passed.

    CSV/support/diff files serve the existing gate and live only for this call.
    The final commit uses the same cancellation and collision-safe publication
    transaction as ordinary Targeted output.
    """
    from xic_extractor.applications.targeted.output_boundary import (
        _commit_staged_outputs,
        _TargetedPublicationCancelled,
    )

    if request.excel_output_path is None:
        raise ConfigError("The Targeted workflow requires an XLSX output path")
    output_root = Path(output_root)
    output_root.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".xic-limited-", dir=output_root.parent) as temp:
        result = _execute_limited_identity_stages(
            request, application=application, output_root=Path(temp)
        )
        _check_cancel(request)
        workbook = next(
            path
            for path in result.final_execution.published_paths
            if path.suffix.lower() == ".xlsx"
        )
        destination = output_root / request.excel_output_path.name
        try:
            published = _commit_staged_outputs(
                {workbook: destination}, should_cancel=request.should_stop
            )
        except _TargetedPublicationCancelled:
            raise InterruptedError("Limited identity workflow cancelled") from None
        return replace(
            result,
            final_execution=replace(result.final_execution, published_paths=published),
            final_config=replace(
                request.config,
                output_csv=output_root / "xic_results.csv",
                diagnostics_csv=output_root / "xic_diagnostics.csv",
                keep_intermediate_csv=False,
            ),
        )


def _execute_limited_identity_stages(
    request: TargetedApplicationRequest,
    *,
    application: TargetedApplicationService,
    output_root: Path,
) -> LimitedIdentityWorkflowExecution:
    """Run the unchanged scientific gate in a private working directory."""
    output_root = Path(output_root)
    baseline_dir = output_root / "baseline" / "output"
    staging_dir = output_root / "final_unverified" / "output"
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
    _stage(request, "Auto limited baseline")
    application.execute_application(
        replace(
            request,
            config=baseline_config,
            model_selection_expected_diff_approvals=approvals,
            excel_output_path=None,
            replace_existing_outputs=False,
            output_projection="acquisition_baseline",
        )
    )
    _check_cancel(request)
    _stage(request, "Auto limited support")
    support_tsv = output_root / "support" / "targeted_ms1_shape_identity.tsv"
    support_outputs = run_build_targeted_ms1_shape_identity_supports(
        long_csv=baseline_config.output_csv.with_name("xic_results_long.csv"),
        raw_dir=baseline_config.data_dir,
        dll_dir=baseline_config.dll_dir,
        config_dir=None,
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
    _stage(request, "Auto limited final")
    final_execution = application.execute_application(
        replace(
            request,
            config=final_config,
            model_selection_expected_diff_approvals=approvals,
            excel_output_path=None,
            replace_existing_outputs=False,
            output_projection="finalized",
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
    assert request.excel_output_path is not None
    workbook = staging_dir / request.excel_output_path.name
    write_excel_from_run_output(
        final_config,
        list(request.targets),
        final_execution.active_output,
        output_path=workbook,
        extra_metadata=(
            ("limited_identity_gate", diff_outputs.gate_status),
            ("identity_support_rows", support_outputs.evidence_row_count),
            ("expected_diff_rows", diff_outputs.expected_diff_row_count),
            ("matrix_diff_cells", diff_outputs.matrix_diff_cell_count),
        ),
    )
    final_execution = replace(final_execution, published_paths=(workbook,))
    return LimitedIdentityWorkflowExecution(
        final_execution=final_execution,
        final_config=final_config,
        support_row_count=support_outputs.evidence_row_count,
        expected_diff_row_count=diff_outputs.expected_diff_row_count,
        matrix_diff_cell_count=diff_outputs.matrix_diff_cell_count,
        gate_status=diff_outputs.gate_status,
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

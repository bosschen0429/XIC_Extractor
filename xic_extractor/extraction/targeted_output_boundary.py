"""Owner-local publication boundary for finalized Targeted results."""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

from xic_extractor.extraction.output_dispatch import write_outputs
from xic_extractor.extraction.targeted_service import (
    FinalizedTargetMs1BackfillResult,
    FinalizedTargetProductionResult,
    TargetedApplicationRequest,
    TargetedPublication,
)
from xic_extractor.extractor import RunOutput
from xic_extractor.output.excel_pipeline import write_excel_from_run_output
from xic_extractor.output.method_manifest import (
    method_manifest_path,
    write_method_manifest,
)
from xic_extractor.peak_detection.targeted_product_projection import (
    TargetedProductProjection,
)


class TargetedOutputBoundaryError(RuntimeError):
    """Raised before a complete finalized output set can be published."""


@dataclass(frozen=True, slots=True)
class _FinalizedExtractionRenderView:
    acquisition: Any
    value: float | None
    no_write_reason: str

    def __getattr__(self, name: str) -> Any:
        return getattr(self.acquisition, name)

    @property
    def reported_rt(self) -> float | None:
        return None if self.no_write_reason else self.acquisition.reported_rt

    @property
    def reported_peak_area(self) -> float | None:
        return self.value

    @property
    def reported_peak_intensity(self) -> float | None:
        return (
            None if self.no_write_reason else self.acquisition.reported_peak_intensity
        )

    @property
    def reported_peak_start(self) -> float | None:
        return None if self.no_write_reason else self.acquisition.reported_peak_start

    @property
    def reported_peak_end(self) -> float | None:
        return None if self.no_write_reason else self.acquisition.reported_peak_end

    @property
    def reported_peak_width(self) -> float | None:
        return None if self.no_write_reason else self.acquisition.reported_peak_width

    @property
    def targeted_product_projection(self) -> TargetedProductProjection | None:
        projection = self.acquisition.targeted_product_projection
        if projection is None:
            return None
        if self.no_write_reason:
            return replace(
                projection,
                product_state="not_counted",
                counted_detection=False,
                review_state="review_required",
                projection_reason=self.no_write_reason,
            )
        return replace(
            projection,
            counted_detection=True,
            projection_reason="finalized_numeric",
        )


@dataclass(frozen=True, slots=True)
class _FinalizedFileRenderView:
    acquisition: Any
    results: dict[str, _FinalizedExtractionRenderView]

    def __getattr__(self, name: str) -> Any:
        return getattr(self.acquisition, name)

    @property
    def extraction_results(self) -> tuple[_FinalizedExtractionRenderView, ...]:
        return tuple(self.results.values())


@dataclass(frozen=True, slots=True)
class _FinalizedRunRenderView:
    file_results: tuple[_FinalizedFileRenderView, ...]
    diagnostics: list[Any]


def publish_targeted_outputs(
    request: TargetedApplicationRequest,
    finalized_results: tuple[FinalizedTargetProductionResult, ...],
    acquisition_evidence: RunOutput,
) -> TargetedPublication:
    """Stage and commit one complete render set after typed finalization."""

    render_view = (
        _build_acquisition_baseline_view(acquisition_evidence)
        if request.output_projection == "acquisition_baseline"
        else _build_render_view(finalized_results, acquisition_evidence)
    )
    output_dir = request.config.output_csv.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    stage_root = Path(tempfile.mkdtemp(prefix=".xic-targeted-publish-", dir=output_dir))
    try:
        stage_config = replace(
            request.config,
            output_csv=stage_root / request.config.output_csv.name,
            diagnostics_csv=stage_root / request.config.diagnostics_csv.name,
        )
        write_outputs(
            stage_config,
            list(request.targets),
            cast(RunOutput, render_view),
            write_manifest=False,
        )
        stage_excel: Path | None = None
        if request.excel_output_path is not None:
            stage_excel = stage_root / request.excel_output_path.name
            write_excel_from_run_output(
                stage_config,
                list(request.targets),
                cast(RunOutput, render_view),
                output_path=stage_excel,
            )
        staged_to_final = _staged_output_map(
            stage_root,
            request,
            stage_config=stage_config,
            stage_excel=stage_excel,
        )
        final_manifest = method_manifest_path(request.config)
        stage_manifest = method_manifest_path(stage_config)
        write_method_manifest(
            request.config,
            request.targets,
            context=request.method_manifest_context,
            destination=stage_manifest,
            artifact_sources={
                final: staged for staged, final in staged_to_final.items()
            },
        )
        staged_to_final[stage_manifest] = final_manifest
        if not request.replace_existing_outputs:
            _reject_collisions(staged_to_final.values())
        paths = _commit_staged_outputs(
            staged_to_final,
            replace_existing=request.replace_existing_outputs,
        )
        return TargetedPublication(paths=paths, output=render_view)
    except TargetedOutputBoundaryError:
        raise
    except Exception as exc:
        raise TargetedOutputBoundaryError(
            f"Targeted output render failed before publication: {exc}"
        ) from exc
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)


def _build_render_view(
    finalized_results: tuple[FinalizedTargetProductionResult, ...],
    acquisition_evidence: RunOutput,
) -> _FinalizedRunRenderView:
    decisions: dict[tuple[str, str], Any] = {}
    for result in finalized_results:
        if isinstance(result, FinalizedTargetMs1BackfillResult):
            for backfill_decision in result.decisions:
                key = (
                    backfill_decision.sample.metadata.sample_name,
                    result.target.label,
                )
                if key in decisions:
                    raise TargetedOutputBoundaryError(
                        "finalized Targeted decision population contains duplicates"
                    )
                decisions[key] = backfill_decision
            continue
        target_label = (
            result.completed_projection.row_binding.target.definition.target.label
        )
        for decision in result.completed_projection.decisions:
            subject = cast(Any, decision.subject)
            key = (subject.sample.metadata.sample_name, target_label)
            if key in decisions:
                raise TargetedOutputBoundaryError(
                    "finalized Targeted decision population contains duplicates"
                )
            decisions[key] = decision.payload

    files: list[_FinalizedFileRenderView] = []
    consumed: set[tuple[str, str]] = set()
    for file_result in acquisition_evidence.file_results:
        rendered: dict[str, _FinalizedExtractionRenderView] = {}
        for target_label, extraction in file_result.results.items():
            key = (file_result.sample_name, target_label)
            payload = decisions.get(key)
            if payload is None:
                raise TargetedOutputBoundaryError(
                    "finalized Targeted decisions do not cover acquisition evidence"
                )
            consumed.add(key)
            if payload.variant == "numeric":
                rendered[target_label] = _FinalizedExtractionRenderView(
                    extraction,
                    payload.value,
                    "",
                )
            else:
                rendered[target_label] = _FinalizedExtractionRenderView(
                    extraction,
                    None,
                    f"finalized_{payload.variant}",
                )
        files.append(_FinalizedFileRenderView(file_result, rendered))
    if consumed != set(decisions):
        raise TargetedOutputBoundaryError(
            "finalized Targeted decisions crossed the acquisition sample population"
        )
    return _FinalizedRunRenderView(
        file_results=tuple(files),
        diagnostics=list(acquisition_evidence.diagnostics),
    )


def _build_acquisition_baseline_view(
    acquisition_evidence: RunOutput,
) -> RunOutput:
    return RunOutput(
        file_results=list(acquisition_evidence.file_results),
        diagnostics=list(acquisition_evidence.diagnostics),
    )


def _staged_output_map(
    stage_root: Path,
    request: TargetedApplicationRequest,
    *,
    stage_config: Any,
    stage_excel: Path | None,
) -> dict[Path, Path]:
    mapping: dict[Path, Path] = {}
    for staged in stage_root.rglob("*"):
        if not staged.is_file():
            continue
        if staged == stage_config.output_csv:
            final = request.config.output_csv
        elif staged == stage_config.diagnostics_csv:
            final = request.config.diagnostics_csv
        elif stage_excel is not None and staged == stage_excel:
            assert request.excel_output_path is not None
            final = request.excel_output_path
        else:
            final = request.config.output_csv.parent / staged.relative_to(stage_root)
        mapping[staged] = final
    return mapping


def _reject_collisions(paths: Any) -> None:
    collisions = [path for path in paths if path.exists()]
    if collisions:
        joined = ", ".join(str(path) for path in sorted(collisions, key=str))
        raise TargetedOutputBoundaryError(
            f"Targeted output path already exists: {joined}"
        )


def _commit_staged_outputs(
    mapping: dict[Path, Path],
    *,
    replace_existing: bool = False,
) -> tuple[Path, ...]:
    moved: list[tuple[Path, Path]] = []
    backups: list[tuple[Path, Path]] = []
    ordered = sorted(mapping.items(), key=lambda item: item[1].name)
    try:
        if replace_existing:
            backup_dir = next(iter(mapping)).parent / ".existing"
            backup_dir.mkdir()
            for index, (_, final) in enumerate(ordered):
                if final.exists():
                    backup = backup_dir / f"{index}-{final.name}"
                    final.replace(backup)
                    backups.append((backup, final))
        for staged, final in ordered:
            final.parent.mkdir(parents=True, exist_ok=True)
            staged.replace(final)
            moved.append((staged, final))
    except OSError as exc:
        rollback_failures: list[Path] = []
        for staged, final in reversed(moved):
            try:
                final.replace(staged)
            except OSError:
                rollback_failures.append(final)
        for backup, final in reversed(backups):
            try:
                backup.replace(final)
            except OSError:
                rollback_failures.append(final)
        detail = (
            f"; rollback failed for {rollback_failures}" if rollback_failures else ""
        )
        raise TargetedOutputBoundaryError(
            f"Targeted output commit failed and was rolled back{detail}"
        ) from exc
    return tuple(final for _, final in ordered)


__all__ = [
    "TargetedOutputBoundaryError",
    "publish_targeted_outputs",
]

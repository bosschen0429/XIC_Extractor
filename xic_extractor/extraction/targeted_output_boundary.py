"""Owner-local publication boundary for finalized Targeted results."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator, cast

from xic_extractor.alignment.result.scientific_alignment import ObservedSearchSubject
from xic_extractor.extraction.output_dispatch import write_outputs
from xic_extractor.extraction.targeted_service import (
    FinalizedTargetProductionResult,
    TargetedApplicationRequest,
    TargetedPublication,
)
from xic_extractor.extractor import ExtractionResult, FileResult, RunOutput
from xic_extractor.output.excel_pipeline import write_excel_from_run_output
from xic_extractor.output.method_manifest import (
    method_manifest_path,
    write_method_manifest,
)
from xic_extractor.output.review_report import review_report_path_for_excel
from xic_extractor.peak_detection.models import PeakResult
from xic_extractor.peak_detection.targeted_product_projection import (
    TargetedProductProjection,
)


class TargetedOutputBoundaryError(RuntimeError):
    """Raised before a complete finalized output set can be published."""


class _TargetedPublicationCancelled(Exception):
    """Internal signal for a cancellation rolled back by the publish transaction."""


class _TargetedRollbackRecoveryRequired(TargetedOutputBoundaryError):
    """A failed rollback retained recoverable files in the staging root."""


_STAGE_OUTPUT_CSV = "__targeted_output__.csv"
_STAGE_DIAGNOSTICS_CSV = "__targeted_diagnostics__.csv"
_STAGE_EXCEL_OUTPUT = "__targeted_output__.xlsx"
_PUBLICATION_LOCK_NAME = ".xic-targeted-publish.lock"


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
    preserve_stage_root = False
    try:
        stage_config = replace(
            request.config,
            output_csv=stage_root / _STAGE_OUTPUT_CSV,
            diagnostics_csv=stage_root / _STAGE_DIAGNOSTICS_CSV,
        )
        write_outputs(
            stage_config,
            list(request.targets),
            render_view,
            write_manifest=False,
        )
        final_manifest = method_manifest_path(request.config)
        stage_manifest = method_manifest_path(stage_config)
        staged_before_excel = _staged_output_map(
            stage_root,
            request,
            stage_config=stage_config,
            stage_excel=None,
        )
        write_method_manifest(
            request.config,
            request.targets,
            context=request.method_manifest_context,
            destination=stage_manifest,
            artifact_sources={
                final: staged for staged, final in staged_before_excel.items()
            },
        )
        stage_excel: Path | None = None
        if request.excel_output_path is not None:
            stage_excel = stage_root / _STAGE_EXCEL_OUTPUT
            write_excel_from_run_output(
                stage_config,
                list(request.targets),
                render_view,
                output_path=stage_excel,
            )
        staged_to_final = _staged_output_map(
            stage_root,
            request,
            stage_config=stage_config,
            stage_excel=stage_excel,
        )
        staged_to_final[stage_manifest] = final_manifest
        _reject_duplicate_destinations(staged_to_final)
        paths = _commit_staged_outputs(
            staged_to_final,
            replace_existing=request.replace_existing_outputs,
            should_cancel=request.should_stop,
        )
        return TargetedPublication(paths=paths, output=render_view)
    except _TargetedPublicationCancelled:
        return TargetedPublication(paths=(), output=render_view)
    except _TargetedRollbackRecoveryRequired:
        preserve_stage_root = True
        raise
    except TargetedOutputBoundaryError:
        raise
    except Exception as exc:
        raise TargetedOutputBoundaryError(
            f"Targeted output render failed before publication: {exc}"
        ) from exc
    finally:
        if not preserve_stage_root:
            shutil.rmtree(stage_root, ignore_errors=True)


def _build_render_view(
    finalized_results: tuple[FinalizedTargetProductionResult, ...],
    acquisition_evidence: RunOutput,
) -> RunOutput:
    if not finalized_results:
        raise TargetedOutputBoundaryError(
            "finalized Targeted result population is empty"
        )
    expected_request = finalized_results[0].processing_request
    expected_samples = finalized_results[0].canonical_samples
    expected_sample_ids = tuple(
        sample.identity.sample_identity_id for sample in expected_samples
    )
    samples_by_raw_stem = {
        sample.metadata.raw_stem: sample for sample in expected_samples
    }
    if len(samples_by_raw_stem) != len(expected_samples):
        raise TargetedOutputBoundaryError(
            "finalized Targeted sample population contains duplicate RAW identities"
        )

    decisions: dict[tuple[str, str], Any] = {}
    target_ids_by_label: dict[str, str] = {}
    for result in finalized_results:
        if (
            result.processing_request != expected_request
            or tuple(
                sample.identity.sample_identity_id
                for sample in result.canonical_samples
            )
            != expected_sample_ids
        ):
            raise TargetedOutputBoundaryError(
                "finalized Targeted results crossed request or sample identities"
            )
        target = result.completed_projection.row_binding.target.definition
        target_label = target.target.label
        target_id = target.target_definition_id
        if target_label in target_ids_by_label:
            raise TargetedOutputBoundaryError(
                "finalized Targeted result population contains duplicate Targets"
            )
        target_ids_by_label[target_label] = target_id
        for projection_decision in result.completed_projection.decisions:
            subject = cast(Any, projection_decision.subject)
            sample_id = subject.sample.identity.sample_identity_id
            if sample_id not in expected_sample_ids:
                raise TargetedOutputBoundaryError(
                    "finalized Targeted decision crossed the canonical "
                    "sample population"
                )
            key = (sample_id, target_id)
            if key in decisions:
                raise TargetedOutputBoundaryError(
                    "finalized Targeted decision population contains duplicates"
                )
            decisions[key] = projection_decision

    files: list[FileResult] = []
    consumed: set[tuple[str, str]] = set()
    consumed_sample_ids: set[str] = set()
    for file_result in acquisition_evidence.file_results:
        sample = samples_by_raw_stem.get(file_result.sample_name)
        if sample is None:
            raise TargetedOutputBoundaryError(
                "acquisition evidence does not resolve to a canonical sample identity"
            )
        sample_id = sample.identity.sample_identity_id
        if sample_id in consumed_sample_ids:
            raise TargetedOutputBoundaryError(
                "acquisition evidence contains duplicate canonical samples"
            )
        consumed_sample_ids.add(sample_id)
        rendered: dict[str, ExtractionResult] = {}
        for target_label, extraction in file_result.results.items():
            resolved_target_id = target_ids_by_label.get(target_label)
            if resolved_target_id is None:
                raise TargetedOutputBoundaryError(
                    "acquisition evidence does not resolve to a finalized "
                    "Target identity"
                )
            key = (sample_id, resolved_target_id)
            render_decision = decisions.get(key)
            if render_decision is None:
                raise TargetedOutputBoundaryError(
                    "finalized Targeted decisions do not cover acquisition evidence"
                )
            consumed.add(key)
            no_write_reason = (
                ""
                if render_decision.variant == "numeric"
                else getattr(
                    render_decision,
                    "no_write_reason",
                    f"finalized_{render_decision.variant}",
                )
            )
            rendered[target_label] = _finalized_extraction_result(
                extraction,
                value=render_decision.value,
                no_write_reason=no_write_reason,
                evidence=getattr(render_decision, "evidence", None),
            )
        files.append(replace(file_result, results=rendered))
    if consumed != set(decisions):
        raise TargetedOutputBoundaryError(
            "finalized Targeted decisions crossed the acquisition sample population"
        )
    return RunOutput(
        file_results=files,
        diagnostics=list(acquisition_evidence.diagnostics),
    )


def _finalized_extraction_result(
    extraction: ExtractionResult,
    *,
    value: float | None,
    no_write_reason: str,
    evidence: object,
) -> ExtractionResult:
    projection = _finalized_product_projection(
        extraction.targeted_product_projection,
        no_write_reason=no_write_reason,
    )
    if not no_write_reason:
        if extraction.reported_peak_area == value:
            return replace(extraction, targeted_product_projection=projection)
        if (
            type(evidence) is not ObservedSearchSubject
            or _hypothesis_area(evidence.hypothesis) != value
        ):
            raise TargetedOutputBoundaryError(
                "finalized Targeted value does not match its public result model"
            )
        integration = evidence.hypothesis.integration
        public_selected = (
            None
            if extraction.selected_hypothesis is None
            else replace(extraction.selected_hypothesis, integration=integration)
        )
        rendered = replace(
            extraction,
            peak_result=replace(
                extraction.peak_result,
                peak=PeakResult(
                    rt=integration.rt_apex_min,
                    intensity=integration.height_raw,
                    intensity_smoothed=integration.height_smoothed,
                    area=value,
                    peak_start=integration.rt_left_min,
                    peak_end=integration.rt_right_min,
                ),
            ),
            selected_hypothesis=public_selected,
            selection_decision=None,
            model_selection_result=None,
            targeted_product_projection=projection,
        )
        if rendered.reported_peak_area != value:
            raise TargetedOutputBoundaryError(
                "finalized Targeted hypothesis did not produce its numeric value"
            )
        return rendered
    return replace(
        extraction,
        peak_result=replace(
            extraction.peak_result,
            peak=None,
            candidates=(),
            candidate_scores=(),
            confidence=None,
            reason=no_write_reason,
        ),
        selected_hypothesis=None,
        selection_decision=None,
        model_selection_result=None,
        targeted_product_projection=projection,
    )


def _hypothesis_area(hypothesis: Any) -> float:
    integration = hypothesis.integration
    if integration.area_ms1_morphology is not None:
        return integration.area_ms1_morphology
    return integration.area_raw_counts_seconds


def _finalized_product_projection(
    projection: TargetedProductProjection | None,
    *,
    no_write_reason: str,
) -> TargetedProductProjection | None:
    if projection is None:
        return None
    if no_write_reason:
        return replace(
            projection,
            product_state="not_counted",
            counted_detection=False,
            review_state="review_required",
            projection_reason=no_write_reason,
        )
    return replace(
        projection,
        product_state="detected_clean",
        counted_detection=True,
        review_state="none",
        projection_reason="finalized_numeric",
        review_reasons=(),
        conflict_reasons=(),
        not_counted_reasons=(),
        exclusion_reasons=(),
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
        elif stage_excel is not None and staged == review_report_path_for_excel(
            stage_excel
        ):
            assert request.excel_output_path is not None
            final = review_report_path_for_excel(request.excel_output_path)
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


def _reject_duplicate_destinations(mapping: Mapping[Path, Path]) -> None:
    staged_by_destination: dict[str, Path] = {}
    for staged, final in mapping.items():
        normalized_final = os.path.normcase(
            str(Path(final).expanduser().resolve(strict=False))
        )
        previous_staged = staged_by_destination.get(normalized_final)
        if previous_staged is not None:
            raise TargetedOutputBoundaryError(
                "Targeted output transaction maps staged artifacts "
                f"{previous_staged} and {staged} to duplicate destination "
                f"{normalized_final}"
            )
        staged_by_destination[normalized_final] = staged


def _commit_staged_outputs(
    mapping: dict[Path, Path],
    *,
    replace_existing: bool = False,
    should_cancel: Callable[[], bool] | None = None,
) -> tuple[Path, ...]:
    moved: list[tuple[Path, Path]] = []
    backups: list[tuple[Path, Path]] = []
    ordered = sorted(mapping.items(), key=lambda item: item[1].name)
    if not ordered:
        return ()
    recovery_root = ordered[0][0].parent
    lock_paths_by_key = {
        os.path.normcase(
            str(final.parent.expanduser().resolve(strict=False))
        ): final.parent.expanduser().resolve(strict=False) / _PUBLICATION_LOCK_NAME
        for _, final in ordered
    }

    def check_cancellation() -> None:
        if should_cancel is not None and should_cancel():
            raise _TargetedPublicationCancelled

    with ExitStack() as locks:
        for key in sorted(lock_paths_by_key):
            locks.enter_context(_publication_commit_lock(lock_paths_by_key[key]))
        try:
            if not replace_existing:
                _reject_collisions(final for _, final in ordered)
            if replace_existing:
                check_cancellation()
                backup_dir = next(iter(mapping)).parent / ".existing"
                backup_dir.mkdir()
                for index, (_, final) in enumerate(ordered):
                    if final.exists():
                        backup = backup_dir / f"{index}-{final.name}"
                        final.replace(backup)
                        backups.append((backup, final))
                        check_cancellation()
            for staged, final in ordered:
                check_cancellation()
                final.parent.mkdir(parents=True, exist_ok=True)
                staged.replace(final)
                moved.append((staged, final))
                check_cancellation()
        except (_TargetedPublicationCancelled, OSError) as exc:
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
                f"; rollback failed for {rollback_failures}"
                if rollback_failures
                else ""
            )
            if isinstance(exc, _TargetedPublicationCancelled):
                if rollback_failures:
                    raise _TargetedRollbackRecoveryRequired(
                        "Targeted output commit was cancelled and rollback failed"
                        f"{detail}; recoverable files retained at {recovery_root}"
                    ) from exc
                raise
            if rollback_failures:
                raise _TargetedRollbackRecoveryRequired(
                    "Targeted output commit failed and rollback was incomplete"
                    f"{detail}; recoverable files retained at {recovery_root}"
                ) from exc
            raise TargetedOutputBoundaryError(
                f"Targeted output commit failed and was rolled back{detail}"
            ) from exc
    return tuple(final for _, final in ordered)


@contextmanager
def _publication_commit_lock(lock_path: Path) -> Iterator[None]:
    """Claim the output root before rechecking and committing final paths."""

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    token = f"{os.getpid()}:{id(lock_path)}"
    descriptor: int | None = None
    open_flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if os.name == "nt":
        open_flags |= getattr(os, "O_TEMPORARY", 0)
    try:
        descriptor = os.open(
            lock_path,
            open_flags,
            0o600,
        )
    except FileExistsError as exc:
        raise TargetedOutputBoundaryError(
            f"Targeted output path already exists or is being published: {lock_path}"
        ) from exc
    assert descriptor is not None
    handle = os.fdopen(descriptor, "w", encoding="ascii")
    try:
        handle.write(token)
        handle.flush()
        yield
    finally:
        handle.close()
        try:
            if lock_path.read_text(encoding="ascii") == token:
                lock_path.unlink()
        except FileNotFoundError:
            pass


__all__ = [
    "TargetedOutputBoundaryError",
    "publish_targeted_outputs",
]

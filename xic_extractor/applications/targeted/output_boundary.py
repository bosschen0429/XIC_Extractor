"""Owner-local publication boundary for finalized Targeted results."""

from __future__ import annotations

import errno
import os
import shutil
import tempfile
from collections.abc import Callable, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator, cast

from xic_extractor.alignment.result.scientific_alignment import ObservedSearchSubject
from xic_extractor.applications.targeted.application import (
    FinalizedTargetProductionResult,
    TargetedApplicationRequest,
    TargetedPublication,
)
from xic_extractor.applications.targeted.production_workflow import (
    acquisition_snapshot_for_publication,
)
from xic_extractor.configuration.models import Target
from xic_extractor.extraction.result_models import (
    ExtractionResult,
    FileResult,
    RunOutput,
)
from xic_extractor.extraction.targeted_product_projection import (
    build_targeted_product_projection,
)
from xic_extractor.output.excel_pipeline import write_excel_from_run_output
from xic_extractor.output.messages import DiagnosticRecord
from xic_extractor.output.targeted_product_model import (
    TargetedProductContext,
    TargetedProductProjection,
)
from xic_extractor.peak_detection.models import PeakResult
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    HYPOTHESIS_METHOD_DDA_CID_NL,
)

if TYPE_CHECKING:
    from xic_extractor.applications.targeted.production_workflow import (
        CompletedTargetedProductionWorkflow,
    )


class TargetedOutputBoundaryError(RuntimeError):
    """Raised before a complete finalized output set can be published."""


class _TargetedPublicationCancelled(Exception):
    """Internal signal for a cancellation rolled back by the publish transaction."""


class _TargetedRollbackRecoveryRequired(TargetedOutputBoundaryError):
    """A failed rollback retained recoverable files in the staging root."""


_STAGE_EXCEL_OUTPUT = "__targeted_output__.xlsx"
_PUBLICATION_LOCK_NAME = ".xic-targeted-publish.lock"


def publish_targeted_outputs(
    request: TargetedApplicationRequest,
    completed_workflow: CompletedTargetedProductionWorkflow,
    acquisition_evidence: RunOutput,
) -> TargetedPublication:
    """Stage and commit one complete render set after typed finalization."""

    from xic_extractor.applications.targeted.production_workflow import (
        CompletedTargetedProductionWorkflow,
    )

    if type(completed_workflow) is not CompletedTargetedProductionWorkflow:
        raise TargetedOutputBoundaryError(
            "publication requires an exact attempt-bound completed workflow"
        )
    prepared = completed_workflow.prepared
    finalized_results = completed_workflow.finalized_results
    acquisition_file_ids = tuple(
        id(file_result) for file_result in acquisition_evidence.file_results
    )
    if acquisition_file_ids != prepared.acquisition_file_ids:
        raise TargetedOutputBoundaryError(
            "prepared Targeted workflow crossed the acquisition evidence"
        )
    try:
        acquisition_snapshot = acquisition_snapshot_for_publication(prepared)
    except RuntimeError as exc:
        raise TargetedOutputBoundaryError(str(exc)) from exc
    render_view = _build_render_view(finalized_results, acquisition_snapshot)
    output_dir = request.config.output_csv.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    stage_root = Path(tempfile.mkdtemp(prefix=".xic-targeted-publish-", dir=output_dir))
    preserve_stage_root = False
    try:
        # Without a workbook destination the run is computed but not published.
        staged_to_final: dict[Path, Path] = {}
        if request.excel_output_path is not None:
            stage_excel = stage_root / _STAGE_EXCEL_OUTPUT
            write_excel_from_run_output(
                request.config,
                list(request.targets),
                render_view,
                output_path=stage_excel,
            )
            staged_to_final[stage_excel] = request.excel_output_path
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
    targets_by_label: dict[str, Target] = {}
    backfills = {}
    backfill_diagnostics = []
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
        targets_by_label[target_label] = target.target
        for backfill in result.scientific_core.targeted_backfill_decisions:
            sample_id = backfill.target_cell.content.canonical_sample_identity_id
            backfills[(sample_id, target_id)] = backfill
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
            if extraction.target_label != target_label:
                raise TargetedOutputBoundaryError(
                    "acquisition evidence crossed its declared Target identity"
                )
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
            backfill_decision = backfills.get(key)
            if backfill_decision is not None:
                backfill_diagnostics.append(
                    DiagnosticRecord(
                        file_result.sample_name,
                        target_label,
                        "BACKFILL_ACCEPTED"
                        if backfill_decision.matched_candidate is not None
                        else "BACKFILL_HOLD",
                        "Cross-sample confirmed peak localization: "
                        + "; ".join(backfill_decision.reason_codes)
                        + "; direct CID/NL remains HOLD; "
                        + (
                            "ISTD identity confirmed by Backfill."
                            if targets_by_label[target_label].is_istd
                            and backfill_decision.matched_candidate is not None
                            else "confirmed=False."
                        ),
                    )
                )
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
                target=targets_by_label[target_label],
                value=render_decision.value,
                no_write_reason=no_write_reason,
                evidence=getattr(render_decision, "evidence", None),
                backfill_supported=(
                    backfill_decision is not None
                    and backfill_decision.matched_candidate is not None
                ),
            )
        files.append(replace(file_result, results=rendered))
    if consumed != set(decisions):
        raise TargetedOutputBoundaryError(
            "finalized Targeted decisions crossed the acquisition sample population"
        )
    return RunOutput(
        file_results=files,
        diagnostics=[*acquisition_evidence.diagnostics, *backfill_diagnostics],
    )


def _finalized_extraction_result(
    extraction: ExtractionResult,
    *,
    target: Target,
    value: float | None,
    no_write_reason: str,
    evidence: object,
    backfill_supported: bool = False,
) -> ExtractionResult:
    projection = _finalized_product_projection(
        extraction.targeted_product_projection,
        target=target,
        evidence=evidence,
        value=value,
        no_write_reason=no_write_reason,
        backfill_supported=backfill_supported,
    )
    if not no_write_reason:
        if (
            value is None
            or type(evidence) is not ObservedSearchSubject
            or _hypothesis_area(evidence.hypothesis) != value
        ):
            raise TargetedOutputBoundaryError(
                "finalized Targeted value does not match its public result model"
            )
        integration = evidence.hypothesis.integration
        physical_evaluation = evidence.hypothesis.peak_observation_refs[
            0
        ].physical_evaluation
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
                candidates=(),
                candidate_scores=(),
            ),
            selected_hypothesis=None,
            selection_decision=None,
            physical_evaluation=physical_evaluation,
            selected_cid_nl_candidate_index=next(
                (
                    index
                    for index, evaluation in enumerate(
                        extraction.candidate_physical_evaluations
                    )
                    if evaluation is physical_evaluation
                ),
                None,
            ),
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
    target: Target,
    evidence: object,
    value: float | None,
    no_write_reason: str,
    backfill_supported: bool = False,
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
    if type(evidence) is not ObservedSearchSubject:
        raise TargetedOutputBoundaryError(
            "numeric projection lost its finalized evidence"
        )
    return build_targeted_product_projection(
        TargetedProductContext(
            role="ISTD" if target.is_istd else "Analyte",
            expected_present=target.is_istd,
            target_label=target.label,
            istd_pair=target.istd_pair,
            neutral_loss_required=target.neutral_loss_da is not None,
        ),
        rt=evidence.hypothesis.integration.rt_apex_min,
        area=value,
        confidence=projection.legacy_evidence.get("confidence", ""),
        nl_status=projection.legacy_evidence.get("nl_status", ""),
        cid_nl_supported=(
            evidence.hypothesis.hypothesis_method == HYPOTHESIS_METHOD_DDA_CID_NL
        ),
        backfill_supported=backfill_supported,
        support_reasons=projection.support_reasons,
        review_reasons=projection.review_reasons,
        conflict_reasons=projection.conflict_reasons,
        not_counted_reasons=projection.not_counted_reasons,
        exclusion_reasons=projection.exclusion_reasons,
        legacy_evidence=projection.legacy_evidence,
    )


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
                if replace_existing:
                    staged.replace(final)
                else:
                    _publish_without_replace(staged, final)
                moved.append((staged, final))
                check_cancellation()
        except Exception as exc:
            rollback_failures: list[Path] = []
            for staged, final in reversed(moved):
                if not _restore_moved_output(final, staged):
                    rollback_failures.append(final)
            for backup, final in reversed(backups):
                if not _restore_previous_output(backup, final):
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
            if (
                not moved
                and not backups
                and isinstance(exc, TargetedOutputBoundaryError)
            ):
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


def _restore_moved_output(final: Path, staged: Path) -> bool:
    if staged.exists():
        return _remove_published_output(final, recovery_path=staged)
    try:
        final.replace(staged)
        return True
    except OSError:
        try:
            _atomic_copy_replace(final, staged)
            return _remove_published_output(final, recovery_path=staged)
        except OSError:
            return False


def _restore_previous_output(backup: Path, final: Path) -> bool:
    try:
        backup.replace(final)
        return True
    except OSError:
        try:
            _atomic_copy_replace(backup, final)
            backup.unlink()
            return True
        except OSError:
            return False


def _atomic_copy_replace(source: Path, destination: Path) -> None:
    """Copy through a private sibling so a failed copy never truncates live data."""

    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{destination.name}.rollback-",
        dir=destination.parent,
    )
    os.close(descriptor)
    temporary = Path(temp_name)
    try:
        shutil.copy2(source, temporary)
        with temporary.open("r+b") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _publish_without_replace(staged: Path, final: Path) -> None:
    """Atomically claim a new destination without replacing an outside writer."""

    try:
        os.link(staged, final)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{final.name}.publish-",
        dir=final.parent,
    )
    os.close(descriptor)
    temporary = Path(temp_name)
    try:
        shutil.copy2(staged, temporary)
        with temporary.open("r+b") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, final)
    finally:
        temporary.unlink(missing_ok=True)


def _remove_published_output(final: Path, *, recovery_path: Path) -> bool:
    try:
        os.unlink(final)
        return True
    except OSError:
        quarantine = recovery_path.with_name(f".{recovery_path.name}.published")
        try:
            os.replace(final, quarantine)
            return True
        except OSError:
            return False


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
        if os.name != "nt":
            try:
                if lock_path.read_text(encoding="ascii") == token:
                    lock_path.unlink()
            except FileNotFoundError:
                pass


__all__ = [
    "TargetedOutputBoundaryError",
    "publish_targeted_outputs",
]

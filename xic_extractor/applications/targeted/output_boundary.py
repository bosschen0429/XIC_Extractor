"""Render and atomically publish the completed Targeted owner's result view."""

from __future__ import annotations

import errno
import os
import shutil
import tempfile
from collections.abc import Callable, Mapping
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

from xic_extractor.applications.targeted.application import (
    TargetedApplicationRequest,
    TargetedOutputBoundaryError,
    TargetedPublication,
)
from xic_extractor.extraction.result_models import RunOutput
from xic_extractor.output.excel_pipeline import write_excel_from_run_output

if TYPE_CHECKING:
    from xic_extractor.applications.targeted.production_workflow import (
        CompletedTargetedProductionWorkflow,
    )


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
    render_view = completed_workflow.publication_output(request, acquisition_evidence)
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

"""Render and atomically publish the completed Targeted owner's result view."""

from __future__ import annotations

import errno
import os
import shutil
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

from xic_extractor.applications.targeted.application import (
    TargetedOutputBoundaryError,
    TargetedPublication,
)
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
    completed_workflow: CompletedTargetedProductionWorkflow,
    *,
    excel_output_path: Path | None,
    replace_existing: bool = False,
    should_cancel: Callable[[], bool] | None = None,
) -> TargetedPublication:
    """Render one completed workbook and publish it without partial output."""

    render_view = completed_workflow.publication_output
    if excel_output_path is None or (should_cancel is not None and should_cancel()):
        return TargetedPublication(paths=(), output=render_view)
    output_dir = completed_workflow.config.output_csv.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    stage_root = Path(tempfile.mkdtemp(prefix=".xic-targeted-publish-", dir=output_dir))
    preserve_stage_root = False
    try:
        stage_excel = stage_root / _STAGE_EXCEL_OUTPUT
        write_excel_from_run_output(
            completed_workflow.config,
            list(completed_workflow.targets),
            render_view,
            output_path=stage_excel,
        )
        _commit_workbook(
            stage_excel,
            excel_output_path,
            replace_existing=replace_existing,
            should_cancel=should_cancel,
        )
        return TargetedPublication(paths=(excel_output_path,), output=render_view)
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


def _commit_workbook(
    staged: Path,
    final: Path,
    *,
    replace_existing: bool = False,
    should_cancel: Callable[[], bool] | None = None,
) -> None:
    backup = staged.parent / ".previous.xlsx"
    previous_saved = False
    published = False

    def check_cancellation() -> None:
        if should_cancel is not None and should_cancel():
            raise _TargetedPublicationCancelled

    lock_path = final.parent.expanduser().resolve(strict=False) / _PUBLICATION_LOCK_NAME
    with _publication_commit_lock(lock_path):
        if not replace_existing and final.exists():
            raise TargetedOutputBoundaryError(
                f"Targeted output path already exists: {final}"
            )
        try:
            check_cancellation()
            if replace_existing and final.exists():
                final.replace(backup)
                previous_saved = True
                check_cancellation()
            if replace_existing:
                staged.replace(final)
            else:
                _publish_without_replace(staged, final)
            published = True
            check_cancellation()
        except Exception as exc:
            recovered = True
            if previous_saved:
                recovered = _restore_previous_output(backup, final)
            elif published:
                recovered = _remove_published_output(final, recovery_path=staged)
            if not recovered:
                raise _TargetedRollbackRecoveryRequired(
                    f"Targeted output publication failed and rollback was incomplete: "
                    f"{final}; recoverable files retained at {staged.parent}"
                ) from exc
            if isinstance(exc, _TargetedPublicationCancelled):
                raise
            raise TargetedOutputBoundaryError(
                f"Targeted output commit failed and was rolled back: {final}: {exc}"
            ) from exc


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

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xic_extractor.alignment.result.application_result import (
        FinalizedUntargetBatchApplicationResult,
    )
    from xic_extractor.alignment.sample_rt_calibration import BatchRtCalibration
    from xic_extractor.evidence_artifacts import EvidenceLocator

from xic_extractor.alignment.backfill.evaluation_coverage import (
    BackfillEvaluationCoverageBatch,
)
from xic_extractor.configuration.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
    SignalProcessingProfile,
    serialize_signal_processing_profile,
)
from xic_extractor.execution.timing import TimingRecorder
from xic_extractor.output.alignment.output_levels import (
    AlignmentOutputLevel,
    artifact_names_for_output_level,
)
from xic_extractor.output.alignment.rt_anchor_table import write_rt_anchor_table
from xic_extractor.output.alignment.tsv_writer import (
    UntargetChildOutputs,
    write_alignment_tsvs,
)


@dataclass(frozen=True)
class AlignmentRunOutputs:
    review_tsv: Path | None = None
    matrix_tsv: Path | None = None
    backfill_cell_evidence_tsv: Path | None = None
    cells_tsv: Path | None = None
    status_matrix_tsv: Path | None = None
    run_metadata_json: Path | None = None
    rt_anchors_tsv: Path | None = None
    owner_backfill_evaluation_coverage: BackfillEvaluationCoverageBatch | None = None
    evidence: EvidenceLocator | None = None


def output_paths(
    output_dir: Path,
    *,
    output_level: AlignmentOutputLevel,
    emit_alignment_cells: bool,
    emit_alignment_status_matrix: bool,
    emit_target_snapshot_metadata: bool = False,
) -> AlignmentRunOutputs:
    artifacts = set(artifact_names_for_output_level(output_level))
    if emit_alignment_cells:
        artifacts.add("alignment_cells.tsv")
    if emit_alignment_status_matrix:
        artifacts.add("alignment_matrix_status.tsv")
    return AlignmentRunOutputs(
        review_tsv=(
            output_dir / "alignment_review.tsv"
            if "alignment_review.tsv" in artifacts
            else None
        ),
        matrix_tsv=(
            output_dir / "alignment_matrix.tsv"
            if "alignment_matrix.tsv" in artifacts
            else None
        ),
        cells_tsv=(
            output_dir / "alignment_cells.tsv"
            if "alignment_cells.tsv" in artifacts
            else None
        ),
        backfill_cell_evidence_tsv=(
            output_dir / "alignment_backfill_cell_evidence.tsv"
            if "alignment_backfill_cell_evidence.tsv" in artifacts
            else None
        ),
        status_matrix_tsv=(
            output_dir / "alignment_matrix_status.tsv"
            if "alignment_matrix_status.tsv" in artifacts
            else None
        ),
        run_metadata_json=(
            output_dir / "alignment_run_metadata.json"
            if emit_target_snapshot_metadata or "alignment_review.tsv" in artifacts
            else None
        ),
        rt_anchors_tsv=(
            output_dir / "alignment_rt_anchors.tsv"
            if "alignment_rt_anchors.tsv" in artifacts
            else None
        ),
    )


def alignment_metadata(
    *,
    discovery_batch_index: Path,
    raw_dir: Path,
    dll_dir: Path,
    output_level: AlignmentOutputLevel,
    resolver_mode: str,
    signal_processing_profile: SignalProcessingProfile = (
        DEFAULT_SIGNAL_PROCESSING_PROFILE
    ),
) -> dict[str, str]:
    if not isinstance(signal_processing_profile, SignalProcessingProfile):
        raise TypeError("signal_processing_profile must be a SignalProcessingProfile")
    metadata = {
        "discovery_batch_index": str(discovery_batch_index),
        "raw_dir": str(raw_dir),
        "dll_dir": str(dll_dir),
        "output_level": output_level,
        "resolver_mode": resolver_mode,
        "baseline_integration_method": "asls",
        "signal_processing_profile_json": serialize_signal_processing_profile(
            signal_processing_profile
        ).decode("utf-8"),
    }
    return metadata


def row_output_paths(outputs: AlignmentRunOutputs) -> dict[str, Path]:
    """The requested TSVs by the names their projected rows are stored under."""
    return {
        name: path
        for name, path in (
            ("matrix", outputs.matrix_tsv),
            ("review", outputs.review_tsv),
            ("backfill", outputs.backfill_cell_evidence_tsv),
            ("cells", outputs.cells_tsv),
            ("status", outputs.status_matrix_tsv),
        )
        if path is not None
    }


def write_completed_untargeted_outputs_atomic(
    outputs: AlignmentRunOutputs,
    completed: FinalizedUntargetBatchApplicationResult | None,
    *,
    metadata: dict[str, str],
    rt_calibration: BatchRtCalibration | None,
    timing_recorder: TimingRecorder,
    consume_child: Callable[[UntargetChildOutputs, int], None] | None = None,
    before_write: Callable[[], None] | None = None,
    render_rows: Callable[[Mapping[str, Path]], int] | None = None,
    extra_tables: Mapping[Path, Callable[[Path], None]] | None = None,
) -> int:
    """Render every requested TSV in one pass, then publish them atomically.

    ``render_rows`` replaces rendering ``completed``'s rows (several
    independently aligned batches); ``extra_tables`` are published with them.
    """
    if before_write is not None:
        before_write()
    row_paths = row_output_paths(outputs)
    paths = list(row_paths.values())
    if outputs.run_metadata_json is not None:
        paths.append(outputs.run_metadata_json)
    if outputs.rt_anchors_tsv is not None:
        paths.append(outputs.rt_anchors_tsv)
    paths.extend(extra_tables or {})
    recorder = (
        timing_recorder
        if timing_recorder.instrument_writers
        else TimingRecorder.disabled("writer")
    )
    with (
        timing_recorder.stage("alignment.write_outputs"),
        _staged_alignment_artifacts(paths, recorder) as temp_paths,
    ):
        row_temp_paths = {name: temp_paths[path] for name, path in row_paths.items()}
        if render_rows is not None:
            row_count = render_rows(row_temp_paths)
        else:
            assert completed is not None
            row_count = write_alignment_tsvs(
                row_temp_paths, completed, consume_child=consume_child
            )
        for path, write_table in (extra_tables or {}).items():
            write_table(temp_paths[path])
        if outputs.run_metadata_json is not None:
            _write_metadata_json(temp_paths[outputs.run_metadata_json], metadata)
        if outputs.rt_anchors_tsv is not None:
            write_rt_anchor_table(temp_paths[outputs.rt_anchors_tsv], rt_calibration)
    return row_count


def alignment_artifact_collisions(paths: Iterable[Path]) -> list[Path]:
    """Existing final, staging or backup paths owned by this publication."""
    return [
        candidate
        for path in paths
        for candidate in (path, _temp_path(path), _backup_path(path))
        if candidate.exists()
    ]


@contextmanager
def new_alignment_artifacts(*paths: Path) -> Iterator[Mapping[Path, Path]]:
    """Publish a replay's outputs together, without replacing a baseline."""
    collisions = alignment_artifact_collisions(paths)
    if collisions:
        raise FileExistsError(f"Output already exists: {collisions[0]}")
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
    with _staged_alignment_artifacts(
        paths, TimingRecorder.disabled("writer")
    ) as staged:
        yield staged


@contextmanager
def _staged_alignment_artifacts(
    final_paths: Sequence[Path], recorder: TimingRecorder
) -> Iterator[Mapping[Path, Path]]:
    """Stage the whole artifact set and roll back failed publication."""
    temp_paths = {path: _temp_path(path) for path in final_paths}
    backup_paths = [_backup_path(final_path) for final_path in final_paths]
    backups: list[tuple[Path, Path]] = []
    replaced_paths: list[Path] = []
    try:
        with recorder.stage("writer.streamed_rows.render"):
            yield temp_paths
        for final_path, temp_path in temp_paths.items():
            artifact = _writer_artifact_name(final_path)
            rendered_bytes = temp_path.stat().st_size if temp_path.exists() else None
            recorder.observe(
                "writer_render_time_sec",
                value=None,
                scope=artifact,
                unit="seconds",
                source="alignment.pipeline_outputs",
                availability="unavailable",
                reason=(
                    "one shared streamed render is measured by "
                    "writer.streamed_rows.render"
                ),
            )
            recorder.observe(
                "writer_render_bytes",
                value=rendered_bytes,
                scope=artifact,
                unit="bytes",
                source="alignment.pipeline_outputs",
                availability="partial",
                reason=("render bytes include the renderer's temp-file write"),
            )
            recorder.observe(
                "writer_temp_write_time_sec",
                value=None,
                scope=artifact,
                unit="seconds",
                source="alignment.pipeline_outputs",
                availability="unavailable",
                reason=(
                    "renderer combines serialization and temp-file write; "
                    "phase cannot be separated without changing writer behavior"
                ),
            )
            recorder.observe(
                "writer_temp_write_bytes",
                value=None,
                scope=artifact,
                unit="bytes",
                source="alignment.pipeline_outputs",
                availability="unavailable",
                reason=(
                    "renderer combines serialization and temp-file write; "
                    "phase cannot be separated without changing writer behavior"
                ),
            )
        for final_path in final_paths:
            backup_path = _backup_path(final_path)
            backup_path.unlink(missing_ok=True)
            if final_path.exists():
                final_path.replace(backup_path)
                backups.append((final_path, backup_path))
        for final_path, temp_path in temp_paths.items():
            artifact = _writer_artifact_name(final_path)
            with recorder.stage(f"writer.{artifact}.rename") as stage:
                temp_path.replace(final_path)
                renamed_bytes = (
                    final_path.stat().st_size if final_path.exists() else None
                )
                stage.metrics["bytes"] = renamed_bytes
            recorder.observe(
                "writer_rename_time_sec",
                value=stage.elapsed_sec,
                scope=artifact,
                unit="seconds",
                source="alignment.pipeline_outputs",
            )
            recorder.observe(
                "writer_rename_bytes",
                value=renamed_bytes,
                scope=artifact,
                unit="bytes",
                source="alignment.pipeline_outputs",
            )
            replaced_paths.append(final_path)
        for _final_path, backup_path in backups:
            with suppress(OSError):
                backup_path.unlink(missing_ok=True)
    except Exception:
        for final_path in replaced_paths:
            final_path.unlink(missing_ok=True)
        for final_path, backup_path in reversed(backups):
            if backup_path.exists():
                final_path.unlink(missing_ok=True)
                backup_path.replace(final_path)
        for temp_path in temp_paths.values():
            temp_path.unlink(missing_ok=True)
        for backup_path in backup_paths:
            backup_path.unlink(missing_ok=True)
        raise


def _temp_path(final_path: Path) -> Path:
    return final_path.with_name(f"{final_path.name}.tmp")


def _write_metadata_json(path: Path, metadata: dict[str, str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _backup_path(final_path: Path) -> Path:
    return final_path.with_name(f"{final_path.name}.bak")


def _writer_artifact_name(path: Path) -> str:
    """Return a stable diagnostic name without exposing absolute paths."""

    name = path.name
    if name.endswith(".tsv"):
        name = name[:-4]
    elif name.endswith(".json"):
        name = name[:-5]
    elif name.endswith(".xlsx"):
        name = name[:-5]
    elif name.endswith(".html"):
        name = name[:-5]
    return name.replace(".", "_").replace("-", "_")

"""Replay the Matrix writer from finalized cell values and a frozen batch join."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

from xic_extractor.alignment.batch_correspondence import BatchCorrespondence
from xic_extractor.diagnostics.stage_snapshot import load_snapshot, save_snapshot
from xic_extractor.output.alignment.pipeline_outputs import new_alignment_artifacts
from xic_extractor.output.alignment.tsv_writer import (
    BatchRowSource,
    UntargetChildOutputs,
    UntargetGroupRows,
    feature_key_by_peaks,
    project_untarget_child_rows,
    untarget_sample_order,
    write_batch_alignment_tsvs,
    write_projected_alignment_tsvs,
)
from xic_extractor.sample_metadata import SampleMetadata

if TYPE_CHECKING:
    from xic_extractor.untargeted_execution.partition_composition import (
        UntargetedPartitionComposition,
    )


class CompletedMatrixCells:
    """Collect Matrix projections in child order when replaying Backfill."""

    def __init__(self, samples: dict[str, dict[str, SampleMetadata]]) -> None:
        self.samples = samples
        self.children: dict[
            str,
            list[
                tuple[
                    str,
                    tuple[UntargetGroupRows, ...],
                    Callable[[UntargetGroupRows], str | None],
                ]
            ],
        ] = {batch_id: [] for batch_id in samples}

    def append(
        self,
        composition: UntargetedPartitionComposition,
        samples: tuple[SampleMetadata, ...],
    ) -> None:
        order = untarget_sample_order(samples)
        batch_id = samples[0].batch_id
        key = (
            feature_key_by_peaks(composition.initial_groups)
            if batch_id
            else _no_feature_key
        )
        for child in composition.children:
            groups = project_untarget_child_rows(
                child, outputs=("matrix",), sample_order=order
            )
            self.children[batch_id].append((child.first_method_subject_id, groups, key))

    def write(self, path: Path) -> None:
        save_snapshot(
            path,
            tuple(
                _matrix_source(
                    batch_id,
                    tuple(self.samples[batch_id].values()),
                    (
                        (groups, key)
                        for _, groups, key in sorted(children, key=lambda c: c[0])
                    ),
                )
                for batch_id, children in self.children.items()
            ),
        )


def _no_feature_key(_group: UntargetGroupRows) -> None:
    return None


def _matrix_source(
    batch_id: str,
    samples: Sequence[SampleMetadata],
    children: Iterable[
        tuple[Sequence[UntargetGroupRows], Callable[[UntargetGroupRows], str | None]]
    ],
) -> BatchRowSource:
    """The batch's Matrix rows and their correspondence features, nothing else."""
    keys: dict[UntargetGroupRows, str | None] = {}
    for groups, feature_key in children:
        for group in groups:
            if group.matrix is not None:
                keys[replace(group, rows=())] = feature_key(group)
    return BatchRowSource(
        batch_id,
        tuple(samples),
        (UntargetChildOutputs(tuple(keys)),),
        keys.__getitem__,
    )


def save_matrix_cells(path: Path, sources: Sequence[BatchRowSource]) -> None:
    """Retain the same finalized row projections used by the formal writer."""
    save_snapshot(
        path,
        tuple(
            _matrix_source(
                source.batch_id,
                source.canonical_samples,
                (
                    (cast(UntargetChildOutputs, child).groups, source.feature_key)
                    for child in source.child_outputs
                ),
            )
            for source in sources
        ),
    )


def save_matrix_join(path: Path, result: BatchCorrespondence) -> None:
    save_snapshot(path, result)


def replay_matrix(
    backfill: Sequence[Path], pairing: Path | None, output_dir: Path
) -> Path:
    output = output_dir / "alignment_matrix.tsv"
    sources: list[BatchRowSource] = [
        source for path in backfill for source in load_snapshot(path)
    ]
    correspondence = None
    if pairing is None:
        if len(sources) != 1:
            raise ValueError("Multiple Matrix batches require --pairing")
    else:
        correspondence = load_snapshot(pairing)
        _require_paired_features(sources, correspondence)
    with new_alignment_artifacts(output) as staged:
        if correspondence is None:
            (source,) = sources
            write_projected_alignment_tsvs(
                {"matrix": staged[output]},
                source.canonical_samples,
                source.child_outputs,
            )
        else:
            reference = correspondence.reference_batch
            write_batch_alignment_tsvs(
                {"matrix": staged[output]},
                sorted(
                    sources,
                    key=lambda source: (source.batch_id != reference, source.batch_id),
                ),
                reference_batch=reference,
                partners={
                    (item.batch_id, item.key): item.partners
                    for item in correspondence.features
                },
                states={
                    (item.batch_id, item.key): item.state
                    for item in correspondence.features
                },
                to_reference_rt=correspondence.to_reference_rt,
            )
    return output


def _require_paired_features(
    sources: Sequence[BatchRowSource], correspondence: BatchCorrespondence
) -> None:
    batch_ids = {source.batch_id for source in sources}
    features = {
        (source.batch_id, key)
        for source in sources
        for child in source.child_outputs
        for group in cast(UntargetChildOutputs, child).groups
        if (key := source.feature_key(group)) is not None
    }
    joined = {member for row in correspondence.rows() for member in row}
    if (
        len(batch_ids) != len(sources)
        or batch_ids
        != {correspondence.reference_batch, *(m.batch_id for m in correspondence.maps)}
        or features != joined
    ):
        raise ValueError(
            "Backfill batches or feature identities differ from the pairing result"
        )

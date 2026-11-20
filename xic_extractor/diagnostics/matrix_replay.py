"""Replay the Matrix writer from finalized cell values and a frozen batch join."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any

from xic_extractor.alignment.batch_correspondence import BatchCorrespondence
from xic_extractor.diagnostics.grouping_experiment_io import write_json
from xic_extractor.output.alignment.tsv_writer import (
    BatchRowSource,
    FinalMatrixValues,
    UntargetChildOutputs,
    UntargetGroupRows,
    feature_key_by_peaks,
    project_untarget_child_rows,
    untarget_sample_order,
    write_batch_alignment_tsvs,
)
from xic_extractor.rt_calibration_codec import (
    sample_calibration_from_payload,
    sample_calibration_payload,
)
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.validation_primitives import (
    require_finite_real,
    require_integer,
    require_list,
    require_mapping,
    require_text,
)

if TYPE_CHECKING:
    from xic_extractor.untargeted_execution.partition_composition import (
        UntargetedPartitionComposition,
    )

_BACKFILL = "finalized_matrix_cells"
_PAIRING = "matrix_batch_join"


class CompletedMatrixCells:
    """Keep only the Matrix writer's inputs, never RAW or a rendered Matrix."""

    def __init__(self, samples: dict[str, dict[str, int | None]]) -> None:
        if "" in samples:
            raise ValueError("Matrix replay requires declared batch IDs")
        self.batches: dict[str, dict[str, Any]] = {
            batch: {"samples": roster, "children": []}
            for batch, roster in samples.items()
        }

    def append(
        self,
        composition: UntargetedPartitionComposition,
        samples: tuple[SampleMetadata, ...],
    ) -> None:
        order = untarget_sample_order(samples)
        batch = self.batches[samples[0].batch_id]
        key = feature_key_by_peaks(composition.initial_groups)
        for child in composition.children:
            rows: list[dict[str, Any]] = []

            def capture(
                group: UntargetGroupRows,
                values: FinalMatrixValues,
                rows: list[dict[str, Any]] = rows,
            ) -> None:
                rows.append(
                    {
                        "key": key(group),
                        "mz": values.mz,
                        "rt": values.rt,
                        "areas": dict(zip(order, values.areas, strict=True)),
                        "physical_peaks": group.physical_peaks,
                        "source_candidate_ids": group.source_candidate_ids,
                    }
                )

            project_untarget_child_rows(
                child, outputs=(), sample_order=order, capture_matrix=capture
            )
            batch["children"].append((child.first_method_subject_id, rows))

    def write(self, path: Path) -> None:
        write_json(
            path,
            {
                "schema": _BACKFILL,
                "batches": {
                    batch_id: {
                        "samples": batch["samples"],
                        "groups": [
                            group
                            for _, rows in sorted(
                                batch["children"], key=lambda child: child[0]
                            )
                            for group in rows
                        ],
                    }
                    for batch_id, batch in self.batches.items()
                },
            },
        )


def save_matrix_join(path: Path, result: BatchCorrespondence) -> None:
    write_json(
        path,
        {
            "schema": _PAIRING,
            "reference_batch": result.reference_batch,
            "rows": result.rows(),
            "maps": {
                m.batch_id: None
                if m.calibration is None
                else sample_calibration_payload(m.calibration)
                for m in result.maps
            },
        },
    )


def _document(path: Path, schema: str) -> dict[str, Any]:
    doc = require_mapping(json.loads(path.read_text(encoding="utf-8")))
    if doc.get("schema") != schema:
        raise ValueError(f"{path}: expected {schema}")
    return doc


def _batch_source(
    batch_id: str, batch: dict, features: set[tuple[str, str]]
) -> BatchRowSource:
    samples = tuple(
        SampleMetadata(stem, stem, require_integer(n, positive=True), batch_id=batch_id)
        for stem, n in require_mapping(batch["samples"]).items()
    )
    order = untarget_sample_order(samples)
    keys: dict[UntargetGroupRows, str | None] = {}
    for item in require_list(batch["groups"]):
        row = require_mapping(item)
        areas = require_mapping(row["areas"])
        if set(areas) != set(order):
            raise ValueError(f"Matrix areas crossed the sample roster for {batch_id}")
        values = FinalMatrixValues(
            require_finite_real(row["mz"], positive=True),
            require_finite_real(row["rt"]),
            tuple(
                None if areas[s] is None else require_finite_real(areas[s])
                for s in order
            ),
        )
        group = UntargetGroupRows(
            "matrix",
            tuple(_two_strings(v) for v in require_list(row["physical_peaks"])),
            tuple(require_text(v) for v in require_list(row["source_candidate_ids"])),
            (("matrix", (values.row(order),)),),
        )
        if group in keys:
            raise ValueError(f"Repeated Backfill row in {batch_id}")
        key = None if row["key"] is None else require_text(row["key"])
        keys[group] = key
        if key is not None:
            member = (batch_id, key)
            if member in features:
                raise ValueError(f"Repeated Backfill feature {member}")
            features.add(member)
    return BatchRowSource(
        batch_id, samples, (UntargetChildOutputs(tuple(keys)),), keys.__getitem__
    )


def _two_strings(value: object) -> tuple[str, str]:
    a, b = require_list(value)
    return require_text(a), require_text(b)


def replay_matrix(backfill: Sequence[Path], pairing: Path, output_dir: Path) -> Path:
    output = output_dir / "alignment_matrix.tsv"
    if output.exists():
        raise FileExistsError(output)
    try:
        join = _document(pairing, _PAIRING)
        reference = require_text(join["reference_batch"])
        maps = {
            name: None
            if obj is None
            else sample_calibration_from_payload(require_mapping(obj))
            for name, obj in require_mapping(join["maps"]).items()
        }
        partners = {}
        joined: set[tuple[str, str]] = set()
        for row in require_list(join["rows"]):
            first, *rest = [_two_strings(m) for m in require_list(row)]
            members = (first, *rest)
            if (
                (rest and first[0] != reference)
                or len({m[0] for m in members}) != len(members)
                or joined.intersection(members)
            ):
                raise ValueError("Invalid or repeated feature in batch join")
            joined.update(members)
            partners[first] = tuple(rest)

        sources: list[BatchRowSource] = []
        features: set[tuple[str, str]] = set()
        for path in backfill:
            batches = require_mapping(_document(path, _BACKFILL)["batches"])
            sources.extend(
                _batch_source(batch_id, require_mapping(batch), features)
                for batch_id, batch in batches.items()
            )
        batch_ids = {s.batch_id for s in sources}
        if (
            len(batch_ids) != len(sources)
            or batch_ids != {reference, *maps}
            or features != joined
        ):
            raise ValueError(
                "Backfill batches or feature identities differ from the pairing result"
            )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid Matrix inputs: {exc}") from exc

    def to_reference(batch_id: str, rt: float) -> float | None:
        model = maps[batch_id]
        estimate = model.estimate_rt(rt) if model is not None else None
        return estimate.rt_min if estimate is not None else None

    output_dir.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output_dir) as tmp:
        staged = Path(tmp) / output.name
        write_batch_alignment_tsvs(
            {"matrix": staged},
            sources,
            reference_batch=reference,
            partners=partners,
            states={},
            to_reference_rt=to_reference,
        )
        staged.replace(output)
    return output

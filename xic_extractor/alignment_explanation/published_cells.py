"""Read published Matrix cells and their retained per-sample evidence."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .reader import open_explanation_run, query_row_cell_values
from .storage import require_matrix_binding

if TYPE_CHECKING:
    from xic_extractor.output.alignment.matrix_output_contract import MatrixRowIdentity


@dataclass(frozen=True)
class PublishedCell:
    row: int
    row_mz: float
    sample: str
    origin: str
    apex_rt_min: float | None
    feature_id: str = ""
    value: float | None = None


def read_matrix_evidence(
    explanation: Path,
    matrix_digest: str,
    identities: Sequence[MatrixRowIdentity] = (),
    *,
    expected_attempt_id: str | None,
) -> tuple[dict[str, Any], tuple[PublishedCell, ...]]:
    """Read bound run metadata and numeric cells for the requested Matrix rows."""
    with open_explanation_run(explanation) as (connection, summary):
        require_matrix_binding(
            summary, matrix_digest, expected_attempt_id=expected_attempt_id
        )
        if not identities:
            return summary, ()
        rows = {key: (index, mz) for index, (key, mz) in enumerate(identities, 1)}
        samples = {s["sample_key"]: s["sample_stem"] for s in summary["samples"]}
        cells = tuple(
            PublishedCell(
                row=rows[group][0],
                row_mz=rows[group][1],
                sample=samples[sample_key],
                origin=origin,
                apex_rt_min=apex,
                feature_id=group,
                value=float(value),
            )
            for group, sample_key, value, origin, apex in query_row_cell_values(
                connection, rows
            )
            if value is not None
        )
        return summary, cells

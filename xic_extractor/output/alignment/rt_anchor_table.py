"""Write the frozen RT calibration knots as one anchor row each."""

from __future__ import annotations

from pathlib import Path

from xic_extractor.alignment.sample_rt_calibration import BatchRtCalibration
from xic_extractor.tabular_io import write_tsv

RT_ANCHOR_COLUMNS = (
    "knot_id",
    "cohort",
    "kind",
    "istd_label",
    "precursor_mz",
    "neutral_loss_da",
    "reference_rt_min",
    "sample_count",
)


def write_rt_anchor_table(path: Path, calibration: BatchRtCalibration | None) -> None:
    """One row per knot; a multi-channel knot lists its channels in order.

    Without a calibration the table has only its header.
    """
    write_tsv(
        path,
        (
            {
                "knot_id": knot.knot_id,
                "cohort": knot.cohort,
                "kind": "ordinary" if knot.istd_label is None else "istd",
                "istd_label": knot.istd_label or "",
                "precursor_mz": ";".join(str(mz) for mz, _ in knot.channels),
                "neutral_loss_da": ";".join(
                    "" if loss is None else str(loss) for _, loss in knot.channels
                ),
                "reference_rt_min": knot.reference_rt_min,
                "sample_count": knot.sample_count,
            }
            for knot in (calibration.knots if calibration is not None else ())
        ),
        RT_ANCHOR_COLUMNS,
        formatter=str,
        lineterminator="\n",
    )

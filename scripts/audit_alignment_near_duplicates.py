from __future__ import annotations

import argparse
import csv
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.alignment.near_duplicate_audit import (
    AlignmentNearDuplicateInput,
    count_near_duplicate_pairs,
)

_MATRIX_METADATA_COLUMNS = frozenset(
    {
        "feature_family_id",
        "cluster_id",
        "neutral_loss_tag",
        "family_center_mz",
        "cluster_center_mz",
        "family_center_rt",
        "cluster_center_rt",
    }
)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    rows = _load_rows(args.review_tsv, args.matrix_tsv)
    summary = count_near_duplicate_pairs(
        rows,
        mz_ppm=args.mz_ppm,
        rt_sec=args.rt_sec,
        product_ppm=args.product_ppm,
        observed_loss_ppm=args.observed_loss_ppm,
        min_shared_samples=args.min_shared_samples,
        min_overlap=args.min_overlap,
    )
    print(f"near_pair_count={summary.near_pair_count}")
    print(f"high_shared_pair_count={summary.high_shared_pair_count}")
    for pair in summary.top_pairs:
        print(
            f"{pair.left_id}\t{pair.right_id}\t"
            f"shared={pair.shared_count}\t"
            f"overlap={pair.overlap_coefficient:.3f}\t"
            f"jaccard={pair.jaccard:.3f}\t"
            f"mz_ppm={pair.mz_ppm:.3g}\t"
            f"rt_sec={pair.rt_sec:.3g}"
        )
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit unresolved near-duplicate alignment rows.",
    )
    parser.add_argument("--review-tsv", type=Path, required=True)
    parser.add_argument("--matrix-tsv", type=Path, required=True)
    parser.add_argument("--mz-ppm", type=float, default=5.0)
    parser.add_argument("--rt-sec", type=float, default=2.0)
    parser.add_argument("--product-ppm", type=float, default=10.0)
    parser.add_argument("--observed-loss-ppm", type=float, default=10.0)
    parser.add_argument("--min-shared-samples", type=int, default=30)
    parser.add_argument("--min-overlap", type=float, default=0.8)
    return parser.parse_args(argv)


def _load_rows(
    review_tsv: Path,
    matrix_tsv: Path,
) -> tuple[AlignmentNearDuplicateInput, ...]:
    with matrix_tsv.open(newline="", encoding="utf-8") as handle:
        matrix_rows = {
            row[_first_present(row, ("feature_family_id", "cluster_id"))]: row
            for row in csv.DictReader(handle, delimiter="\t")
        }
    with review_tsv.open(newline="", encoding="utf-8") as handle:
        review_rows = list(csv.DictReader(handle, delimiter="\t"))

    output: list[AlignmentNearDuplicateInput] = []
    for row in review_rows:
        row_id = row[_first_present(row, ("feature_family_id", "cluster_id"))]
        matrix_row = matrix_rows[row_id]
        present_samples = frozenset(
            key
            for key, value in matrix_row.items()
            if key not in _MATRIX_METADATA_COLUMNS
            and value is not None
            and value != ""
        )
        output.append(
            AlignmentNearDuplicateInput(
                row_id=row_id,
                neutral_loss_tag=row["neutral_loss_tag"],
                mz=float(row.get("family_center_mz") or row["cluster_center_mz"]),
                rt=float(row.get("family_center_rt") or row["cluster_center_rt"]),
                product_mz=float(
                    row.get("family_product_mz") or row["cluster_product_mz"]
                ),
                observed_neutral_loss_da=float(
                    row.get("family_observed_neutral_loss_da")
                    or row["cluster_observed_neutral_loss_da"]
                ),
                present_samples=present_samples,
            ),
        )
    return tuple(output)


def _first_present(row: dict[str, str], names: tuple[str, ...]) -> str:
    for name in names:
        if name in row:
            return name
    raise ValueError(f"missing any of required columns: {', '.join(names)}")


if __name__ == "__main__":
    raise SystemExit(main())

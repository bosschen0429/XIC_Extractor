"""
scripts/collapse_same_compound_rows.py

在 Untargeted Alignment 產出矩陣之後，把同一個化合物重複出現的列收斂成一列，
輸出新的矩陣供下游分析；原本的輸出檔不會被修改。

Usage:
    python scripts/collapse_same_compound_rows.py <alignment_dir>
        --targets <targets.csv> --output-dir <dir>

<alignment_dir> 需要 validation-minimal 層級的輸出：
    alignment_matrix.tsv、alignment_matrix_identity.tsv、alignment_review.tsv、
    alignment_backfill_cell_evidence.tsv
<targets.csv> 的 is_istd=TRUE 列會被保護，永遠保留。

兩層規則（強度比只用兩列都直接偵測到的樣本，至少 5 個）：
    1. M+1 同位素：比另一列多一個 13C 或 15N、RT 相近、
       母峰至少強 2 倍且比值穩定 → 刪除。
    2. 同一化合物的離子：Na+/K+/NH4+ 加成、[2M+H]+、M+2、源內丟 dR 或 H2O，
       同時流出且比值穩定的列連成一群，每群只留 [M+H]+。
       [M+H]+ 是群內能由質量推出最多其他成員（加成、二聚體、M+2）的那一列。

輸出（<output-dir>）：
    alignment_matrix.same_compound_collapsed.tsv
        保留的列，逐字複製
    alignment_matrix.same_compound_collapsed.detection_mask.tsv
        每格 D（偵測）或 B（回填）
    same_compound_removed_rows.tsv
        每一列刪除的原因與保留的代表列
    same_compound_review.tsv
        建議人工確認的列
"""

from __future__ import annotations

import argparse
import bisect
import csv
import math
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

PROTON = 1.007276
C13_SHIFT = 1.003355
N15_SHIFT = 0.997035
MATCH_PPM = 5.0
ISTD_PPM = 10.0
MIN_SHARED_DETECTED = 5

ISOTOPE_RT_MIN = 0.3
ISOTOPE_MIN_PARENT_RATIO = 2.0
ISOTOPE_MAX_RATIO_MAD = 0.1

ION_RT_MIN = 0.1
ION_MAX_RATIO_MAD = 0.15
WEAK_MH_REVIEW_FOLD = 10.0

# name, heavier m/z minus lighter m/z, whether the heavier row derives from the lighter
ION_RELATIONS: tuple[tuple[str, float, bool], ...] = (
    ("[M+Na]+", 21.981944, True),
    ("[M+K]+", 37.955882, True),
    ("[M+NH4]+", 17.026549, True),
    ("M+2", 2.006710, True),
    ("in-source loss of dR", 116.047344, False),
    ("in-source loss of H2O", 18.010565, False),
)
DIMER = "[2M+H]+"
MATRIX_NAME = "alignment_matrix.tsv"
IDENTITY_NAME = "alignment_matrix_identity.tsv"
REVIEW_NAME = "alignment_review.tsv"
CELL_EVIDENCE_NAME = "alignment_backfill_cell_evidence.tsv"


class InputError(ValueError):
    """The alignment output cannot support this collapse."""


@dataclass(frozen=True)
class FeatureRow:
    index: int
    mz: float
    rt: float
    line: str
    marks: tuple[str, ...]
    detected: dict[str, float]

    @property
    def median_detected(self) -> float:
        return statistics.median(self.detected.values()) if self.detected else 0.0


@dataclass(frozen=True)
class Removal:
    row: FeatureRow
    stage: str
    reason: str
    kept: FeatureRow
    group: int | None
    parent_over_row: float | None
    ratio_mad: float | None


@dataclass(frozen=True)
class ReviewItem:
    kind: str
    row: FeatureRow
    related: FeatureRow
    note: str


def _ppm(a: float, b: float) -> float:
    return abs(a - b) / b * 1e6


def _ratio_stats(
    parent: FeatureRow, row: FeatureRow
) -> tuple[int, float, float] | None:
    shared = [s for s in parent.detected if s in row.detected and row.detected[s] > 0]
    if len(shared) < MIN_SHARED_DETECTED:
        return None
    logs = [math.log10(parent.detected[s] / row.detected[s]) for s in shared]
    center = statistics.median(logs)
    mad = statistics.median(abs(value - center) for value in logs)
    return len(shared), 10**center, mad


class _MzIndex:
    def __init__(self, rows: list[FeatureRow]) -> None:
        self.rows = sorted(rows, key=lambda row: row.mz)
        self.mzs = [row.mz for row in self.rows]

    def near(self, mz: float, rt: float, rt_window: float) -> list[FeatureRow]:
        tolerance = mz * MATCH_PPM * 1e-6
        lo = bisect.bisect_left(self.mzs, mz - tolerance)
        hi = bisect.bisect_right(self.mzs, mz + tolerance)
        return [row for row in self.rows[lo:hi] if abs(row.rt - rt) <= rt_window]


def _is_istd(row: FeatureRow, istd_mzs: tuple[float, ...]) -> bool:
    return any(_ppm(row.mz, mz) <= ISTD_PPM for mz in istd_mzs)


def find_isotope_shadows(
    rows: list[FeatureRow], istd_mzs: tuple[float, ...]
) -> tuple[list[Removal], list[ReviewItem]]:
    """Rows that are the M+1 (13C or 15N) of a stronger co-eluting row."""
    index = _MzIndex(rows)
    removals: list[Removal] = []
    review: list[ReviewItem] = []
    for row in rows:
        if _is_istd(row, istd_mzs):
            continue
        best: tuple[str, FeatureRow, float, float] | None = None
        for label, shift in (("13C", C13_SHIFT), ("15N", N15_SHIFT)):
            for parent in index.near(row.mz - shift, row.rt, ISOTOPE_RT_MIN):
                if parent is row:
                    continue
                stats = _ratio_stats(parent, row)
                if stats is None:
                    continue
                _, ratio, mad = stats
                if ratio >= ISOTOPE_MIN_PARENT_RATIO and (
                    best is None or ratio > best[2]
                ):
                    best = (label, parent, ratio, mad)
        if best is None:
            continue
        label, parent, ratio, mad = best
        if mad <= ISOTOPE_MAX_RATIO_MAD:
            removals.append(
                Removal(row, "isotope", f"{label} M+1", parent, None, ratio, mad)
            )
        else:
            review.append(
                ReviewItem(
                    "unstable isotope ratio",
                    row,
                    parent,
                    f"{label} M+1 candidate kept: parent/row={ratio:.3g}, "
                    f"ratio MAD(log10)={mad:.3f}",
                )
            )
    return removals, review


def _derives(light: FeatureRow, heavy: FeatureRow) -> bool:
    """True when heavy is an adduct, dimer or M+2 of light by mass."""
    for _, shift, derived in ION_RELATIONS:
        if derived and _ppm(heavy.mz, light.mz + shift) <= MATCH_PPM:
            return True
    return _ppm(heavy.mz, 2 * (light.mz - PROTON) + PROTON) <= MATCH_PPM


def collapse_ion_groups(
    rows: list[FeatureRow], istd_mzs: tuple[float, ...]
) -> tuple[list[Removal], list[ReviewItem]]:
    """Keep one [M+H]+ row per group of co-eluting ions of the same compound."""
    index = _MzIndex(rows)
    edges: dict[int, set[int]] = defaultdict(set)
    by_index = {row.index: row for row in rows}
    relations = [(name, shift) for name, shift, _ in ION_RELATIONS] + [(DIMER, None)]
    for light in rows:
        for name, shift in relations:
            heavy_mz = (
                2 * (light.mz - PROTON) + PROTON if shift is None else light.mz + shift
            )
            for heavy in index.near(heavy_mz, light.rt, ION_RT_MIN):
                stats = _ratio_stats(light, heavy)
                if heavy is light or stats is None or stats[2] > ION_MAX_RATIO_MAD:
                    continue
                edges[light.index].add(heavy.index)
                edges[heavy.index].add(light.index)

    removals: list[Removal] = []
    review: list[ReviewItem] = []
    seen: set[int] = set()
    group_number = 0
    for start in sorted(edges):
        if start in seen:
            continue
        group_number += 1
        stack, members = [start], []
        seen.add(start)
        while stack:
            current = stack.pop()
            members.append(by_index[current])
            for neighbour in edges[current]:
                if neighbour not in seen:
                    seen.add(neighbour)
                    stack.append(neighbour)
        derived = {
            m.index
            for m in members
            if any(_derives(o, m) for o in members if o is not m)
        }
        roots = [m for m in members if m.index not in derived] or members
        keep = max(
            roots,
            key=lambda m: (
                sum(_derives(m, o) for o in members if o is not m),
                m.median_detected,
                len(m.detected),
            ),
        )
        strongest = max(members, key=lambda m: m.median_detected)
        fold = strongest.median_detected / max(keep.median_detected, 1e-12)
        if (
            strongest is not keep
            and strongest.median_detected > WEAK_MH_REVIEW_FOLD * keep.median_detected
        ):
            review.append(
                ReviewItem(
                    "weak [M+H]+",
                    keep,
                    strongest,
                    f"kept [M+H]+ is {fold:.3g}x weaker than a removed ion "
                    "of the same compound",
                )
            )
        for member in sorted(members, key=lambda m: m.index):
            if member is keep or _is_istd(member, istd_mzs):
                continue
            role = (
                "adduct, dimer or M+2"
                if member.index in derived
                else "co-eluting related ion"
            )
            removals.append(
                Removal(member, "ion group", role, keep, group_number, None, None)
            )
    return removals, review


def load_alignment_rows(alignment_dir: Path) -> tuple[str, str, list[FeatureRow]]:
    """Read Matrix rows and mark each value as directly detected (D) or Backfill (B)."""
    missing = [
        n
        for n in (MATRIX_NAME, IDENTITY_NAME, REVIEW_NAME, CELL_EVIDENCE_NAME)
        if not (alignment_dir / n).is_file()
    ]
    if missing:
        raise InputError(
            f"missing {', '.join(missing)} in {alignment_dir}; "
            "rerun Alignment with --output-level validation-minimal"
        )
    with open(alignment_dir / MATRIX_NAME, encoding="utf-8", newline="") as fh:
        lines = fh.read().splitlines(keepends=True)
    header = lines[0]
    samples = header.rstrip("\r\n").split("\t")[2:]
    with open(alignment_dir / IDENTITY_NAME, encoding="utf-8", newline="") as fh:
        identity = {
            int(r["matrix_row_index"]): r for r in csv.DictReader(fh, delimiter="\t")
        }
    backfilled: dict[str, set[str]] = defaultdict(set)
    with open(alignment_dir / CELL_EVIDENCE_NAME, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            if r["status"] == "backfilled":
                backfilled[r["group_hypothesis_id"]].add(r["sample_stem"])
    with open(alignment_dir / REVIEW_NAME, encoding="utf-8", newline="") as fh:
        review_counts = {
            r["group_hypothesis_id"]: int(r["accepted_backfill_count"] or 0)
            for r in csv.DictReader(fh, delimiter="\t")
        }

    rows: list[FeatureRow] = []
    for index, line in enumerate(lines[1:], start=1):
        cells = line.rstrip("\r\n").split("\t")
        mz, rt = float(cells[0]), float(cells[1])
        ident = identity.get(index)
        # The Matrix prints m/z and RT to four decimals; identity keeps full precision.
        if (
            ident is None
            or abs(float(ident["Mz"]) - mz) > 1e-4
            or abs(float(ident["RT"]) - rt) > 1e-4
        ):
            raise InputError(
                f"identity row {index} does not match Matrix row m/z {mz} RT {rt}"
            )
        group_id = ident["peak_hypothesis_id"]
        if review_counts.get(group_id, 0) != len(backfilled.get(group_id, ())):
            raise InputError(
                f"row {index}: review reports {review_counts.get(group_id, 0)} "
                "accepted Backfill cells but cell evidence lists "
                f"{len(backfilled.get(group_id, ()))}"
            )
        marks, detected = [], {}
        for sample, value in zip(samples, cells[2:]):
            if value in ("", "NA", "nan"):
                marks.append("")
            elif sample in backfilled.get(group_id, ()):
                marks.append("B")
            else:
                marks.append("D")
                detected[sample] = float(value)
        rows.append(FeatureRow(index, mz, rt, line, tuple(marks), detected))
    return header, samples, rows


def read_istd_mzs(targets: Path) -> tuple[float, ...]:
    with open(targets, encoding="utf-8", newline="") as fh:
        return tuple(
            float(r["mz"])
            for r in csv.DictReader(fh)
            if (r.get("is_istd") or "").strip().upper() == "TRUE"
        )


def collapse(
    rows: list[FeatureRow], istd_mzs: tuple[float, ...]
) -> tuple[list[FeatureRow], list[Removal], list[ReviewItem]]:
    isotope_removals, isotope_review = find_isotope_shadows(rows, istd_mzs)
    removed = {r.row.index for r in isotope_removals}
    remaining = [row for row in rows if row.index not in removed]
    ion_removals, ion_review = collapse_ion_groups(remaining, istd_mzs)
    removed |= {r.row.index for r in ion_removals}
    kept = [row for row in rows if row.index not in removed]
    return kept, isotope_removals + ion_removals, isotope_review + ion_review


def write_outputs(
    output_dir: Path,
    header: str,
    samples: list[str],
    kept: list[FeatureRow],
    removals: list[Removal],
    review: list[ReviewItem],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with open(
        output_dir / "alignment_matrix.same_compound_collapsed.tsv",
        "w",
        encoding="utf-8",
        newline="",
    ) as fh:
        fh.write(header)
        fh.writelines(row.line for row in kept)
    with open(
        output_dir / "alignment_matrix.same_compound_collapsed.detection_mask.tsv",
        "w",
        encoding="utf-8",
        newline="",
    ) as fh:
        fh.write(header)
        for row in kept:
            fh.write("\t".join(row.line.split("\t")[:2] + list(row.marks)) + "\n")
    with open(
        output_dir / "same_compound_removed_rows.tsv", "w", encoding="utf-8", newline=""
    ) as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(
            [
                "matrix_row_index",
                "Mz",
                "RT",
                "stage",
                "reason",
                "kept_row_index",
                "kept_Mz",
                "kept_RT",
                "ion_group",
                "parent_over_row",
                "ratio_mad_log10",
            ]
        )
        for r in sorted(removals, key=lambda r: r.row.index):
            writer.writerow(
                [
                    r.row.index,
                    f"{r.row.mz:.4f}",
                    f"{r.row.rt:.2f}",
                    r.stage,
                    r.reason,
                    r.kept.index,
                    f"{r.kept.mz:.4f}",
                    f"{r.kept.rt:.2f}",
                    r.group or "",
                    "" if r.parent_over_row is None else f"{r.parent_over_row:.3f}",
                    "" if r.ratio_mad is None else f"{r.ratio_mad:.3f}",
                ]
            )
    with open(
        output_dir / "same_compound_review.tsv", "w", encoding="utf-8", newline=""
    ) as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(
            [
                "kind",
                "matrix_row_index",
                "Mz",
                "RT",
                "related_row_index",
                "related_Mz",
                "related_RT",
                "note",
            ]
        )
        for item in review:
            writer.writerow(
                [
                    item.kind,
                    item.row.index,
                    f"{item.row.mz:.4f}",
                    f"{item.row.rt:.2f}",
                    item.related.index,
                    f"{item.related.mz:.4f}",
                    f"{item.related.rt:.2f}",
                    item.note,
                ]
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collapse rows of the same compound in an Untargeted Matrix."
    )
    parser.add_argument("alignment_dir", type=Path)
    parser.add_argument(
        "--targets", type=Path, required=True, help="targets CSV; is_istd rows are kept"
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        header, samples, rows = load_alignment_rows(args.alignment_dir)
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    kept, removals, review = collapse(rows, read_istd_mzs(args.targets))
    write_outputs(args.output_dir, header, samples, kept, removals, review)
    isotope = sum(1 for r in removals if r.stage == "isotope")
    print(
        f"rows: {len(rows)} -> {len(kept)} "
        f"(isotope {isotope}, ion group {len(removals) - isotope}); "
        f"review {len(review)}; output {args.output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

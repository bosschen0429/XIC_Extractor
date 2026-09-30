from __future__ import annotations

import csv
from pathlib import Path

from scripts.collapse_same_compound_rows import main

SAMPLES = ("S1", "S2", "S3", "S4", "S5", "S6")
BASE = (1.0e6, 2.1e6, 3.3e6, 4.2e6, 5.6e6, 6.4e6)
ISTD = (5.0e6, 5.1e6, 4.9e6, 5.2e6, 5.05e6, 4.8e6)


def _scaled(values: tuple[float, ...], factor: float) -> list[float]:
    return [value * factor for value in values]


# index: (m/z, RT, per-sample values)
ROWS: dict[int, tuple[float, float, list[float]]] = {
    1: (252.1098, 19.20, _scaled(BASE, 10)),  # [M+H]+ with two M+1 isotope rows
    2: (253.1132, 19.20, _scaled(BASE, 10 / 9.2)),  # 13C M+1
    3: (253.1068, 19.20, _scaled(BASE, 10 / 70)),  # 15N M+1
    4: (253.1132, 30.00, _scaled(BASE, 3)),  # same m/z, other RT: its own feature
    5: (268.1040, 14.34, _scaled(BASE, 4)),  # [M+H]+ weaker than its Na+ adduct
    6: (290.0859, 14.34, _scaled(BASE, 10)),  # [M+Na]+ of row 5
    7: (306.0599, 14.34, _scaled(BASE, 0.3)),  # [M+K]+ of row 5
    8: (384.1513, 14.34, _scaled(BASE, 50)),  # row 5 + dR, strongest ion of the group
    9: (406.1333, 14.34, _scaled(BASE, 5)),  # [M+Na]+ of row 8
    10: (261.1273, 9.00, list(ISTD)),  # ISTD, also the Na+ mass of row 11
    11: (239.1454, 9.00, _scaled(ISTD, 0.5)),
    12: (400.2000, 20.00, _scaled(BASE, 5)),
    13: (
        401.2034,
        20.00,
        [v * 5 / r for v, r in zip(BASE, (3, 8, 2.5, 12, 4, 20))],
    ),  # unstable ratio
}
BACKFILLED = {(1, "S6")}


def _write_run(
    root: Path, *, review_backfill: dict[int, int] | None = None
) -> tuple[Path, Path, list[str]]:
    run = root / "alignment"
    run.mkdir()
    lines = ["Mz\tRT\t" + "\t".join(SAMPLES) + "\n"]
    for index, (mz, rt, values) in ROWS.items():
        lines.append(
            f"{mz:.4f}\t{rt:.4f}\t" + "\t".join(f"{v:.6g}" for v in values) + "\n"
        )
    (run / "alignment_matrix.tsv").write_text(
        "".join(lines), encoding="utf-8", newline=""
    )
    with open(
        run / "alignment_matrix_identity.tsv", "w", encoding="utf-8", newline=""
    ) as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["matrix_row_index", "Mz", "RT", "peak_hypothesis_id"])
        for index, (mz, rt, _) in ROWS.items():
            w.writerow([index, f"{mz:.10f}", f"{rt:.10f}", f"group-{index}"])
    counts = review_backfill or {
        i: sum(1 for (r, _) in BACKFILLED if r == i) for i in ROWS
    }
    with open(run / "alignment_review.tsv", "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["group_hypothesis_id", "accepted_backfill_count"])
        for index in ROWS:
            w.writerow([f"group-{index}", counts.get(index, 0)])
    with open(
        run / "alignment_backfill_cell_evidence.tsv", "w", encoding="utf-8", newline=""
    ) as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["group_hypothesis_id", "sample_stem", "status"])
        for index, sample in sorted(BACKFILLED):
            w.writerow([f"group-{index}", sample, "backfilled"])
    targets = root / "targets.csv"
    targets.write_text(
        "label,mz,is_istd\nd3-ISTD,261.127276,TRUE\nanalyte,258.1085,FALSE\n",
        encoding="utf-8",
    )
    return run, targets, lines


def _read(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def test_collapse_keeps_one_row_per_compound_and_copies_rows_verbatim(
    tmp_path: Path,
) -> None:
    run, targets, lines = _write_run(tmp_path)
    out = tmp_path / "out"

    assert main([str(run), "--targets", str(targets), "--output-dir", str(out)]) == 0

    kept = (
        (out / "alignment_matrix.same_compound_collapsed.tsv")
        .read_text(encoding="utf-8")
        .splitlines(True)
    )
    assert kept == [lines[0]] + [lines[i] for i in (1, 4, 5, 10, 11, 12, 13)]
    removed = {
        int(r["matrix_row_index"]): r
        for r in _read(out / "same_compound_removed_rows.tsv")
    }
    assert {
        i: (r["stage"], r["reason"], r["kept_row_index"]) for i, r in removed.items()
    } == {
        2: ("isotope", "13C M+1", "1"),
        3: ("isotope", "15N M+1", "1"),
        6: ("ion group", "adduct, dimer or M+2", "5"),
        7: ("ion group", "adduct, dimer or M+2", "5"),
        8: ("ion group", "co-eluting related ion", "5"),
        9: ("ion group", "adduct, dimer or M+2", "5"),
    }


def test_collapse_marks_backfill_cells_and_lists_rows_for_review(
    tmp_path: Path,
) -> None:
    run, targets, _ = _write_run(tmp_path)
    out = tmp_path / "out"

    assert main([str(run), "--targets", str(targets), "--output-dir", str(out)]) == 0

    mask = (
        out / "alignment_matrix.same_compound_collapsed.detection_mask.tsv"
    ).read_text(encoding="utf-8")
    first_row = mask.splitlines()[1].split("\t")
    assert first_row[2:] == ["D", "D", "D", "D", "D", "B"]
    review = {
        (r["kind"], r["matrix_row_index"], r["related_row_index"])
        for r in _read(out / "same_compound_review.tsv")
    }
    assert review == {("unstable isotope ratio", "13", "12"), ("weak [M+H]+", "5", "8")}


def test_collapse_requires_validation_minimal_outputs(tmp_path: Path, capsys) -> None:
    run, targets, _ = _write_run(tmp_path)
    (run / "alignment_backfill_cell_evidence.tsv").unlink()

    assert (
        main(
            [str(run), "--targets", str(targets), "--output-dir", str(tmp_path / "out")]
        )
        == 1
    )
    assert "validation-minimal" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_collapse_rejects_backfill_evidence_that_disagrees_with_review(
    tmp_path: Path, capsys
) -> None:
    run, targets, _ = _write_run(tmp_path, review_backfill={1: 0})

    assert (
        main(
            [str(run), "--targets", str(targets), "--output-dir", str(tmp_path / "out")]
        )
        == 1
    )
    assert "accepted Backfill" in capsys.readouterr().err

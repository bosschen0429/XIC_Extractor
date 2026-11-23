"""Count adducts among neutral-loss Discovery candidates of one sample.

Each compound counts once: ions derived from another candidate (known mass
difference and synchronized peak shape) or from a nucleoside are removed, so
are compounds whose every ion leaves a negative mass defect after the loss (no
organic nucleobase); single-MS2 compounds are listed as unconfirmed, and
compounds made only of overlap-review peaks as overlap_only.
Optional replicate controls from the same batch add per-injection and
cumulative counts; their RT is compared as measured. Writes
``nl_adduct_count.json`` and ``nl_adduct_candidates.tsv`` with each
candidate's fate; a derived ion also names the ion it derives from and its
known relation (isotope, adduct, metal complex, dimer). Rules live in
``xic_extractor.diagnostics.nl_adduct_count``. The result screens Discovery
candidates before Alignment; it is not chemical identity, and Matrix Curation
judges the published Matrix.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.configuration import paths
from xic_extractor.diagnostics.nl_adduct_count import cumulative, union_runs
from xic_extractor.diagnostics.nl_adduct_count_acquisition import (
    count_run,
    read_nucleosides,
)
from xic_extractor.presets.apply import bind_cid_nl_recipe
from xic_extractor.presets.loader import load_preset

NUCLEOSIDES = Path(paths.__file__).with_name("data") / "nl_adduct_nucleosides.tsv"


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    preset = bind_cid_nl_recipe(load_preset(args.preset), "full")
    nucleosides = read_nucleosides(args.nucleosides)
    runs = [
        count_run(
            directory,
            dll_dir=args.dll_dir,
            neutral_loss_by_tag=preset.discovery_settings.neutral_loss_by_tag,
            nucleosides=nucleosides,
        )
        for directory in (args.discovery_dir, *args.control_dir)
    ]
    sample, controls = runs[0], runs[1:]
    report: dict[str, object] = {"sample": sample.summary()}
    if controls:
        consensus = union_runs(controls)
        report["controls"] = {
            "injections": [run.summary() for run in controls],
            "cumulative": cumulative(consensus, len(controls)),
            "unconfirmed_in_one_injection": sum(
                c.unconfirmed and len(c.runs) == 1 for c in consensus
            ),
        }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, indent=2, ensure_ascii=False)
    (args.output_dir / "nl_adduct_count.json").write_text(text + "\n", encoding="utf-8")
    rows = [row for run in runs for row in run.candidate_rows()]
    with (args.output_dir / "nl_adduct_candidates.tsv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    print(text)
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Count adducts in one sample's neutral-loss Discovery output: remove "
            "ions derived from another candidate or a nucleoside, list single-MS2 "
            "and overlap-only compounds apart, and compare with replicate control "
            "injections."
        ),
    )
    parser.add_argument("--discovery-dir", type=Path, required=True)
    parser.add_argument(
        "--control-dir",
        type=Path,
        action="append",
        default=[],
        help="Discovery output of one control injection; repeat per injection.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dll-dir", type=Path, required=True)
    parser.add_argument(
        "--preset",
        default="dna_dr",
        help="Discovery preset; its neutral-loss tags pick the same-sugar nucleosides.",
    )
    parser.add_argument(
        "--nucleosides",
        type=Path,
        default=NUCLEOSIDES,
        help="TSV: label, mz, neutral_loss_tag, rt_min, rt_max.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())

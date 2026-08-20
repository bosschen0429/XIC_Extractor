"""Keep development-phase names out of the domain package.

Owner: #293. Principle: AGENTS.md "Product North Star" -- product rules must be
short, human-explainable, and domain-meaningful.

A lane fixture like `85raw` at least points at a real dataset. `Package 0`,
`M5`, and `p7-` point at a moment in the project's history, and once that
moment passes the name refers to nothing. The largest product module in the
repo is 2981 lines called `package0_successor_comparator.py`, whose docstring
is "Executable Package 0 parity and frozen successor-comparison receipt
checks" -- neither says what it does.

Eleven of the recorded occurrences are persisted values that reach artifacts
(schema identifiers, work-unit keys, a loaded resource filename), so this is
not only a readability concern. See #293.

Scope is `xic_extractor/`, including `diagnostics/`. This differs from
check_lane_fixture_boundary.py, which exempts diagnostics: replaying a named
lane is a legitimate thing for a diagnostic to do, whereas naming code after
the sprint that produced it has no equivalent justification anywhere.

NOT IN SCOPE: `tests/`, `scripts/`, `tools/`, `docs/`. They carry roughly 670
more occurrences, recorded in #293 so the scale is known. They are development
evidence, and the cost of renaming them is not repaid by the clarity gained.

DELIBERATELY NOT MATCHED: `tier1`/`tier2`/`tier3` are a genuine evidence-grade
concept (`tier2_apex_rt`, `tier3_width_only_sample_count`), not phase naming.
Any pattern added here must be checked against that distinction first -- a
checker that flags domain vocabulary trains people to ignore it.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parent.parent

# Names tied to a development phase, work-package split, milestone, or ticket.
#
# No leading \b on the first group: the worst cases are infixes, such as the
# persisted schema id "issue180_m9_gate_c_manual_review_v1". A word boundary
# would skip exactly those.
#
# Two spellings of a milestone, and they need different guards:
#
#   `\bM\d+(?![a-z0-9])` stays case-sensitive via a local flag, because a
#   case-insensitive form matches ordinary words like atom1A and form2Bx. The
#   trailing guard admits both the CamelCase prefix M5SuccessorVerdictReceipt
#   and the standalone M5 that prose uses ("owner-minted M2 decisions"), while
#   still refusing atom1abc. MS2 is safe: S is not a digit.
#
#   `m\d+_` is the snake_case spelling -- _M5_PACKET_SCHEMA, _m5_git_bytes,
#   m9_mode_track_source_v2. It may not follow a letter or digit, which is what
#   keeps atom1_x and form2_y out.
#
# `package`, `phase` and `issue` accept one separator, so "Package 0" and
# "Phase 1" in a docstring count the same as package0_x. Without that the
# checker could not see the example its own docstring quotes.
#
# Watch this against the tier1/tier2 rule if the domain ever adopts isotopologue
# notation: M1 and M2 would then be real vocabulary. Today they are not -- the
# repo contains no M+1 or M0 usage, and every standalone M-number is a milestone.
PHASE_NAMING_PATTERN = re.compile(
    r"(?i:package[ _-]?\d+|phase[ _-]?\d+|issue[ _-]?\d+|\bp\d+-"
    r"|(?<![a-z0-9])m\d+_)|\bM\d+(?![a-z0-9])"
)

SCOPE_ROOT = "xic_extractor/"

# path -> (max_hits, owning issue)
# Ceilings, not targets. Lower one when you remove a name; never raise one to
# make this checker pass. Regenerate from scan(), never by hand.
#
# Sorted by path so a regenerated map diffs cleanly against this one.
#
# The number moved twice, for opposite reasons:
#
#     355  at filing
#      52  after the identifier sweep. `Package0ComparatorError` alone was 194
#          lines; it, the `Package1*`/`M5*` classes and the `_PACKAGE1_*`
#          constants were all internal to a module no production code imports.
#     281  after `m\d+_` joined the pattern. Nothing was added to the codebase
#          -- the snake_case milestone spelling had simply never been visible.
#      58  after the same sweep ran over what the widening exposed: 76 `_M5_*`
#          and `_m5_*` identifiers in the comparator, and `Phase1` in
#          instrument_qc, which is now clean.
#     279  after review found the pattern could not match a standalone `M5` or
#          a separated `Package 0` -- the spelling its own docstring quotes.
#          Again nothing was added; three spellings had been unreadable.
#     279  still, after two offsetting corrections from the same review. The
#          scan read file *contents* only, so a module called package0_gate.py
#          with a clean body passed -- the exact form the docstring above says
#          this checker prevents; line 0 of a file's hits is now the path
#          itself, which added one. Rewriting the comparator's own docstring,
#          the "Executable Package 0 parity" line this file quotes, removed
#          one.
#
# The 279 are two different debts. Some are persisted -- schema ids, receipt
# fields, work-unit keys, pinned evidence directory names, `p7-` version
# strings -- and renaming one changes a value existing outputs carry. The rest,
# and it is now the majority, is prose: docstrings and error messages that say
# "M2 decisions" or "Package 1 experiment" to a reader who was not there.
# Prose costs nothing to change and breaks nothing, so it is the cheap half of
# what remains; it was simply invisible until this pattern was corrected.
#
# METHOD NOTE, learned the hard way: a census that greps artifacts for the
# literal value is necessary but not sufficient. `package2:alignment-method`
# returned zero literal hits, yet renaming it changed a derived evidence
# artifact id and broke the sha256 recorded in
# docs/superpowers/validation/m5_successor_verdict_v1/rollback_rehearsal.json.
# A value can reach an artifact through composition or hashing, where no string
# search will find it. Run the tests before believing a census.
#
# instrument_qc/calibration.py's `Phase1` was checked against the tier1/tier2
# exclusion and does not qualify. `Thermo_Stellar_IBD_Phase1_HSP` in the
# artifacts is a sample batch name, but the loader never references it: the
# CLI takes any `--trend-tsv` and the required-column tuple lists only neutral
# columns (sample_name, compound, apex_rt_min ...). The name describes which
# trend file was in play at the time, not a property of the data. Owner
# confirmed the reading; renamed accordingly, and that file is clean.
#
# The m7 metric names in untargeted_cid_nl_mode_track_workflow.py are the
# opposite call: five of the seven (m7_raw_open_count, m7_xic_batch_count,
# m7_trace_point_count, m7_logical_xic_request_count,
# m7_adapter_chromatogram_call_count) are already in artifacts. Renaming part of
# a field family would leave two schemas where there is one, so all seven stay.
KNOWN_PHASE_NAMES: dict[str, tuple[int, str]] = {
    "xic_extractor/alignment/backfill_evidence_contract.py": (1, "#293"),
    "xic_extractor/alignment/backfill_scope.py": (1, "#293"),
    "xic_extractor/alignment/cid_nl_evidence_spine.py": (5, "#293"),
    "xic_extractor/alignment/conservative_backfill.py": (16, "#293"),
    "xic_extractor/alignment/drift_evidence.py": (1, "#293"),
    "xic_extractor/alignment/identity_coherence/family_context.py": (2, "#293"),
    "xic_extractor/alignment/identity_coherence/mode_track_assignment.py": (10, "#293"),
    "xic_extractor/alignment/identity_coherence/mode_track_producer.py": (2, "#293"),
    "xic_extractor/alignment/identity_settlement.py": (13, "#293"),
    "xic_extractor/alignment/mode_track_table_finalization.py": (4, "#293"),
    "xic_extractor/alignment/production_acceptance.py": (1, "#293"),
    "xic_extractor/alignment/quantitative_projection_authority.py": (3, "#293"),
    "xic_extractor/alignment/runtime_adapter.py": (1, "#293"),
    "xic_extractor/alignment/untargeted_cid_nl_golden_path.py": (6, "#293"),
    "xic_extractor/execution_runtime.py": (1, "#293"),
    "xic_extractor/extraction/targeted_diagnostic_application.py": (2, "#293"),
    "xic_extractor/extraction/targeted_diagnostic_method.py": (2, "#293"),
    "xic_extractor/extraction/targeted_real_data_runtime.py": (1, "#293"),
    "xic_extractor/extraction/untargeted_cid_nl_mode_track_workflow.py": (19, "#293"),
    "xic_extractor/extraction/untargeted_cid_nl_real_data_adapter.py": (3, "#293"),
    "xic_extractor/extraction/untargeted_cid_nl_real_data_inputs.py": (2, "#293"),
    "xic_extractor/extraction/untargeted_cid_nl_real_data_runtime.py": (7, "#293"),
    "xic_extractor/instrument_qc/decision_report.py": (2, "#293"),
    "xic_extractor/output/target_pair_rt_auto_reselection.py": (1, "#293"),
    "xic_extractor/package0_successor_comparator.py": (187, "#293"),
}


def _tracked_python_files() -> list[str]:
    out = subprocess.check_output(
        ["git", "ls-files", "xic_extractor/*.py"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return [line.strip() for line in out.split("\n") if line.strip()]


def in_scope(path: str) -> bool:
    return path.startswith(SCOPE_ROOT)


def scan(paths: Sequence[str] | None = None) -> dict[str, list[tuple[int, str]]]:
    """Return {path: [(lineno, line), ...]} for every in-scope phase name."""
    if paths is None:
        paths = [p for p in _tracked_python_files() if in_scope(p)]
    hits: dict[str, list[tuple[int, str]]] = {}
    for path in paths:
        try:
            text = (ROOT / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Line 0 is the module path itself. A file called package0_gate.py is
        # the exact form this checker exists to prevent, and scanning only the
        # bytes inside it would let a new one through with a clean body.
        # One entry per match, not per line. Counting lines let a second token
        # join an already-counted line without moving the number, so the debt
        # could grow while this ratchet stayed green.
        found: list[tuple[int, str]] = [
            (0, f"<module path> {path}")
            for _ in PHASE_NAMING_PATTERN.finditer(path)
        ]
        found += [
            (n, line.strip())
            for n, line in enumerate(text.split("\n"), start=1)
            for _ in PHASE_NAMING_PATTERN.finditer(line)
        ]
        if found:
            hits[path] = found
    return hits


def check_phase_naming_boundary() -> list[str]:
    hits = scan()
    problems: list[str] = []

    for path, found in sorted(hits.items()):
        budget, owner = KNOWN_PHASE_NAMES.get(path, (0, ""))
        if len(found) <= budget:
            continue
        if budget == 0:
            problems.append(
                f"{path}: development-phase name in the domain package "
                f"({len(found)} hit(s)). Name it for what it does, not for the "
                f"phase that produced it. First at line {found[0][0]}: "
                f"{found[0][1][:60]}"
            )
        else:
            problems.append(
                f"{path}: phase naming grew past its budget "
                f"({len(found)} > {budget}, owner={owner}). Budgets ratchet "
                f"down only."
            )

    for path, (budget, owner) in sorted(KNOWN_PHASE_NAMES.items()):
        actual = len(hits.get(path, ()))
        if actual == 0:
            problems.append(
                f"{path}: recorded {budget} known name(s) but none remain. "
                f"Delete this KNOWN_PHASE_NAMES entry."
            )
        elif actual < budget:
            problems.append(
                f"{path}: budget {budget} exceeds the {actual} remaining "
                f"name(s). Lower it to {actual} to keep the ratchet tight."
            )

    return problems


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--list",
        action="store_true",
        help="List every in-scope hit, including budgeted ones",
    )
    args = parser.parse_args(argv)

    if args.list:
        for path, found in sorted(scan().items()):
            budget, owner = KNOWN_PHASE_NAMES.get(path, (0, ""))
            print(f"{path}  hits={len(found)} budget={budget} owner={owner or '-'}")
            for lineno, line in found[:6]:
                print(f"    {lineno}: {line[:84]}")
            if len(found) > 6:
                print(f"    ... {len(found) - 6} more")
        return 0

    problems = check_phase_naming_boundary()
    if problems:
        print("Phase naming boundary violations:", file=sys.stderr)
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        return 1

    budgeted = sum(b for b, _ in KNOWN_PHASE_NAMES.values())
    issues = sorted({owner for _, owner in KNOWN_PHASE_NAMES.values()})
    print(
        f"Phase naming boundary held "
        f"({budgeted} known name(s) in {len(KNOWN_PHASE_NAMES)} file(s), "
        f"tracked in {', '.join(issues)})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

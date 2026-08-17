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
# No leading \b on the first group: the worst cases are infixes, such as
# MULTI_SEED_REQUIRES_PHASE2 and its persisted value
# "multi_seed_requires_phase2". A word boundary would skip exactly those.
#
# The M-prefix group stays case-sensitive via a local flag, because a
# case-insensitive `m\d+[A-Z]` matches ordinary words like atom1A and form2Bx.
# It targets CamelCase class prefixes such as M5SuccessorVerdictReceipt.
PHASE_NAMING_PATTERN = re.compile(
    r"(?i:package\d+|phase\d+|issue\d+|\bp\d+-)|\bM\d+[A-Z]"
)

SCOPE_ROOT = "xic_extractor/"

# path -> (max_hits, owning issue)
# Ceilings, not targets. Lower one when you remove a name; never raise one to
# make this checker pass.
KNOWN_PHASE_NAMES: dict[str, tuple[int, str]] = {
    # 91% of the total. Naming this module for what it does would resolve most
    # of #293 on its own.
    "xic_extractor/package0_successor_comparator.py": (323, "#293"),
    "xic_extractor/instrument_qc/calibration.py": (18, "#293"),
    # MULTI_SEED_REQUIRES_PHASE2 and its persisted value.
    "xic_extractor/alignment/identity_coherence/schema.py": (3, "#293"),
    # Loads issue256_quantitative_projection_authority_v1.json -- a ticket
    # number that became a real filename.
    "xic_extractor/alignment/quantitative_projection_authority.py": (3, "#293"),
    # package2: work-unit keys, persisted.
    "xic_extractor/extraction/targeted_diagnostic_application.py": (2, "#293"),
    "xic_extractor/alignment/mode_track_table_finalization.py": (2, "#293"),
    "xic_extractor/alignment/production_acceptance.py": (1, "#293"),
    "xic_extractor/instrument_qc/calibration_writers.py": (1, "#293"),
    # p7- version strings, persisted.
    "xic_extractor/alignment/backfill_scope.py": (1, "#293"),
    "xic_extractor/alignment/backfill_evidence_contract.py": (1, "#293"),
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
        found = [
            (n, line.strip())
            for n, line in enumerate(text.split("\n"), start=1)
            if PHASE_NAMING_PATTERN.search(line)
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

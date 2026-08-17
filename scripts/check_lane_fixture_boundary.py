"""Keep lane validation fixtures out of the domain package.

Owner: AGENTS.md "Product North Star".

    XIC Extractor is an LC-MS evidence and model-selection system, not a
    single-dataset or CID-NL-only pipeline.
    8RAW and 85RAW are lane-specific validation fixtures and stress oracles,
    not architecture boundaries or universal Discovery gates.

That rule had no enforcement, and the domain package drifted: persisted artifact
ids, scope values and config keys across the domain are named for the lane they
were first validated against.
Architecture Guardrails, which sits
in the same file, held perfectly over the same period -- it has import-linter
and an architecture census watching it. This checker is the missing half.

Scope is deliberately narrow. Only `xic_extractor/` outside `diagnostics/` must
be dataset-neutral, because that is the code that has to run on whatever data a
user brings.

NOT IN SCOPE, and not a cleanup target: `docs/`, `tests/`, `scripts/`,
`tools/`, `output/`, `local_validation_artifacts/`. Those hold validation
evidence pinned during development to prevent drift. Naming a lane fixture is
exactly what they are supposed to do, and discarding them would destroy the
record of how the current behaviour was established. Belonging to the evidence
layer rather than the long-term architecture is a statement about where the
code lives, not about whether it is worth keeping.

Existing violations are recorded in KNOWN_LEAKAGE with a hit budget rather than
being ignored wholesale, so debt cannot grow silently while it waits to be
paid down. Each entry carries the issue that owns it. Budgets ratchet down
only: removing a leak without lowering its budget fails, and emptying a file
requires deleting its entry.

Names are all this checker can see. An assumption expressed as a number --
`expected_sample_count: int = 8` -- passes it untouched. See #290 group 3.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parent.parent

# Lane fixture identifiers. Delimited by non-alphanumerics rather than \b, so a
# snake_case segment counts: `85raw_alignment_cells` and `all_available_85raw`
# are hits. \b treated `_` as a word character and missed every infix, which is
# where most of the persisted lane names live. Letters and digits still guard
# both ends, so `raw8bit_decode` and `withdraw85` stay clean.
LANE_FIXTURE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:85raw|raw85|8raw|raw8|2raw|raw2)(?![A-Za-z0-9])",
    re.IGNORECASE,
)

SCOPE_ROOT = "xic_extractor/"
# Diagnostics are product-adjacent replay tooling; they are allowed to name the
# lane they replay. Everything else under xic_extractor/ must stay neutral.
EXEMPT_PREFIXES = ("xic_extractor/diagnostics/",)

# path -> (max_hits, owning issue)
# Budgets are ceilings, not targets. Lower one when you remove a leak; never
# raise one to make this checker pass. Regenerate this map from scan(); a
# hand-built set was wrong in four files the last time one was written.
#
# Sorted by path so a regenerated map diffs cleanly against this one.
#
# Nearly every line below was invisible while the pattern was \b-anchored,
# because `_` counts as a word character and these are snake_case infixes:
# `85raw_alignment_cells` (an artifact_id), `all_available_85raw` (a scope
# value), `max_projected_85raw_identity_xic_requests` (a config key). Most are
# persisted values, so clearing a file is a contract decision about existing
# artifacts, not a rename: a census of output/ before each removal is what says
# whether a rename is free. #290 owns the set, and its group 1 is closed.
KNOWN_LEAKAGE: dict[str, tuple[int, str]] = {
    "xic_extractor/alignment/quant_matrix_promotion.py": (4, "#290"),
    "xic_extractor/alignment/quant_matrix_validation_packet.py": (6, "#290"),
    "xic_extractor/alignment/shared_peak_identity_explanation/activation_contract.py": (
        8,
        "#290",
    ),
    "xic_extractor/alignment/shared_peak_identity_explanation/blast_radius.py": (
        28,
        "#290",
    ),
    "xic_extractor/alignment/shared_peak_identity_explanation/classifier.py": (
        14,
        "#290",
    ),
    "xic_extractor/alignment/shared_peak_identity_explanation/schema.py": (5, "#290"),
    "xic_extractor/package0_successor_comparator.py": (1, "#290"),
    "xic_extractor/peak_detection/selected_envelope_changed_row_review.py": (1, "#290"),
    "xic_extractor/peak_detection/selected_envelope_oracle.py": (2, "#290"),
    "xic_extractor/peak_detection/selected_envelope_oracle_artifacts.py": (2, "#290"),
}


def _tracked_python_files() -> list[str]:
    out = subprocess.check_output(
        ["git", "ls-files", "xic_extractor/*.py"], cwd=ROOT, text=True,
        encoding="utf-8", errors="replace",
    )
    return [line.strip() for line in out.split("\n") if line.strip()]


def in_scope(path: str) -> bool:
    return path.startswith(SCOPE_ROOT) and not path.startswith(EXEMPT_PREFIXES)


def scan(paths: Sequence[str] | None = None) -> dict[str, list[tuple[int, str]]]:
    """Return {path: [(lineno, line), ...]} for every in-scope leak."""
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
            if LANE_FIXTURE_PATTERN.search(line)
        ]
        if found:
            hits[path] = found
    return hits


def check_lane_fixture_boundary() -> list[str]:
    hits = scan()
    problems: list[str] = []

    for path, found in sorted(hits.items()):
        budget, owner = KNOWN_LEAKAGE.get(path, (0, ""))
        if len(found) <= budget:
            continue
        if budget == 0:
            problems.append(
                f"{path}: lane fixture named in the domain package "
                f"({len(found)} hit(s)); the domain must run on any dataset. "
                f"First at line {found[0][0]}: {found[0][1][:70]}"
            )
        else:
            problems.append(
                f"{path}: lane-fixture leakage grew past its budget "
                f"({len(found)} > {budget}, owner={owner}). Budgets ratchet "
                f"down only -- remove the new reference instead of raising it."
            )

    # A budget that no longer matches reality is stale bookkeeping: shrink it.
    for path, (budget, owner) in sorted(KNOWN_LEAKAGE.items()):
        actual = len(hits.get(path, ()))
        if actual == 0:
            problems.append(
                f"{path}: recorded {budget} known leak(s) but none remain. "
                f"Delete this KNOWN_LEAKAGE entry."
            )
        elif actual < budget:
            problems.append(
                f"{path}: budget {budget} exceeds the {actual} remaining "
                f"leak(s). Lower it to {actual} to keep the ratchet tight."
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
        hits = scan()
        for path, found in sorted(hits.items()):
            budget, owner = KNOWN_LEAKAGE.get(path, (0, ""))
            print(f"{path}  hits={len(found)} budget={budget} owner={owner or '-'}")
            for lineno, line in found:
                print(f"    {lineno}: {line[:88]}")
        return 0

    problems = check_lane_fixture_boundary()
    if problems:
        print("Lane fixture boundary violations:", file=sys.stderr)
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        return 1

    budgeted = sum(b for b, _ in KNOWN_LEAKAGE.values())
    issues = sorted({owner for _, owner in KNOWN_LEAKAGE.values()})
    print(
        f"Lane fixture boundary held "
        f"(domain files scanned outside diagnostics; "
        f"{budgeted} known leak(s) in {len(KNOWN_LEAKAGE)} file(s), "
        f"tracked in {', '.join(issues)})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

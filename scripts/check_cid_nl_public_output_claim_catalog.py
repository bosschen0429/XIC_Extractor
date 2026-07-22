"""Validate the bounded CID-NL public-output claim catalog.

The catalog indexes existing owners and executable tests. It does not grant
ProductWriter, selected-value, counted-detection, or Matrix authority.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xic_extractor.tabular_io import read_delimited_rows  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG_PATH = (
    ROOT
    / "docs/superpowers/validation/cid_nl_public_output_claim_catalog_v1.tsv"
)

CATALOG_COLUMNS = (
    "schema_version",
    "claim_id",
    "public_surface",
    "existing_authority_owner",
    "evidence_or_fixture",
    "executable_gate",
    "disposition",
    "forbidden_overclaim",
    "replacement_or_retirement_gate",
)
SCHEMA_VERSION = "cid_nl_public_output_claim_catalog_v1"
ALLOWED_DISPOSITIONS = frozenset(
    {"retain", "rewrite", "replace", "retire", "unknown"}
)
REQUIRED_CLAIM_IDS = frozenset(
    {
        "targeted_output_v1.file_bytes",
        "targeted_output_v1.schema_and_order",
        "targeted_output_v1.numeric_and_nl_projection",
        "targeted_output_v1.missing_zero_error_and_duplicates",
        "targeted_output_v1.selected_value_authority_ref",
        "targeted_output_v1.counted_detection_authority_ref",
        "untargeted_matrix_v1.authority_boundary_ref",
    }
)
CHARACTERIZATION_EVIDENCE_PREFIXES = (
    "docs/superpowers/validation/cid_nl_legacy_baseline_v1/",
    "docs/superpowers/validation/cid_nl_targeted_csv_contract_v1/",
)

def check_cid_nl_public_output_claim_catalog(
    *, catalog_path: Path = DEFAULT_CATALOG_PATH
) -> list[str]:
    problems: list[str] = []
    try:
        rows = read_delimited_rows(
            catalog_path,
            required_columns=CATALOG_COLUMNS,
            exact_columns=CATALOG_COLUMNS,
            delimiter="\t",
            encoding="utf-8-sig",
        )
    except (OSError, ValueError) as exc:
        return [f"catalog: {exc}"]

    if not rows:
        return ["catalog must contain at least one claim"]

    claim_ids = [row["claim_id"].strip() for row in rows]
    duplicate_ids = sorted(
        claim_id
        for claim_id, count in Counter(claim_ids).items()
        if claim_id and count > 1
    )
    if duplicate_ids:
        problems.append("duplicate claim ids: " + ", ".join(duplicate_ids))

    observed_ids = frozenset(claim_ids)
    missing = sorted(REQUIRED_CLAIM_IDS - observed_ids)
    unexpected = sorted(observed_ids - REQUIRED_CLAIM_IDS)
    if missing:
        problems.append("missing required claim ids: " + ", ".join(missing))
    if unexpected:
        problems.append("unexpected claim ids: " + ", ".join(unexpected))

    for row_index, row in enumerate(rows, start=2):
        _check_row(row, row_index=row_index, problems=problems)
    return problems


def _check_row(
    row: Mapping[str, str], *, row_index: int, problems: list[str]
) -> None:
    claim_id = row["claim_id"].strip()
    for column in CATALOG_COLUMNS:
        if not row[column].strip():
            row_label = claim_id or "blank"
            problems.append(f"row {row_index} ({row_label}): {column} is blank")

    if row["schema_version"].strip() != SCHEMA_VERSION:
        problems.append(
            f"row {row_index} ({claim_id}): schema_version must be {SCHEMA_VERSION}"
        )
    disposition = row["disposition"].strip()
    if disposition not in ALLOWED_DISPOSITIONS:
        problems.append(
            f"row {row_index} ({claim_id}): unsupported disposition {disposition!r}"
        )

    authority_refs = _split_refs(row["existing_authority_owner"])
    characterization_owners = [
        ref
        for ref in authority_refs
        if _reference_path(ref).startswith(CHARACTERIZATION_EVIDENCE_PREFIXES)
    ]
    if characterization_owners:
        problems.append(
            f"row {row_index} ({claim_id}): characterization evidence cannot be "
            "an authority owner: " + ", ".join(characterization_owners)
        )

    for ref in authority_refs:
        _check_repo_reference(
            ref,
            label="authority owner",
            row_index=row_index,
            claim_id=claim_id,
            problems=problems,
        )
    for ref in _split_refs(row["evidence_or_fixture"]):
        _check_repo_reference(
            ref,
            label="evidence or fixture",
            row_index=row_index,
            claim_id=claim_id,
            problems=problems,
        )
    for gate in _split_refs(row["executable_gate"]):
        _check_executable_gate(
            gate,
            row_index=row_index,
            claim_id=claim_id,
            problems=problems,
        )

    is_reference = claim_id.endswith("_ref")
    surface = row["public_surface"].strip()
    if is_reference and not surface.startswith("reference_only:"):
        problems.append(
            f"row {row_index} ({claim_id}): authority references must be "
            "marked reference_only"
        )
    if not is_reference and not surface.startswith("hard_exact:"):
        problems.append(
            f"row {row_index} ({claim_id}): delivery claims must be marked hard_exact"
        )

def _check_repo_reference(
    ref: str,
    *,
    label: str,
    row_index: int,
    claim_id: str,
    problems: list[str],
) -> None:
    path = _repo_reference_path(ref)
    if path is None:
        problems.append(
            f"row {row_index} ({claim_id}): {label} is not a repo-relative "
            f"clean-checkout path: {ref}"
        )
    elif not path.is_file():
        problems.append(
            f"row {row_index} ({claim_id}): {label} does not resolve: {ref}"
        )


def _check_executable_gate(
    gate: str, *, row_index: int, claim_id: str, problems: list[str]
) -> None:
    path = _repo_reference_path(gate)
    if path is None:
        problems.append(
            f"row {row_index} ({claim_id}): executable gate is not a "
            f"repo-relative clean-checkout path: {gate}"
        )
        return
    if not path.is_file() or "::" not in gate:
        problems.append(
            f"row {row_index} ({claim_id}): executable gate does not resolve: {gate}"
        )
        return
    test_name = gate.rsplit("::", 1)[1].strip()
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        problems.append(
            f"row {row_index} ({claim_id}): executable gate does not resolve: {gate}"
        )
        return
    if not re.search(rf"^def {re.escape(test_name)}\b", source, flags=re.MULTILINE):
        problems.append(
            f"row {row_index} ({claim_id}): executable gate does not resolve: {gate}"
        )


def _split_refs(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(";") if part.strip())


def _reference_path(value: str) -> str:
    return value.split("::", 1)[0].split("#", 1)[0].strip()


def _repo_reference_path(value: str) -> Path | None:
    path_text = _reference_path(value)
    relative = Path(path_text)
    if not path_text or relative.is_absolute() or relative.drive:
        return None
    if ".." in relative.parts:
        return None
    resolved = (ROOT / relative).resolve()
    if not resolved.is_relative_to(ROOT.resolve()):
        return None
    return resolved


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    problems = check_cid_nl_public_output_claim_catalog(catalog_path=args.catalog)
    if problems:
        print("CID-NL public-output claim catalog failed:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("CID-NL public-output claim catalog is valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

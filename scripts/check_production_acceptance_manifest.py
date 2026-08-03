"""Check ProductionAcceptanceManifest v1 schema and TSV artifacts."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.alignment.production_acceptance import (
    ACCEPTANCE_DECISIONS,
    REQUIRED_COLUMNS,
)
from xic_extractor.alignment.production_acceptance import (
    check_production_acceptance_manifest as _check_manifest,
)
from xic_extractor.alignment.production_acceptance import (
    check_production_acceptance_manifest_schema as _check_schema,
)
from xic_extractor.alignment.quant_matrix_version import (
    production_acceptance_manifest_sha256,
)

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ACCEPTANCE_MANIFEST_SCHEMA = (
    ROOT / "docs/superpowers/schemas/production_acceptance_manifest_schema.v1.json"
)


def check_production_acceptance_manifest_schema(
    *,
    schema_path: Path = PRODUCTION_ACCEPTANCE_MANIFEST_SCHEMA,
) -> list[str]:
    """Check the tracked schema against the package-owned authority contract."""

    return _check_schema(schema_path=schema_path)


def check_production_acceptance_manifest(
    *,
    manifest_path: Path,
    schema_path: Path = PRODUCTION_ACCEPTANCE_MANIFEST_SCHEMA,
    repo_root: Path = ROOT,
) -> list[str]:
    """Check schema synchronization and the manifest's complete authority rules."""

    return _check_manifest(
        manifest_path=manifest_path,
        schema_path=schema_path,
        repo_root=repo_root,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--schema",
        type=Path,
        default=PRODUCTION_ACCEPTANCE_MANIFEST_SCHEMA,
    )
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args(argv)
    if args.manifest is None:
        problems = check_production_acceptance_manifest_schema(schema_path=args.schema)
    else:
        problems = check_production_acceptance_manifest(
            manifest_path=args.manifest,
            schema_path=args.schema,
            repo_root=ROOT,
        )
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    if args.manifest is None:
        print("ProductionAcceptanceManifest schema is valid.")
    else:
        print("ProductionAcceptanceManifest is valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "ACCEPTANCE_DECISIONS",
    "PRODUCTION_ACCEPTANCE_MANIFEST_SCHEMA",
    "REQUIRED_COLUMNS",
    "check_production_acceptance_manifest",
    "check_production_acceptance_manifest_schema",
    "main",
    "production_acceptance_manifest_sha256",
)

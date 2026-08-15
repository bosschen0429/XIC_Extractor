from __future__ import annotations

import ast
from pathlib import Path

from xic_extractor.discovery.physical_fact_persistence import (
    DiscoveryPhysicalFactRecord,
)


def test_physical_fact_record_has_dependency_light_canonical_owner() -> None:
    from xic_extractor.discovery.physical_fact_record import (
        DiscoveryPhysicalFactRecord as CanonicalDiscoveryPhysicalFactRecord,
    )

    assert DiscoveryPhysicalFactRecord is CanonicalDiscoveryPhysicalFactRecord
    assert CanonicalDiscoveryPhysicalFactRecord.__module__ == (
        "xic_extractor.discovery.physical_fact_record"
    )

    repository_root = Path(__file__).parents[1]
    for relative_path in (
        "xic_extractor/discovery/ms1_backfill.py",
        "xic_extractor/discovery/physical_fact_sidecar.py",
        "xic_extractor/discovery/pipeline.py",
    ):
        source = (repository_root / relative_path).read_text(encoding="utf-8")
        persistence_imports = {
            alias.name
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom)
            and node.module
            == "xic_extractor.discovery.physical_fact_persistence"
            for alias in node.names
        }
        assert "DiscoveryPhysicalFactRecord" not in persistence_imports, relative_path

import ast
from pathlib import Path


def test_alignment_exports_plan2_public_api_names():
    import xic_extractor.alignment as alignment

    assert alignment.__all__ == (
        "AlignmentConfig",
        "AlignmentCluster",
        "AlignedCell",
        "AlignmentMatrix",
        "CellStatus",
    )
    assert {
        name for name in dir(alignment) if not name.startswith("_")
    } == set(alignment.__all__)


def test_discovery_csv_and_alignment_tsv_do_not_import_raw_reader():
    package_root = Path(__file__).parents[1] / "xic_extractor"
    for relative_path in (
        "discovery/csv_reader.py",
        "output/alignment/tsv_writer.py",
    ):
        path = package_root / relative_path
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        violations = [
            imported_name
            for node in ast.walk(tree)
            for imported_name in _imported_module_names(node)
            if imported_name.startswith("xic_extractor.raw_reader")
        ]

        assert violations == []




def test_alignment_ownership_module_stays_domain_focused():
    source = (
        Path(__file__).parents[1]
        / "xic_extractor"
        / "alignment"
        / "ownership.py"
    ).read_text(encoding="utf-8")

    forbidden = (
        "xic_extractor.output.alignment.tsv_writer",
        "scripts.run_alignment",
        "openpyxl",
        "csv.",
    )
    for token in forbidden:
        assert token not in source


def test_alignment_edge_scoring_stays_domain_focused():
    path = (
        Path(__file__).parents[1]
        / "xic_extractor"
        / "alignment"
        / "edge_scoring.py"
    )
    banned_roots = (
        "gui",
        "openpyxl",
        "scripts",
        "xic_extractor.alignment.process_backend",
        "xic_extractor.raw_reader",
    )

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations = [
        imported_name
        for node in ast.walk(tree)
        for imported_name in _imported_module_names(node)
        if imported_name.startswith(banned_roots)
    ]

    assert violations == []


def _imported_module_names(node):
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        imported_names = [node.module] if node.module else []
        imported_names.extend(
            f"{node.module}.{alias.name}"
            for alias in node.names
            if node.module
        )
        return imported_names
    return []

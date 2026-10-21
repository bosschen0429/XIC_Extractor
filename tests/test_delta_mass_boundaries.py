"""Delta-mass remains inward-pure and outside product publication routes."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DISCOVERY = ROOT / "xic_extractor" / "discovery"


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    result = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            result.add(module)
            result.update(module + "." + alias.name for alias in node.names)
    return result


def test_delta_mass_method_does_not_acquire_or_publish():
    forbidden = (
        "xic_extractor.raw_reader",
        "xic_extractor.alignment",
        "xic_extractor.output",
        "xic_extractor.acquisition",
        "multiprocessing",
        "hashlib",
        "scripts",
    )
    for name in ("delta_mass_patterns.py", "delta_mass_strategy.py"):
        assert not {
            module
            for module in imports(DISCOVERY / name)
            if module.startswith(forbidden)
        }


def test_delta_mass_pipeline_does_not_import_product_writers_or_alignment():
    for path in (
        *DISCOVERY.glob("delta_mass_*.py"),
        ROOT / "scripts/run_delta_mass_discovery.py",
    ):
        assert not {
            module
            for module in imports(path)
            if module.startswith(
                (
                    "xic_extractor.alignment",
                    "xic_extractor.output",
                    "scripts.run_alignment",
                )
            )
        }
        assert "ProductWriter" not in path.read_text(encoding="utf-8")


def test_product_routes_do_not_consume_delta_mass_diagnostics():
    for package in ("alignment", "applications", "output", "untargeted_execution"):
        for path in (ROOT / "xic_extractor" / package).rglob("*.py"):
            assert not {
                module
                for module in imports(path)
                if module.startswith("xic_extractor.discovery.delta_mass_")
            }, path

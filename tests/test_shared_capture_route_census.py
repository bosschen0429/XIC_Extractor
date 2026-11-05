import ast
from pathlib import Path

import xic_extractor


def test_direct_hypothesis_owner_consumers_match_the_finite_census() -> None:
    package_root = Path(xic_extractor.__file__).parent
    direct_importers = {
        path.relative_to(package_root).as_posix()
        for path in package_root.rglob("*.py")
        if _imports_hypothesis_builder(path)
    }

    assert direct_importers == {
        "extraction/targeted_peak_hypotheses.py",
        "peak_detection/capture.py",
    }


def _imports_hypothesis_builder(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "xic_extractor.peak_detection.hypotheses"
        and any(alias.name == "build_peak_hypotheses" for alias in node.names)
        for node in ast.walk(tree)
    )

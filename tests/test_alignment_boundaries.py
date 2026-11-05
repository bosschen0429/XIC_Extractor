import ast
from pathlib import Path


def test_alignment_exports_plan2_public_api_names():
    import xic_extractor.alignment as alignment

    assert alignment.__all__ == ("AlignmentConfig",)
    assert {name for name in dir(alignment) if not name.startswith("_")} == set(
        alignment.__all__
    )


def _imported_module_names(node):
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        imported_names = [node.module] if node.module else []
        imported_names.extend(
            f"{node.module}.{alias.name}" for alias in node.names if node.module
        )
        return imported_names
    return []


def test_alignment_consumes_quality_facts_without_importing_noise_estimators():
    root = Path(__file__).parents[1] / "xic_extractor" / "alignment"
    forbidden = (
        "xic_extractor.peak_detection.baseline",
        "xic_extractor.peak_detection.signal_quality.short_scale_noise_sigma",
        "xic_extractor.peak_detection.signal_quality.measure_signal_quality",
        "xic_extractor.peak_detection.signal_quality.candidate_signal_quality",
    )
    violations = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        violations.extend(
            (str(path.relative_to(root)), name)
            for node in ast.walk(tree)
            for name in _imported_module_names(node)
            if name.startswith(forbidden)
        )
    assert violations == []

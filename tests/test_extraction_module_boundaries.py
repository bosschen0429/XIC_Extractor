from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_targeted_inputs_owns_raw_path_resolution() -> None:
    extraction_dir = ROOT / "xic_extractor" / "extraction"

    targeted_inputs_source = (extraction_dir / "targeted_inputs.py").read_text(
        encoding="utf-8"
    )
    assert "read_analysis_set(" in targeted_inputs_source

    for module_name in ("pipeline.py", "serial_backend.py", "process_backend.py"):
        source = (extraction_dir / module_name).read_text(encoding="utf-8")
        assert 'glob("*.raw")' not in source
        assert "read_analysis_set" not in source


def test_untargeted_identity_paths_exclude_targeted_product_projection() -> None:
    forbidden_terms = (
        "TargetedProductProjection",
        "detected_flagged",
        "Product State",
        "Counted Detection",
        "targeted_review_positive",
    )
    scoped_dirs = (
        ROOT / "xic_extractor" / "alignment",
        ROOT / "xic_extractor" / "discovery",
    )
    findings: list[str] = []
    for scoped_dir in scoped_dirs:
        for path in scoped_dir.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            for term in forbidden_terms:
                if term in source:
                    findings.append(f"{path.relative_to(ROOT)}: {term}")

    assert findings == []

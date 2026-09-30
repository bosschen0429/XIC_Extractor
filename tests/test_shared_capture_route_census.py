import ast
from pathlib import Path

from xic_extractor.extraction import untargeted_cid_nl_real_data_adapter
from xic_extractor.peak_detection import capture as capture_owner


def test_current_adapter_cannot_bypass_shared_capture_implementation() -> None:
    untargeted_source = _function_source(
        Path(untargeted_cid_nl_real_data_adapter.__file__),
        "_reacquire_peak_observations",
    )
    capture_source = _function_source(
        Path(capture_owner.__file__),
        "capture_peak_hypotheses",
    )

    assert "capture_owner.capture_peak_hypotheses" in untargeted_source
    assert "capture_owner.mint_captured_peak_observation" in untargeted_source
    assert "build_peak_hypotheses(" not in untargeted_source
    assert "mint_peak_observation_ref(" not in untargeted_source
    assert _call_keyword_sets(
        untargeted_source,
        function_name="capture_peak_hypotheses",
    ) == {frozenset({"scope", "candidate_id", "trace"})}
    assert _call_keyword_sets(
        untargeted_source,
        function_name="mint_captured_peak_observation",
    ) == {frozenset({"processing_owners", "sample"})}
    assert "scope.method_constraints.signal_processing_profile" in capture_source
    assert "promote_targeted_boundary_hypothesis" not in capture_source


def test_targeted_boundary_promotion_has_no_scientific_implementation() -> None:
    package_root = Path(untargeted_cid_nl_real_data_adapter.__file__).parents[1]
    implementers = {
        path.relative_to(package_root).as_posix()
        for path in package_root.rglob("*.py")
        if _defines_function(path, "promote_targeted_boundary_hypothesis")
    }

    assert implementers == set()


def test_direct_hypothesis_owner_consumers_match_the_finite_census() -> None:
    package_root = Path(untargeted_cid_nl_real_data_adapter.__file__).parents[1]
    direct_importers = {
        path.relative_to(package_root).as_posix()
        for path in package_root.rglob("*.py")
        if _imports_hypothesis_builder(path)
    }

    assert direct_importers == {
        "extraction/targeted_peak_hypotheses.py",
        "extraction/untargeted_cid_nl_real_data_adapter.py",
        "peak_detection/capture.py",
    }


def _function_source(path: Path, function_name: str) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next(
        index
        for index, line in enumerate(lines)
        if line.startswith(f"def {function_name}(")
    )
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].startswith("def ")
        ),
        len(lines),
    )
    return "\n".join(lines[start:end])


def _imports_hypothesis_builder(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "xic_extractor.peak_detection.hypotheses"
        and any(alias.name == "build_peak_hypotheses" for alias in node.names)
        for node in ast.walk(tree)
    )


def _defines_function(path: Path, function_name: str) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
        for node in ast.walk(tree)
    )


def _call_keyword_sets(
    source: str,
    *,
    function_name: str,
) -> set[frozenset[str]]:
    tree = ast.parse(source)
    return {
        frozenset(keyword.arg for keyword in node.keywords if keyword.arg is not None)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == function_name
    }

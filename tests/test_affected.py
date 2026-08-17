from pathlib import Path

from tools.testing.affected import (
    BROAD_IMPACT_PATTERNS,
    _direct_imports,
    _tracked_python_files,
    module_name,
    select,
)
from tools.testing.test_shards import discover_test_files

ALL_TESTS = tuple(p.as_posix() for p in discover_test_files())


def _known_modules() -> set[str]:
    return {
        module_name(path)
        for path in _tracked_python_files()
        if path.startswith(("xic_extractor/", "scripts/", "tools/", "gui/"))
    }


def test_module_name_maps_paths_and_collapses_package_init() -> None:
    assert module_name("xic_extractor/alignment/matrix.py") == (
        "xic_extractor.alignment.matrix"
    )
    assert module_name("xic_extractor/alignment/__init__.py") == (
        "xic_extractor.alignment"
    )


def test_shared_conftest_refuses_to_narrow() -> None:
    selected, broad, unresolved = select(["tests/conftest.py"])

    assert broad == ["tests/conftest.py"]
    assert unresolved == []
    assert len(selected) == len(ALL_TESTS)


def test_dependency_lockfile_refuses_to_narrow() -> None:
    selected, broad, _ = select(["uv.lock"])

    assert broad == ["uv.lock"]
    assert len(selected) == len(ALL_TESTS)


def test_selector_itself_refuses_to_narrow() -> None:
    _, broad, _ = select(["tools/testing/affected.py"])

    assert broad == ["tools/testing/affected.py"]


def test_broad_patterns_stay_narrow_enough_to_be_worth_having() -> None:
    # Each entry costs the tool its entire value for that change, so the list
    # is deliberately tiny. Growing it is a decision, not a detail.
    assert len(BROAD_IMPACT_PATTERNS) <= 6


def test_product_module_selects_its_dependents_but_not_everything() -> None:
    selected, broad, unresolved = select(
        ["xic_extractor/alignment/owner_backfill.py"]
    )

    assert broad == []
    assert unresolved == []
    assert "tests/test_alignment_owner_backfill.py" in selected
    assert 0 < len(selected) < len(ALL_TESTS)


def test_package_init_resolves_its_own_relative_imports() -> None:
    # module_name() strips `.__init__`, so a naive `level` subtraction climbs
    # one package too far and every `from .x import y` in an __init__.py
    # silently drops out of the graph.
    resolved = _direct_imports(
        "xic_extractor/alignment/identity_coherence/__init__.py",
        _known_modules(),
    )

    assert "xic_extractor.alignment.identity_coherence.models" in resolved
    assert "xic_extractor.alignment.models" not in resolved


def test_module_relative_imports_still_resolve_to_the_parent_package() -> None:
    resolved = _direct_imports(
        "xic_extractor/alignment/quant_matrix_promotion.py",
        _known_modules(),
    )

    assert "xic_extractor.alignment.quant_matrix_report" in resolved


def test_unrelated_modules_are_left_out() -> None:
    selected, _, _ = select(["xic_extractor/alignment/owner_backfill.py"])

    assert "tests/test_xic_models.py" not in selected


def test_changed_test_file_selects_itself() -> None:
    selected, broad, unresolved = select(["tests/test_xic_models.py"])

    assert selected == ["tests/test_xic_models.py"]
    assert broad == []
    assert unresolved == []


def test_non_python_change_is_reported_rather_than_silently_dropped() -> None:
    selected, broad, unresolved = select(["README.md"])

    assert selected == []
    assert broad == []
    assert unresolved == ["README.md"]


def test_deleted_or_unknown_module_is_unresolved() -> None:
    missing = "xic_extractor/definitely_not_a_real_module.py"
    assert not Path(missing).exists()

    selected, _, unresolved = select([missing])

    assert selected == []
    assert unresolved == [missing]

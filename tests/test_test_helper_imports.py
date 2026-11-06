"""Tests share helpers through fixture modules, never through another test."""

import ast
from pathlib import Path

TESTS = Path(__file__).parent


def _imported_test_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("tests.test_"):
                modules.add(node.module)
            elif node.module == "tests":
                modules.update(
                    f"tests.{alias.name}"
                    for alias in node.names
                    if alias.name.startswith("test_")
                )
        elif isinstance(node, ast.Import):
            modules.update(
                alias.name
                for alias in node.names
                if alias.name.startswith("tests.test_")
            )
    return {module.split(".")[1] for module in modules}


def _test_module_imports() -> set[tuple[str, str]]:
    return {
        (path.stem, imported)
        for path in TESTS.glob("*.py")
        for imported in _imported_test_modules(
            ast.parse(path.read_text(encoding="utf-8"), path.name)
        )
    }


def test_tests_share_helpers_through_fixture_modules():
    found = sorted(_test_module_imports())

    assert not found, (
        "Move the shared helper into a non-test module under tests/ "
        "(docs/agent/testing.md#test-placement) instead of importing another "
        "test module:\n"
        + "\n".join(f"  {importer} imports {module}" for importer, module in found)
    )


def test_detects_function_local_and_package_style_test_imports():
    tree = ast.parse(
        "import tests.test_a\n"
        "from tests import test_b, membership_fixtures\n"
        "def helper():\n"
        "    from tests.test_c import _private\n"
        "from tests.membership_fixtures import cid_nl_owner\n"
    )

    assert _imported_test_modules(tree) == {"test_a", "test_b", "test_c"}

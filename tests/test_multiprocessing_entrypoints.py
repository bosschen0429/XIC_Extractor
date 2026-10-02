import ast
from pathlib import Path


def test_cli_entrypoint_calls_freeze_support_first() -> None:
    first_call = _first_call_in_main(Path("scripts/run_extraction.py"))

    assert first_call == "multiprocessing.freeze_support"


def test_html_entrypoint_calls_freeze_support_first() -> None:
    first_call = _first_call_in_main(Path("xic_workbench/cli.py"))

    assert first_call == "multiprocessing.freeze_support"


def _first_call_in_main(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    main = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    first_statement = main.body[0]
    assert isinstance(first_statement, ast.Expr)
    assert isinstance(first_statement.value, ast.Call)
    return _call_name(first_statement.value.func)


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Attribute):
        return f"{_call_name(node.value)}.{node.attr}"
    if isinstance(node, ast.Name):
        return node.id
    raise AssertionError(f"unsupported call node: {ast.dump(node)}")

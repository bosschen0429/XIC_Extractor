import ast
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_csv_to_excel_is_thin_compatibility_wrapper() -> None:
    script_path = ROOT / "scripts" / "csv_to_excel.py"

    assert _openpyxl_imports(script_path) == []
    assert len(script_path.read_text(encoding="utf-8").splitlines()) <= 300


def test_workbook_inputs_exists_and_is_openpyxl_free() -> None:
    assert importlib.util.find_spec("xic_extractor.output.workbook_inputs") is not None

    input_source = (ROOT / "xic_extractor" / "output" / "workbook_inputs.py").read_text(
        encoding="utf-8"
    )
    assert "workbook_styles" not in input_source
    assert (
        _openpyxl_imports(ROOT / "xic_extractor" / "output" / "workbook_inputs.py")
        == []
    )


def _openpyxl_imports(path: Path) -> list[ast.AST]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [node for node in ast.walk(tree) if _is_openpyxl_import(node)]


def _is_openpyxl_import(node: ast.AST) -> bool:
    if isinstance(node, ast.Import):
        return any(_is_openpyxl_name(alias.name) for alias in node.names)
    if isinstance(node, ast.ImportFrom) and node.module is not None:
        return _is_openpyxl_name(node.module)
    return False


def _is_openpyxl_name(name: str) -> bool:
    return name == "openpyxl" or name.startswith("openpyxl.")

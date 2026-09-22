from __future__ import annotations

from pathlib import Path

from tools.diagnostics.production_reachability_census import find_unreachable_symbols


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_reports_symbols_that_only_tests_or_their_own_body_reach(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path / "pyproject.toml",
        '[project.scripts]\nxic-run = "xic_extractor.cli:launch"\n',
    )
    _write(
        tmp_path / "xic_extractor" / "cli.py",
        "def launch():\n    return None\n",
    )
    _write(
        tmp_path / "xic_extractor" / "stage.py",
        "__all__ = ['exported_only']\n"
        "\n"
        "def used_by_gui():\n"
        "    return 1\n"
        "\n"
        "def exported_only():\n"
        "    return 2\n"
        "\n"
        "def recursive_only(n):\n"
        "    return recursive_only(n - 1) if n else 0\n"
        "\n"
        "class Store:\n"
        "    def __len__(self):\n"
        "        return 0\n"
        "\n"
        "    def read(self):\n"
        "        return self.helper()\n"
        "\n"
        "    def helper(self):\n"
        "        return 3\n"
        "\n"
        "    def never_read(self):\n"
        "        return 4\n"
        "\n"
        "def uses_store():\n"
        "    return Store().read()\n",
    )
    _write(
        tmp_path / "gui" / "view.py",
        "from xic_extractor import stage\n"
        "\n"
        "stage.used_by_gui()\n"
        "stage.uses_store()\n",
    )
    _write(
        tmp_path / "tests" / "test_stage.py",
        "from xic_extractor.stage import exported_only, recursive_only\n"
        "\n"
        "exported_only()\n"
        "recursive_only(2)\n",
    )

    unreachable = find_unreachable_symbols(tmp_path)

    assert {symbol.qualified_name for symbol in unreachable} == {
        "xic_extractor.stage.exported_only",
        "xic_extractor.stage.recursive_only",
        "xic_extractor.stage.Store.never_read",
    }

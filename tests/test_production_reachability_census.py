from __future__ import annotations

import subprocess
from pathlib import Path

from tools.diagnostics.production_reachability_census import (
    find_unreachable_symbols,
    orphaned_since,
)


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
        "def used_by_workbench():\n"
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
        tmp_path / "xic_workbench" / "view.py",
        "from xic_extractor import stage\n"
        "\n"
        "stage.used_by_workbench()\n"
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


def test_import_alias_use_counts_for_the_original_name(tmp_path: Path) -> None:
    _write(tmp_path / "pyproject.toml", "[project]\n")
    _write(
        tmp_path / "xic_extractor" / "stage.py",
        "def called_through_alias():\n"
        "    return 1\n"
        "\n"
        "def imported_but_unused():\n"
        "    return 2\n",
    )
    _write(
        tmp_path / "xic_workbench" / "view.py",
        "from xic_extractor.stage import called_through_alias as run\n"
        "from xic_extractor.stage import imported_but_unused as unused\n"
        "\n"
        "run()\n",
    )

    unreachable = {symbol.name for symbol in find_unreachable_symbols(tmp_path)}

    assert "called_through_alias" not in unreachable
    assert "imported_but_unused" in unreachable


def test_tools_reach_only_the_diagnostics_package(tmp_path: Path) -> None:
    _write(tmp_path / "pyproject.toml", "[project]\n")
    _write(
        tmp_path / "xic_extractor" / "diagnostics" / "review.py",
        "def review():\n    return 1\n",
    )
    _write(tmp_path / "xic_extractor" / "model.py", "def tool_only():\n    return 2\n")
    _write(
        tmp_path / "tools" / "diagnostics" / "run.py",
        "from xic_extractor.diagnostics.review import review\n"
        "from xic_extractor.model import tool_only\n"
        "\n"
        "review()\n"
        "tool_only()\n",
    )

    unreachable = {symbol.name for symbol in find_unreachable_symbols(tmp_path)}

    assert unreachable == {"tool_only"}


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


def test_base_comparison_fails_only_on_symbols_this_change_orphaned(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "pyproject.toml", "[project]\n")
    _write(
        tmp_path / "xic_extractor" / "stage.py",
        "def kept():\n    return 1\n\ndef replaced():\n    return 2\n\n"
        "def already_dead():\n    return 3\n",
    )
    _write(
        tmp_path / "xic_workbench" / "view.py",
        "from xic_extractor.stage import kept, replaced\n\nkept()\nreplaced()\n",
    )
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")

    assert orphaned_since(tmp_path, "HEAD") == ()

    _write(
        tmp_path / "xic_workbench" / "view.py",
        "from xic_extractor.stage import kept\n\nkept()\n",
    )
    _write(
        tmp_path / "xic_extractor" / "added.py", "def never_called():\n    return 4\n"
    )

    assert {symbol.name for symbol in orphaned_since(tmp_path, "HEAD")} == {
        "replaced",
        "never_called",
    }
    assert orphaned_since(tmp_path, "HEAD", staged=True) == ()

    _git(tmp_path, "add", "xic_workbench/view.py")

    assert {
        symbol.name for symbol in orphaned_since(tmp_path, "HEAD", staged=True)
    } == {"replaced"}

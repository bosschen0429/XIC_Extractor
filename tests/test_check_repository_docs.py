from __future__ import annotations

import subprocess
from pathlib import Path

from scripts import check_repository_docs as docs_check


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for rel_path, text in files.items():
        path = tmp_path / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return tmp_path


def test_resolving_links_and_live_lanes_are_clean(tmp_path: Path) -> None:
    root = _repo(
        tmp_path,
        {
            "README.md": (
                "[guide](docs/user/guide.md#start) [dir](docs/user/) "
                "[web](https://example.org) [anchor](#top)\n"
                "`[not a link](missing.md)`\n"
                "```\n[fenced](missing.md)\n```\n"
            ),
            "docs/user/guide.md": "[back](../../README.md)\n",
        },
    )

    assert docs_check.check(root) == []


def test_broken_relative_link_is_reported(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"docs/a.md": "[gone](retired.md)\n"})

    assert docs_check.check(root) == ["docs/a.md: broken link -> retired.md"]


def test_untracked_link_target_is_broken(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"docs/a.md": "[local](notes.md)\n"})
    (root / "docs" / "notes.md").write_text("untracked\n", encoding="utf-8")

    assert docs_check.check(root) == ["docs/a.md: broken link -> notes.md"]


def test_tracked_file_in_retired_lane_is_reported(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"docs/superpowers/plans/next.md": "plan\n"})

    assert docs_check.check(root) == [
        "docs/superpowers/plans/next.md: tracked file in retired docs lane"
    ]

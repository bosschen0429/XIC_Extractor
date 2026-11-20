import subprocess
import sys
from pathlib import Path

import pytest

from scripts.check_comments import comments

CHECK = Path(__file__).resolve().parents[1] / "scripts/check_comments.py"


@pytest.mark.parametrize(
    ("path", "source", "expected"),
    [
        (
            "p.py",
            '#!/usr/bin/env python\n# coding: utf-8\ns = "# text"  # type: ignore\n'
            '"""# docstring"""\nanswer = 1  # prose',
            [(5, "# prose")],
        ),
        (
            "page.html",
            "<style>a { color: #abc }/* css */</style>\n"
            '<script>let s = "//literal"; // js\n</script><!-- html -->',
            [(1, "/* css */"), (2, "// js"), (3, "<!-- html -->")],
        ),
        (
            ".github/workflows/test.yml",
            "jobs:\n  lint:\n    steps:\n      - shell: pwsh\n        run: |\n"
            '          Write-Output "# string" # shell\n        name: lint # yaml',
            [(7, "# yaml"), (6, "# shell")],
        ),
        ("xic_extractor/output/writer.py", "value = 1 # note", [(1, "# note")]),
        ("tools/a.js", "/* @license MIT */\n// @ts-check\nconst s = `//text`;", []),
        ("tools/test.ps1", "#Requires -Version 7\n# why", [(2, "# why")]),
        ("vendor/library.js", "// upstream", []),
        ("output/generated.py", "# generated", []),
    ],
)
def test_inventory_finds_only_source_prose(path, source, expected):
    assert list(comments(path, source)) == expected


def test_ratchet_rejects_replacement_duplicates_and_new_files(tmp_path):
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    def lint():
        return subprocess.run(
            [sys.executable, str(CHECK), "--base", "HEAD"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )

    git("init")
    source = tmp_path / "old.py"
    body = "value = 1\nsecond_value = value + 1\nthird_value = second_value + 1\n"
    source.write_text("# kept\n# retired\n" + body)
    git("add", ".")
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.org",
        "commit",
        "-m",
        "base",
    )
    git("mv", "old.py", "new.py")
    renamed = tmp_path / "new.py"
    renamed.write_text("# kept\n" + body)
    assert lint().returncode == 0
    renamed.write_text("# kept\n# kept\n" + body + "last = 1  # replacement\n")
    (tmp_path / "new.js").write_text('const url = "https://example.org"; // extra\n')
    git("add", ".")
    result = lint()
    assert result.returncode == 1
    assert "# replacement" in result.stdout
    assert result.stdout.count("# kept") == 1
    assert "new.js:1:" in result.stdout

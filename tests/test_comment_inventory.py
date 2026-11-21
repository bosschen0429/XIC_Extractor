import pytest

from scripts.list_comments import comments


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

from __future__ import annotations

import json
from pathlib import Path

from tools.diagnostics import code_intelligence_paired_pilot as pilot


def test_score_reports_locator_recall_without_promoting_extra_paths(
    tmp_path: Path,
) -> None:
    last = tmp_path / "last.json"
    oracle = tmp_path / "oracle.json"
    last.write_text(
        json.dumps(
            {
                "locators": [
                    {
                        "path": str(tmp_path / "tools" / "owner.py"),
                        "line": 12,
                        "claim": "owner",
                    },
                    {"path": "unrelated.py", "line": 1, "claim": "extra"},
                ]
            }
        ),
        encoding="utf-8",
    )
    oracle.write_text(
        json.dumps(
            {
                "expected_locators": [
                    {"path": "tools/owner.py", "line_min": 10, "line_max": 15},
                    {"path": "tests/test_owner.py", "line_min": 20, "line_max": 25},
                ],
                "allowed_paths": ["tools/owner.py", "tests/test_owner.py"],
            }
        ),
        encoding="utf-8",
    )

    score = pilot._score(last, oracle, tmp_path)

    assert score["relevant_locator_recall"] == 0.5
    assert score["false_locator_count"] == 1


def test_plain_arm_removes_codegraph_bin_from_path(monkeypatch, tmp_path: Path) -> None:
    npm_bin = tmp_path / "npm"
    npm_bin.mkdir()
    codex_js = npm_bin / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    codex_js.parent.mkdir(parents=True)
    codex_js.write_text("", encoding="utf-8")
    node = tmp_path / "node.exe"
    node.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        pilot.shutil,
        "which",
        lambda name: str(node) if name == "node" else str(npm_bin / "codex.cmd"),
    )
    monkeypatch.setenv("PATH", f"{npm_bin}{pilot.os.pathsep}{tmp_path}")
    args = pilot.build_parser().parse_args(
        [
            "--repo",
            str(tmp_path),
            "--repo-sha",
            "abc",
            "--task-id",
            "t",
            "--task-prompt",
            str(tmp_path / "task.md"),
            "--output-schema",
            str(tmp_path / "schema.json"),
            "--oracle",
            str(tmp_path / "oracle.json"),
            "--output-root",
            str(tmp_path / "out"),
            "--disposable-worktree",
        ]
    )

    _, env = pilot._arm_command_and_env("plain", args, tmp_path / "last.json")

    assert str(npm_bin.resolve()) not in env["PATH"].split(pilot.os.pathsep)


def test_parse_events_counts_completed_tool_payload_once() -> None:
    stdout = "\n".join(
        [
            json.dumps({"type": "item.started", "item": {"type": "mcp_tool_call"}}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "mcp_tool_call",
                        "server": "codegraph",
                        "tool": "codegraph_explore",
                        "result": {"content": "abc"},
                    },
                }
            ),
        ]
    )

    _, item_types, _, metrics = pilot._parse_events(stdout)

    assert item_types["mcp_tool_call"] == 1
    assert metrics["mcp_tool_counts"] == {"codegraph/codegraph_explore": 1}
    assert metrics["tool_payload_chars"] > 0

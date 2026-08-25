from __future__ import annotations

import json
from pathlib import Path

from tools.diagnostics import codegraph_session_baseline


def _write_session(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )


def _event(timestamp: str, payload: dict[str, object]) -> dict[str, object]:
    return {"timestamp": timestamp, "type": "event_msg", "payload": payload}


def test_build_report_counts_actual_calls_and_emits_no_raw_content(
    tmp_path: Path,
) -> None:
    sessions_root = tmp_path / "sessions"
    raw_prompt = "追查 symbol 的 callers 與跨模組影響面，secret-prompt-marker"
    raw_payload = "src/a.py:1 Alpha\nsrc/b.py:2 Beta\nsrc/a.py:1 Alpha\n"
    records = [
        {
            "timestamp": "2026-08-01T00:00:00Z",
            "type": "session_meta",
            "payload": {
                "id": "graph-session",
                "cwd": r"C:\work\XIC_Extractor",
            },
        },
        _event(
            "2026-08-01T00:00:01Z",
            {"type": "user_message", "message": raw_prompt},
        ),
        _event(
            "2026-08-01T00:00:02Z",
            {
                "type": "task_started",
                "turn_id": "turn-1",
                "model_context_window": 1_000,
            },
        ),
        _event(
            "2026-08-01T00:00:03Z",
            {
                "type": "mcp_tool_call_end",
                "call_id": "call-1",
                "invocation": {
                    "server": "codegraph",
                    "tool": "codegraph_explore",
                    "arguments": {"intent": "trace callers"},
                },
                "duration": 1.25,
                "result": {"Ok": raw_payload},
            },
        ),
        {
            "timestamp": "2026-08-01T00:00:04Z",
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "name": "exec",
                "input": json.dumps({"cmd": "rg -n Alpha src; Get-Content src/a.py"}),
            },
        },
        _event(
            "2026-08-01T00:00:05Z",
            {
                "type": "mcp_tool_call_end",
                "call_id": "call-2",
                "invocation": {
                    "server": "codegraph",
                    "tool": "codegraph_explore",
                    "arguments": {"intent": "trace callers"},
                },
                "duration": 0.75,
                "result": {
                    "Ok": {
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "This is not a stale cache.\n"
                                    "src/a.py:1 Alpha\nsrc/c.py:3 Gamma\n"
                                ),
                            }
                        ]
                    }
                },
            },
        ),
        _event(
            "2026-08-01T00:00:06Z",
            {
                "type": "token_count",
                "info": {
                    "total_token_usage": {"input_tokens": 2_000},
                    "last_token_usage": {"input_tokens": 800},
                    "model_context_window": 1_000,
                },
            },
        ),
        _event("2026-08-01T00:00:07Z", {"type": "context_compacted"}),
        _event(
            "2026-08-01T00:00:08Z",
            {
                "type": "task_complete",
                "turn_id": "turn-1",
                "duration_ms": 6_000,
            },
        ),
        _event(
            "2026-08-01T00:00:09Z",
            {"type": "user_message", "message": "不對，請重新判斷"},
        ),
    ]
    _write_session(sessions_root / "2026" / "08" / "graph.jsonl", records)

    # A schema mention is not an actual tool call and must stay at zero.
    _write_session(
        sessions_root / "2026" / "08" / "graph-poor.jsonl",
        [
            {
                "timestamp": "2026-08-02T00:00:00Z",
                "type": "session_meta",
                "payload": {
                    "id": "graph-poor-session",
                    "thread_source": "subagent",
                    "cwd": r"C:\work\XIC_Extractor",
                    "base_instructions": (
                        "<!-- CODEGRAPH_START --> codegraph_explore "
                        "<!-- CODEGRAPH_END -->"
                    ),
                },
            },
            _event(
                "2026-08-02T00:00:01Z",
                {
                    "type": "user_message",
                    "message": "整理 branch 並寫 PR report",
                },
            ),
            {
                "timestamp": "2026-08-02T00:00:02Z",
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "unrelated_tool",
                    "arguments": '{"schema":"codegraph_explore"}',
                },
            },
        ],
    )
    cli_payload = "**Exploration: Parser**\nsrc/parser.py:1\n"
    _write_session(
        sessions_root / "2026" / "08" / "cli.jsonl",
        [
            {
                "timestamp": "2026-08-02T01:00:00Z",
                "type": "session_meta",
                "payload": {
                    "id": "cli-session",
                    "cwd": r"C:\work\XIC_Extractor",
                },
            },
            _event(
                "2026-08-02T01:00:01Z",
                {"type": "user_message", "message": "修正 parser bug"},
            ),
            {
                "timestamp": "2026-08-02T01:00:02Z",
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "call_id": "cli-call",
                    "name": "exec",
                    "input": 'codegraph explore "Parser"',
                },
            },
            {
                "timestamp": "2026-08-02T01:00:03Z",
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call_output",
                    "call_id": "cli-call",
                    "output": [
                        {
                            "type": "input_text",
                            "text": f"Script completed\nOutput:\n{cli_payload}",
                        }
                    ],
                },
            },
        ],
    )
    # Forked rollouts can copy the entire parent transcript with new timestamps.
    # They are not independent session evidence.
    _write_session(
        sessions_root / "2026" / "08" / "forked.jsonl",
        [
            {
                "timestamp": "2026-08-03T00:00:00Z",
                "type": "session_meta",
                "payload": {
                    "id": "forked-session",
                    "session_id": "graph-session",
                    "forked_from_id": "graph-session",
                    "thread_source": "subagent",
                    "cwd": r"C:\work\XIC_Extractor",
                },
            },
            records[3],
        ],
    )

    report = codegraph_session_baseline.build_report(
        sessions_root=sessions_root,
        repo_marker="XIC_Extractor",
    )

    assert report["verdict"] == "baseline_needed"
    historical = report["historical_observational_baseline"]
    assert historical["scan"]["matching_repo_session_files"] == 4
    assert historical["scan"]["repo_sessions"] == 3
    assert historical["scan"]["excluded_inherited_history_sessions"] == 1
    assert historical["scan"]["actual_codegraph_calls"] == 3
    graph_session = next(
        row for row in historical["sessions"] if row["session_id"] == "graph-session"
    )
    assert graph_session["task_class"] == "graph_fit"
    assert graph_session["codegraph_call_count"] == 2
    assert graph_session["codegraph_retry_count"] == 1
    assert graph_session["codegraph_output_chars"] == len(raw_payload) + len(
        "This is not a stale cache.\nsrc/a.py:1 Alpha\nsrc/c.py:3 Gamma\n"
    )
    assert (
        graph_session["codegraph_mcp_serialized_result_chars"]
        > graph_session["codegraph_output_chars"]
    )
    assert graph_session["stale_or_wrong_result_proxy_count"] == 0
    assert graph_session["followup_rg_calls"] == 1
    assert graph_session["followup_read_calls"] == 1
    assert graph_session["peak_last_input_context_ratio"] == 0.8
    assert graph_session["compaction_count"] == 1
    assert graph_session["active_wall_seconds"] == 6.0
    assert graph_session["operator_correction_count"] == 1
    cli_session = next(
        row for row in historical["sessions"] if row["session_id"] == "cli-session"
    )
    assert cli_session["codegraph_mcp_call_count"] == 0
    assert cli_session["codegraph_cli_call_count"] == 1
    assert cli_session["codegraph_cli_output_chars_proxy"] == len(cli_payload)
    assert historical["strata"]["graph_poor"]["actual_codegraph_calls"] == 0
    assert (
        historical["strata"]["graph_fit"]["cohorts"]["codegraph_observed"]["sessions"]
        == 1
    )
    assert (
        historical["strata"]["graph_poor"]["cohorts"][
            "codegraph_route_present_not_used"
        ]["sessions"]
        == 1
    )

    serialized = json.dumps(report, ensure_ascii=False)
    assert "secret-prompt-marker" not in serialized
    assert raw_payload not in serialized
    assert r"C:\work\XIC_Extractor" not in serialized


def test_cli_writes_paired_packet_with_complete_off_arm(tmp_path: Path) -> None:
    sessions_root = tmp_path / "sessions"
    _write_session(
        sessions_root / "session.jsonl",
        [
            {
                "timestamp": "2026-08-03T00:00:00Z",
                "type": "session_meta",
                "payload": {
                    "id": "ordinary-session",
                    "cwd": r"C:\work\XIC_Extractor",
                },
            },
            _event(
                "2026-08-03T00:00:01Z",
                {"type": "user_message", "message": "修正 parser bug"},
            ),
        ],
    )
    output = tmp_path / "report.json"

    exit_code = codegraph_session_baseline.main(
        [
            "--sessions-root",
            str(sessions_root),
            "--repo-marker",
            "XIC_Extractor",
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    packet = report["future_paired_exploration_packet"]
    assert packet["fixed_controls"] == [
        "repo_sha",
        "task_packet",
        "model",
        "reasoning_effort",
        "harness_version",
        "context_window",
        "token_budget",
        "cache_and_index_state",
    ]
    assert packet["arms"]["off"]["remove"] == [
        "CodeGraph MCP server and tool",
        "CodeGraph initialize instructions",
        "CodeGraph-first routing rule",
    ]
    assert packet["metrics"]["correctness"]["historical_status"] == "unscored"
    assert "composite_score" not in packet
    assert report["decision_rules"]["incomparable"] == "inconclusive"

"""Build an observational CodeGraph baseline from Codex session JSONL.

The output is aggregate diagnostic evidence. It deliberately excludes raw
prompts, tool arguments, tool results, absolute session paths, and acceptance
claims. Historical associations cannot establish CodeGraph causality.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import tempfile
from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "codegraph_session_baseline_v1"
TASK_CLASSES = ("graph_fit", "ordinary_coding", "graph_poor")
FOLLOWUP_TOOL_WINDOW = 8

GRAPH_FIT_PATTERNS = (
    r"\bcallers?\b",
    r"\bcallees?\b",
    r"\bcall[ -]?graph\b",
    r"\bcall[ -]?paths?\b",
    r"\bblast[ -]?radius\b",
    r"\btopology\b",
    r"\bsymbols?\b",
    r"\bdependencies\b",
    r"\breferences\b",
    r"\breferrers?\b",
    r"\bdata[ -]?flow\b",
    r"\bwhere (?:is|are).*(?:defined|used)\b",
    r"呼叫鏈",
    r"影響面",
    r"依賴",
    r"跨模組",
    r"架構",
)
ORDINARY_CODING_PATTERNS = (
    r"\bprompt_file\b",
    r"\bimplement(?:ation)?\b",
    r"\bfix(?:es|ed|ing)?\b",
    r"\bfeatures?\b",
    r"\bbugs?\b",
    r"\brefactor(?:ing)?\b",
    r"\bparsers?\b",
    r"\bperformance\b",
    r"\bpipelines?\b",
    r"\balignment\b",
    r"\bdiscovery\b",
    r"\bschemas?\b",
    r"\bmatrix\b",
    r"\bgui\b",
    r"\bcli\b",
    r"\bowners?\b",
    r"修正",
    r"實作",
    r"重構",
)
GRAPH_POOR_PATTERNS = (
    r"\bbranches?\b",
    r"\bpull requests?\b",
    r"\bprs?\b",
    r"\bcommits?\b",
    r"\bpush\b",
    r"\bmerge\b",
    r"\brebase\b",
    r"\bworktrees?\b",
    r"\bhooks?\b",
    r"agents\.md",
    r"\bsessions?\b",
    r"context window",
    r"\breports?\b",
    r"\bdocs?\b",
    r"\bconversation\b",
    r"\bmemory\b",
    r"\bskills?\b",
)
CORRECTION_PATTERNS = (
    r"不對",
    r"搞錯",
    r"錯了",
    r"別開玩笑",
    r"先回答",
    r"你又",
    r"重來",
    r"認真判斷",
    r"不是這個",
    r"這不是",
)
STALE_OR_WRONG_PATTERNS = (
    r"(?:error|failed|failure).{0,80}\bstale\b",
    r"index(?:ed)?.{0,30}(?:stale|out of date)",
    r"project.{0,40}not indexed",
    r"no indexed project",
    r"wrong (?:repository|workspace|branch)",
)
RG_PATTERN = re.compile(r"(?:^|[\s\"'])rg(?:\.exe)?(?:\s|$)", re.IGNORECASE)
READ_PATTERN = re.compile(
    r"\bget-content\b|\bread_(?:file|text)\b|\bopen_file\b|"
    r"\bsed\s+-n\b|\bcat\s+",
    re.IGNORECASE,
)
CODEGRAPH_CLI_PATTERN = re.compile(
    r"(?:^|[;&|]\s*|[\"']?(?:command|cmd)[\"']?\s*:\s*[\"'])"
    r"\s*(?:&\s*)?codegraph(?:\.exe)?\s+explore\b",
    re.IGNORECASE,
)


def _json_text(value: object) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        return str(value)


def _result_text(result: object) -> str:
    if isinstance(result, dict):
        ok = result.get("Ok")
        if isinstance(ok, str):
            return ok
        if isinstance(ok, dict):
            content = ok.get("content")
            if isinstance(content, dict):
                content = [content]
            if isinstance(content, list):
                text_blocks = [
                    item.get("text", "")
                    for item in content
                    if isinstance(item, dict) and isinstance(item.get("text"), str)
                ]
                if text_blocks:
                    return "\n".join(text_blocks)
    return _json_text(result)


def _content_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(
            item.get("text", "")
            for item in value
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        )
    if isinstance(value, dict) and isinstance(value.get("text"), str):
        return value["text"]
    return _json_text(value)


def _cli_output_proxy(value: object) -> str:
    """Return the bounded CodeGraph section from a shell tool output."""
    text = _content_text(value)
    start = text.find("**Exploration:")
    if start < 0:
        return text
    following = re.search(r"(?m)^===[^=\n]+===\s*$", text[start:])
    if following is None:
        return text[start:]
    return text[start : start + following.start()]


def _duration_seconds(value: object) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0.0, float(value))
    if isinstance(value, dict):
        seconds = value.get("secs", value.get("seconds", 0))
        nanos = value.get("nanos", value.get("nanoseconds", 0))
        try:
            return max(0.0, float(seconds) + float(nanos) / 1_000_000_000)
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _contains_any(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _pattern_score(text: str, patterns: Iterable[str]) -> int:
    return sum(1 for pattern in patterns if re.search(pattern, text, re.IGNORECASE))


def _classify_task(first_user_message: str) -> tuple[str, dict[str, int]]:
    graph_score = _pattern_score(first_user_message, GRAPH_FIT_PATTERNS)
    coding_score = _pattern_score(first_user_message, ORDINARY_CODING_PATTERNS)
    poor_score = _pattern_score(first_user_message, GRAPH_POOR_PATTERNS)
    if graph_score >= 2:
        task_class = "graph_fit"
    elif poor_score >= 1 and coding_score == 0:
        task_class = "graph_poor"
    else:
        task_class = "ordinary_coding"
    return task_class, {
        "graph_fit_keyword_hits": graph_score,
        "ordinary_coding_keyword_hits": coding_score,
        "graph_poor_keyword_hits": poor_score,
    }


def _is_codegraph_call(payload: dict[str, Any]) -> bool:
    if payload.get("type") != "mcp_tool_call_end":
        return False
    invocation = payload.get("invocation")
    if not isinstance(invocation, dict):
        return False
    return (
        str(invocation.get("server", "")).casefold() == "codegraph"
        and invocation.get("tool") == "codegraph_explore"
    )


def _tool_call_text(record_type: object, payload: dict[str, Any]) -> str | None:
    if record_type != "response_item":
        return None
    payload_type = payload.get("type")
    if payload_type == "custom_tool_call":
        return _json_text(payload.get("input", ""))
    if payload_type == "function_call":
        return _json_text(payload.get("arguments", ""))
    return None


def _result_failed(result: object) -> bool:
    if isinstance(result, dict):
        if "Err" in result or result.get("isError") is True:
            return True
        ok = result.get("Ok")
        return isinstance(ok, dict) and ok.get("isError") is True
    return False


def _normal_payload_lines(text: str) -> list[str]:
    lines = []
    for line in text.splitlines():
        normalized = " ".join(line.split()).casefold()
        if len(normalized) >= 8:
            lines.append(normalized)
    return lines


def _parse_session(
    path: Path,
    *,
    sessions_root: Path,
    repo_marker: str,
) -> dict[str, Any] | None:
    repo_marker_folded = repo_marker.casefold()
    matched_repo = False
    codegraph_route_present = False
    session_id = path.stem
    session_date: str | None = None
    invalid_json_lines = 0
    first_user_message = ""
    later_user_messages: list[str] = []
    first_user_timestamp: datetime | None = None
    mcp_calls: list[dict[str, Any]] = []
    cli_calls: list[dict[str, Any]] = []
    cli_calls_by_id: dict[str, dict[str, Any]] = {}
    tool_calls: list[tuple[int, str]] = []
    compaction_count = 0
    task_started_count = 0
    task_completed_count = 0
    active_wall_seconds = 0.0
    max_total_input_tokens = 0
    peak_context_ratio = 0.0
    event_index = 0

    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for raw_line in stream:
            event_index += 1
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError:
                invalid_json_lines += 1
                continue
            if not isinstance(record, dict):
                continue
            record_type = record.get("type")
            payload = record.get("payload")
            if not isinstance(payload, dict):
                payload = {}

            if not matched_repo:
                if record_type != "session_meta":
                    if event_index >= 50:
                        return None
                    continue
                meta_text = _json_text(payload).casefold()
                if repo_marker_folded not in meta_text:
                    return None
                matched_repo = True
                codegraph_route_present = (
                    "<!-- codegraph_start -->" in meta_text
                    and "codegraph_explore" in meta_text
                )
                session_id = str(payload.get("id") or session_id)
                timestamp = str(
                    record.get("timestamp") or payload.get("timestamp") or ""
                )
                session_date = timestamp[:10] or None
                if payload.get("forked_from_id"):
                    return {
                        "_excluded_reason": "inherited_history_session",
                        "invalid_json_lines": invalid_json_lines,
                    }
                continue

            if record_type == "response_item" and payload.get("type") == "message":
                route_text = _json_text(payload).casefold()
                if (
                    "<!-- codegraph_start -->" in route_text
                    and "codegraph_explore" in route_text
                ):
                    codegraph_route_present = True

            if record_type == "event_msg":
                event_type = payload.get("type")
                if event_type == "user_message":
                    message = _json_text(payload.get("message", ""))
                    timestamp = _parse_timestamp(record.get("timestamp"))
                    if not first_user_message:
                        first_user_message = message
                        first_user_timestamp = timestamp
                    else:
                        later_user_messages.append(message)
                elif _is_codegraph_call(payload):
                    invocation = payload["invocation"]
                    result = payload.get("result")
                    result_text = _result_text(result)
                    serialized_result = _json_text(result)
                    arguments_text = _json_text(invocation.get("arguments", {}))
                    mcp_calls.append(
                        {
                            "transport": "mcp",
                            "event_index": event_index,
                            "timestamp": _parse_timestamp(record.get("timestamp")),
                            "argument_key": arguments_text,
                            "argument_chars": len(arguments_text),
                            "output_chars": len(result_text),
                            "serialized_result_chars": len(serialized_result),
                            "output_lines": _normal_payload_lines(result_text),
                            "duration_seconds": _duration_seconds(
                                payload.get("duration")
                            ),
                            "failed": _result_failed(result),
                            "stale_or_wrong": _contains_any(
                                result_text, STALE_OR_WRONG_PATTERNS
                            ),
                        }
                    )
                elif event_type == "context_compacted":
                    compaction_count += 1
                elif event_type == "task_started":
                    task_started_count += 1
                elif event_type == "task_complete":
                    task_completed_count += 1
                    try:
                        active_wall_seconds += max(
                            0.0, float(payload.get("duration_ms", 0)) / 1_000
                        )
                    except (TypeError, ValueError):
                        pass
                elif event_type == "token_count":
                    info = payload.get("info")
                    if not isinstance(info, dict):
                        continue
                    total_usage = info.get("total_token_usage")
                    last_usage = info.get("last_token_usage")
                    if isinstance(total_usage, dict):
                        try:
                            max_total_input_tokens = max(
                                max_total_input_tokens,
                                int(total_usage.get("input_tokens", 0)),
                            )
                        except (TypeError, ValueError):
                            pass
                    if isinstance(last_usage, dict):
                        try:
                            last_input = float(last_usage.get("input_tokens", 0))
                            context_window = float(info.get("model_context_window", 0))
                            if context_window > 0:
                                peak_context_ratio = max(
                                    peak_context_ratio,
                                    last_input / context_window,
                                )
                        except (TypeError, ValueError):
                            pass

            if record_type == "response_item":
                response_type = payload.get("type")
                if response_type == "custom_tool_call":
                    input_text = _json_text(payload.get("input", ""))
                    command_count = len(CODEGRAPH_CLI_PATTERN.findall(input_text))
                    if command_count:
                        call_id = str(payload.get("call_id") or payload.get("id") or "")
                        cli_call = {
                            "transport": "cli",
                            "event_index": event_index,
                            "timestamp": _parse_timestamp(record.get("timestamp")),
                            "argument_key": input_text,
                            "argument_chars": len(input_text),
                            "command_count": command_count,
                            "output_chars": 0,
                            "serialized_result_chars": 0,
                            "output_lines": [],
                            "stale_or_wrong": False,
                        }
                        cli_calls.append(cli_call)
                        if call_id:
                            cli_calls_by_id[call_id] = cli_call
                elif response_type == "custom_tool_call_output":
                    call_id = str(payload.get("call_id") or "")
                    cli_call = cli_calls_by_id.get(call_id)
                    if cli_call is not None:
                        output = payload.get("output")
                        output_text = _cli_output_proxy(output)
                        cli_call["output_chars"] = len(output_text)
                        cli_call["serialized_result_chars"] = len(_json_text(output))
                        cli_call["output_lines"] = _normal_payload_lines(output_text)
                        cli_call["stale_or_wrong"] = _contains_any(
                            output_text, STALE_OR_WRONG_PATTERNS
                        )

            tool_text = _tool_call_text(record_type, payload)
            if tool_text is not None:
                tool_calls.append((event_index, tool_text))

    if not matched_repo:
        return None

    task_class, class_hits = _classify_task(first_user_message)
    all_codegraph_calls = sorted(
        [*mcp_calls, *cli_calls], key=lambda call: call["event_index"]
    )
    argument_counts = Counter(
        (call["transport"], call["argument_key"]) for call in all_codegraph_calls
    )
    retry_count = sum(max(0, count - 1) for count in argument_counts.values())
    all_payload_lines = [
        line for call in all_codegraph_calls for line in call["output_lines"]
    ]
    duplicate_payload_ratio = (
        (len(all_payload_lines) - len(set(all_payload_lines))) / len(all_payload_lines)
        if all_payload_lines
        else 0.0
    )

    followup_rg_indexes: set[int] = set()
    followup_read_indexes: set[int] = set()
    for call in all_codegraph_calls:
        following_tools = [
            tool_call for tool_call in tool_calls if tool_call[0] > call["event_index"]
        ][:FOLLOWUP_TOOL_WINDOW]
        for tool_index, tool_text in following_tools:
            if RG_PATTERN.search(tool_text):
                followup_rg_indexes.add(tool_index)
            if READ_PATTERN.search(tool_text):
                followup_read_indexes.add(tool_index)

    first_codegraph_seconds: float | None = None
    if first_user_timestamp and all_codegraph_calls:
        first_call_timestamp = all_codegraph_calls[0]["timestamp"]
        if first_call_timestamp:
            first_codegraph_seconds = max(
                0.0, (first_call_timestamp - first_user_timestamp).total_seconds()
            )

    output_samples = [call["output_chars"] for call in all_codegraph_calls]
    correction_count = sum(
        1
        for message in later_user_messages
        if _contains_any(message, CORRECTION_PATTERNS)
    )
    return {
        "session_id": session_id,
        "session_date": session_date,
        "session_locator": path.relative_to(sessions_root).as_posix(),
        "task_class": task_class,
        "task_class_basis": "first_user_message_keyword_heuristic",
        **class_hits,
        "codegraph_route_present": codegraph_route_present,
        "codegraph_call_count": len(all_codegraph_calls),
        "codegraph_mcp_call_count": len(mcp_calls),
        "codegraph_cli_call_count": len(cli_calls),
        "codegraph_cli_explore_command_count": sum(
            call["command_count"] for call in cli_calls
        ),
        "codegraph_argument_chars": sum(
            call["argument_chars"] for call in all_codegraph_calls
        ),
        "codegraph_output_chars": sum(output_samples),
        "codegraph_mcp_output_chars": sum(call["output_chars"] for call in mcp_calls),
        "codegraph_cli_output_chars_proxy": sum(
            call["output_chars"] for call in cli_calls
        ),
        "codegraph_output_max_chars": max(output_samples, default=0),
        "codegraph_mcp_serialized_result_chars": sum(
            call["serialized_result_chars"] for call in mcp_calls
        ),
        "codegraph_cli_serialized_output_chars": sum(
            call["serialized_result_chars"] for call in cli_calls
        ),
        "codegraph_transport_serialized_chars": sum(
            call["serialized_result_chars"] for call in all_codegraph_calls
        ),
        "codegraph_mcp_duration_seconds": round(
            sum(call["duration_seconds"] for call in mcp_calls), 6
        ),
        "codegraph_retry_count": retry_count,
        "codegraph_mcp_failure_count": sum(bool(call["failed"]) for call in mcp_calls),
        "stale_or_wrong_result_proxy_count": sum(
            bool(call["stale_or_wrong"]) for call in all_codegraph_calls
        ),
        "misroute_proxy_count": (
            len(all_codegraph_calls) if task_class == "graph_poor" else 0
        ),
        "duplicate_payload_ratio": round(duplicate_payload_ratio, 6),
        "followup_rg_calls": len(followup_rg_indexes),
        "followup_read_calls": len(followup_read_indexes),
        "followup_window_size": FOLLOWUP_TOOL_WINDOW,
        "max_total_input_tokens": max_total_input_tokens,
        "peak_last_input_context_ratio": round(peak_context_ratio, 6),
        "compaction_count": compaction_count,
        "active_wall_seconds": round(active_wall_seconds, 6),
        "time_to_first_codegraph_seconds": first_codegraph_seconds,
        "operator_correction_count": correction_count,
        "task_started_count": task_started_count,
        "task_completed_count": task_completed_count,
        "acceptance": "unscored",
        "invalid_json_lines": invalid_json_lines,
        "_output_char_samples": output_samples,
    }


def _percentile(values: Sequence[float | int], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def _aggregate_sessions(sessions: Sequence[dict[str, Any]]) -> dict[str, Any]:
    output_samples = [
        value for session in sessions for value in session["_output_char_samples"]
    ]
    context_ratios = [
        session["peak_last_input_context_ratio"]
        for session in sessions
        if session["peak_last_input_context_ratio"] > 0
    ]
    wall_times = [session["active_wall_seconds"] for session in sessions]
    total_input_tokens = [
        session["max_total_input_tokens"]
        for session in sessions
        if session["max_total_input_tokens"] > 0
    ]
    weighted_duplicate_numerator = sum(
        session["duplicate_payload_ratio"] * session["codegraph_output_chars"]
        for session in sessions
    )
    total_output_chars = sum(session["codegraph_output_chars"] for session in sessions)
    return {
        "sessions": len(sessions),
        "sessions_with_codegraph": sum(
            session["codegraph_call_count"] > 0 for session in sessions
        ),
        "sessions_with_codegraph_route": sum(
            bool(session["codegraph_route_present"]) for session in sessions
        ),
        "actual_codegraph_calls": sum(
            session["codegraph_call_count"] for session in sessions
        ),
        "codegraph_mcp_calls": sum(
            session["codegraph_mcp_call_count"] for session in sessions
        ),
        "codegraph_cli_calls": sum(
            session["codegraph_cli_call_count"] for session in sessions
        ),
        "codegraph_cli_explore_commands": sum(
            session["codegraph_cli_explore_command_count"] for session in sessions
        ),
        "codegraph_output_chars": total_output_chars,
        "codegraph_mcp_output_chars": sum(
            session["codegraph_mcp_output_chars"] for session in sessions
        ),
        "codegraph_cli_output_chars_proxy": sum(
            session["codegraph_cli_output_chars_proxy"] for session in sessions
        ),
        "codegraph_mcp_serialized_result_chars": sum(
            session["codegraph_mcp_serialized_result_chars"] for session in sessions
        ),
        "codegraph_cli_serialized_output_chars": sum(
            session["codegraph_cli_serialized_output_chars"] for session in sessions
        ),
        "codegraph_transport_serialized_chars": sum(
            session["codegraph_transport_serialized_chars"] for session in sessions
        ),
        "median_codegraph_output_chars_per_call": (
            statistics.median(output_samples) if output_samples else None
        ),
        "p90_codegraph_output_chars_per_call": _percentile(output_samples, 0.9),
        "max_codegraph_output_chars_per_call": max(output_samples, default=0),
        "codegraph_mcp_duration_seconds": round(
            sum(session["codegraph_mcp_duration_seconds"] for session in sessions), 6
        ),
        "duplicate_payload_ratio_char_weighted_proxy": round(
            weighted_duplicate_numerator / total_output_chars, 6
        )
        if total_output_chars
        else 0.0,
        "codegraph_retry_count": sum(
            session["codegraph_retry_count"] for session in sessions
        ),
        "codegraph_mcp_failure_count": sum(
            session["codegraph_mcp_failure_count"] for session in sessions
        ),
        "misroute_proxy_count": sum(
            session["misroute_proxy_count"] for session in sessions
        ),
        "stale_or_wrong_result_proxy_count": sum(
            session["stale_or_wrong_result_proxy_count"] for session in sessions
        ),
        "followup_rg_calls": sum(session["followup_rg_calls"] for session in sessions),
        "followup_read_calls": sum(
            session["followup_read_calls"] for session in sessions
        ),
        "operator_correction_count": sum(
            session["operator_correction_count"] for session in sessions
        ),
        "compaction_count": sum(session["compaction_count"] for session in sessions),
        "peak_last_input_context_ratio": max(context_ratios, default=0.0),
        "median_peak_last_input_context_ratio": (
            round(statistics.median(context_ratios), 6) if context_ratios else None
        ),
        "max_total_input_tokens_observed": max(total_input_tokens, default=0),
        "median_max_total_input_tokens_observed": (
            statistics.median(total_input_tokens) if total_input_tokens else None
        ),
        "active_wall_seconds": round(sum(wall_times), 6),
        "median_active_wall_seconds": (
            round(statistics.median(wall_times), 6) if wall_times else None
        ),
        "acceptance": "unscored",
    }


def _aggregate_with_cohorts(
    sessions: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    summary = _aggregate_sessions(sessions)
    summary["cohorts"] = {
        "codegraph_observed": _aggregate_sessions(
            [session for session in sessions if session["codegraph_call_count"] > 0]
        ),
        "codegraph_route_present_not_used": _aggregate_sessions(
            [
                session
                for session in sessions
                if session["codegraph_route_present"]
                and session["codegraph_call_count"] == 0
            ]
        ),
        "codegraph_route_not_observed": _aggregate_sessions(
            [
                session
                for session in sessions
                if not session["codegraph_route_present"]
                and session["codegraph_call_count"] == 0
            ]
        ),
    }
    return summary


def _paired_packet() -> dict[str, Any]:
    return {
        "design": "same-SHA counterbalanced paired exploration in fresh sessions",
        "task_strata": list(TASK_CLASSES),
        "fixed_controls": [
            "repo_sha",
            "task_packet",
            "model",
            "reasoning_effort",
            "harness_version",
            "context_window",
            "token_budget",
            "cache_and_index_state",
        ],
        "arms": {
            "on": {
                "retain": [
                    "CodeGraph MCP server and tool",
                    "CodeGraph initialize instructions",
                    "CodeGraph-first routing rule",
                ]
            },
            "off": {
                "remove": [
                    "CodeGraph MCP server and tool",
                    "CodeGraph initialize instructions",
                    "CodeGraph-first routing rule",
                ],
                "retain": ["the same shell, rg, file-read, and edit capabilities"],
            },
        },
        "execution": [
            "freeze each task packet and expected-locator oracle before either arm",
            "run each arm in an isolated fresh session and clean worktree "
            "at the same SHA",
            "counterbalance ON-first and OFF-first order within every task stratum",
            "blind correctness adjudication to the arm",
            "capture only existing rollout JSONL plus the frozen oracle receipt",
        ],
        "metrics": {
            "correctness": {
                "measures": [
                    "relevant_locator_recall",
                    "false_locator_count",
                    "missed_impact_or_review_findings",
                    "acceptance",
                ],
                "historical_status": "unscored",
            },
            "time": {
                "measures": [
                    "time_to_first_correct_locator",
                    "time_to_first_edit",
                    "time_to_done",
                ]
            },
            "context": {
                "measures": [
                    "net_total_input_token_delta_on_minus_off",
                    "peak_last_input_over_context_window",
                    "compaction_count",
                    "codegraph_payload_chars",
                ]
            },
            "behavior": {
                "measures": [
                    "duplicate_payload_ratio",
                    "misroute_count",
                    "retry_count",
                    "stale_or_wrong_result_count",
                    "operator_correction_count",
                ]
            },
        },
    }


def build_report(*, sessions_root: Path, repo_marker: str) -> dict[str, Any]:
    """Parse matching sessions and return a privacy-bounded baseline report."""
    sessions_root = sessions_root.resolve()
    files = sorted(sessions_root.rglob("*.jsonl"))
    sessions: list[dict[str, Any]] = []
    files_with_read_errors = 0
    matching_repo_session_files = 0
    excluded_inherited_history_sessions = 0
    for path in files:
        try:
            session = _parse_session(
                path,
                sessions_root=sessions_root,
                repo_marker=repo_marker,
            )
        except OSError:
            files_with_read_errors += 1
            continue
        if session is not None:
            matching_repo_session_files += 1
            if session.get("_excluded_reason") == "inherited_history_session":
                excluded_inherited_history_sessions += 1
            else:
                sessions.append(session)

    strata = {
        task_class: _aggregate_with_cohorts(
            [session for session in sessions if session["task_class"] == task_class]
        )
        for task_class in TASK_CLASSES
    }
    overall = _aggregate_with_cohorts(sessions)
    scan = {
        "jsonl_files_scanned": len(files),
        "files_with_read_errors": files_with_read_errors,
        "matching_repo_session_files": matching_repo_session_files,
        "repo_sessions": len(sessions),
        "excluded_inherited_history_sessions": (excluded_inherited_history_sessions),
        "sessions_with_codegraph": overall["sessions_with_codegraph"],
        "sessions_with_codegraph_route": overall["sessions_with_codegraph_route"],
        "actual_codegraph_calls": overall["actual_codegraph_calls"],
        "invalid_json_lines": sum(
            session["invalid_json_lines"] for session in sessions
        ),
    }
    for session in sessions:
        session.pop("_output_char_samples", None)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "source": {
            "kind": "codex_rollout_jsonl",
            "repo_marker": repo_marker,
            "session_date_min": min(
                (
                    session["session_date"]
                    for session in sessions
                    if session["session_date"]
                ),
                default=None,
            ),
            "session_date_max": max(
                (
                    session["session_date"]
                    for session in sessions
                    if session["session_date"]
                ),
                default=None,
            ),
        },
        "verdict": "baseline_needed",
        "evidence_role": "observational_diagnostic_only",
        "historical_observational_baseline": {
            "scan": scan,
            "strata": strata,
            "overall": overall,
            "sessions": sessions,
            "metric_status": {
                "correctness": "unscored_without_frozen_locator_or_review_oracle",
                "time": "observed_active_task_wall_time_not_causal",
                "context": "observed_from_token_count_and_compaction_events",
                "behavior": "heuristic_proxies_not_causal",
            },
        },
        "future_paired_exploration_packet": _paired_packet(),
        "decision_rules": {
            "graph_fit_only_win": "conditional",
            "correctness_non_regressing_and_majority_context_wall_improve": "always-on",
            "no_quality_gain_and_side_effects_increase": "remove",
            "incomparable": "inconclusive",
        },
        "limitations": [
            "Historical sessions are observational and cannot establish causality.",
            "Forked rollouts are excluded because inherited transcript events "
            "are not independent session evidence; non-forked subagent sessions "
            "remain included.",
            "Task strata use first-user-message keyword heuristics.",
            "CodeGraph-first route presence is inferred from its bounded marker; "
            "MCP availability is unconfirmed without an observed call.",
            "CLI payload size is a bounded section-attribution proxy when one "
            "outer executor call batches CodeGraph with other commands.",
            "Tool duration covers MCP calls only; CLI child duration is not "
            "separately attributable from existing rollout events.",
            "Follow-up read/rg, misroute, stale/wrong, duplicate payload, and "
            "operator correction are proxies.",
            "Task completion is not treated as acceptance or correctness.",
            "Raw prompts, tool arguments, tool outputs, and absolute source paths "
            "are omitted.",
        ],
    }


def _write_json_atomic(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as stream:
        stream.write(serialized)
        temporary = Path(stream.name)
    try:
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions-root", required=True, type=Path)
    parser.add_argument("--repo-marker", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    report = build_report(
        sessions_root=args.sessions_root,
        repo_marker=args.repo_marker,
    )
    _write_json_atomic(args.output, report)
    scan = report["historical_observational_baseline"]["scan"]
    print(
        "verdict=baseline_needed "
        f"repo_sessions={scan['repo_sessions']} "
        f"codegraph_sessions={scan['sessions_with_codegraph']} "
        f"codegraph_calls={scan['actual_codegraph_calls']} "
        f"output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

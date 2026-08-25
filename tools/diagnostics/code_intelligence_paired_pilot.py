"""Run one frozen XIC exploration packet across isolated code-intelligence arms.

This is a bounded diagnostic runner, not standing telemetry. It sends the task
through stdin so long prompts never become shell command text, captures Codex
JSONL, and writes one comparable receipt per arm.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

ARM_OVERLAYS = {
    "plain": (
        "Experiment arm: plain. CodeGraph and codebase-memory are unavailable. "
        "Do not invoke either through a shell. Use rg and targeted file reads."
    ),
    "codegraph": (
        "Experiment arm: CodeGraph. Use codegraph_explore first for structural "
        "topology; use targeted file reads only for evidence the graph omitted."
    ),
    "codebase_memory": (
        "Experiment arm: codebase-memory. Use structured graph tools first and "
        "request source snippets only when the structural result needs evidence."
    ),
}


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
    ) as handle:
        handle.write(text)
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def _toml(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True)


def _codex_runtime() -> tuple[str, str, Path]:
    node = shutil.which("node")
    codex_cmd = shutil.which("codex.cmd") or shutil.which("codex")
    if not node or not codex_cmd:
        raise RuntimeError("node and codex must both be available")
    npm_bin = Path(codex_cmd).resolve().parent
    codex_js = npm_bin / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    if not codex_js.is_file():
        raise RuntimeError(f"Codex entrypoint not found below {npm_bin}")
    return node, str(codex_js), npm_bin


def _base_command(args: argparse.Namespace, last_message: Path) -> list[str]:
    node, codex_js, _ = _codex_runtime()
    return [
        node,
        codex_js,
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "-m",
        args.model,
        "-c",
        f"model_reasoning_effort={_toml(args.reasoning_effort)}",
        "--approve-for-me",
        "--json",
        "--output-schema",
        str(args.output_schema.resolve()),
        "--output-last-message",
        str(last_message.resolve()),
        "-",
    ]


def _without_path(path_value: str, excluded: Path) -> str:
    return os.pathsep.join(
        part
        for part in path_value.split(os.pathsep)
        if part and Path(part).resolve() != excluded.resolve()
    )


def _arm_command_and_env(
    arm: str, args: argparse.Namespace, last_message: Path
) -> tuple[list[str], dict[str, str]]:
    command = _base_command(args, last_message)
    env = os.environ.copy()
    _, _, npm_bin = _codex_runtime()

    if arm == "plain":
        env["PATH"] = _without_path(env.get("PATH", ""), npm_bin)
    elif arm == "codegraph":
        command[3:3] = [
            "-c",
            f"mcp_servers.codegraph.command={_toml('codegraph')}",
            "-c",
            f"mcp_servers.codegraph.args={_toml(['serve', '--mcp'])}",
        ]
    else:
        if args.cbm_command is None or args.cbm_cache_dir is None:
            raise RuntimeError(
                "codebase_memory requires --cbm-command and --cbm-cache-dir"
            )
        env["CBM_CACHE_DIR"] = str(args.cbm_cache_dir.resolve())
        command[3:3] = [
            "-c",
            f"mcp_servers.codebase_memory.command={_toml(str(args.cbm_command.resolve()))}",
        ]
    return command, env


def _cbm_preflight(args: argparse.Namespace) -> dict[str, Any] | None:
    if args.cbm_command is None or args.cbm_cache_dir is None:
        return {"status": "blocked", "reason": "cbm_command_or_cache_missing"}
    if not args.cbm_command.is_file():
        return {"status": "blocked", "reason": "cbm_command_not_found"}
    env = os.environ.copy()
    env["CBM_CACHE_DIR"] = str(args.cbm_cache_dir.resolve())
    result = subprocess.run(
        [str(args.cbm_command.resolve()), "cli", "list_projects"],
        cwd=args.repo,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
    )
    if result.returncode == 0:
        return None
    detail = (result.stderr or result.stdout).strip().splitlines()
    return {
        "status": "blocked",
        "reason": "cbm_runtime_preflight_failed",
        "exit_code": result.returncode,
        "detail": detail[-1][:500] if detail else "no diagnostic text",
    }


def _parse_events(
    stdout: str,
) -> tuple[Counter[str], Counter[str], dict[str, int], dict[str, Any]]:
    event_types: Counter[str] = Counter()
    item_types: Counter[str] = Counter()
    usage: Counter[str] = Counter()
    tool_payload_chars = 0
    mcp_tool_counts: Counter[str] = Counter()
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        event_type = str(event.get("type", "unknown"))
        event_types[event_type] += 1
        item = event.get("item")
        if event_type == "item.completed" and isinstance(item, dict):
            item_type = str(item.get("type", "unknown"))
            item_types[item_type] += 1
            if item_type == "mcp_tool_call":
                mcp_tool_counts[
                    f"{item.get('server', 'unknown')}/{item.get('tool', 'unknown')}"
                ] += 1
                tool_payload_chars += len(
                    json.dumps(item.get("result"), ensure_ascii=False, default=str)
                )
            elif item_type == "command_execution":
                tool_payload_chars += len(str(item.get("aggregated_output", "")))
        if event_type == "turn.completed" and isinstance(event.get("usage"), dict):
            for key, value in event["usage"].items():
                if isinstance(value, int):
                    usage[key] += value
    return event_types, item_types, dict(usage), {
        "tool_payload_chars": tool_payload_chars,
        "mcp_tool_counts": dict(mcp_tool_counts),
    }


def _normalize_locator_path(value: object, repo: Path) -> str:
    text = str(value or "").replace("\\", "/")
    repo_text = repo.resolve().as_posix().rstrip("/")
    if text.casefold().startswith(f"{repo_text.casefold()}/"):
        return text[len(repo_text) + 1 :]
    return text.removeprefix("./")


def _score(last_message: Path, oracle_path: Path, repo: Path) -> dict[str, Any]:
    if not last_message.is_file():
        return {"status": "unscored", "reason": "last_message_missing"}
    try:
        response = json.loads(last_message.read_text(encoding="utf-8"))
        oracle = json.loads(oracle_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {"status": "unscored", "reason": str(exc)}
    returned = response.get("locators", [])
    expected = oracle.get("expected_locators", [])
    matched: list[str] = []
    for wanted in expected:
        wanted_path = _normalize_locator_path(wanted.get("path"), repo)
        for locator in returned:
            if _normalize_locator_path(locator.get("path"), repo) != wanted_path:
                continue
            line = locator.get("line")
            if (
                isinstance(line, int)
                and wanted["line_min"] <= line <= wanted["line_max"]
            ):
                matched.append(
                    f"{wanted['path']}:{wanted['line_min']}-{wanted['line_max']}"
                )
                break
    allowed_paths = {
        _normalize_locator_path(path, repo) for path in oracle.get("allowed_paths", [])
    }
    outside = [
        locator
        for locator in returned
        if _normalize_locator_path(locator.get("path"), repo) not in allowed_paths
    ]
    recall = len(matched) / len(expected) if expected else None
    return {
        "status": "scored",
        "relevant_locator_recall": recall,
        "matched_expected_locators": matched,
        "false_locator_count": len(outside),
        "returned_locator_count": len(returned),
    }


def _run_arm(arm: str, args: argparse.Namespace, task_text: str) -> dict[str, Any]:
    arm_root = args.output_root / arm
    arm_root.mkdir(parents=True, exist_ok=True)
    if arm == "codebase_memory":
        blocked = _cbm_preflight(args)
        if blocked is not None:
            receipt = {"arm": arm, "repo_sha": args.repo_sha, **blocked}
            _atomic_write(
                arm_root / "receipt.json", json.dumps(receipt, indent=2) + "\n"
            )
            return receipt

    last_message = arm_root / "last_message.json"
    command, env = _arm_command_and_env(arm, args, last_message)
    prompt = f"{ARM_OVERLAYS[arm]}\n\n{task_text}"
    started = time.monotonic()
    result = subprocess.run(
        command,
        cwd=args.repo,
        env=env,
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=args.timeout_seconds,
        check=False,
    )
    wall_seconds = time.monotonic() - started
    _atomic_write(arm_root / "events.jsonl", result.stdout)
    _atomic_write(arm_root / "stderr.txt", result.stderr)
    event_types, item_types, usage, tool_metrics = _parse_events(result.stdout)
    receipt = {
        "arm": arm,
        "status": "completed" if result.returncode == 0 else "failed",
        "repo_sha": args.repo_sha,
        "task_id": args.task_id,
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "execution_mode": "approve-for-me_workspace-write_disposable_worktree",
        "exit_code": result.returncode,
        "wall_seconds": round(wall_seconds, 3),
        "stdout_chars": len(result.stdout),
        "stderr_chars": len(result.stderr),
        "event_types": dict(event_types),
        "item_types": dict(item_types),
        **tool_metrics,
        "usage": usage,
        "correctness": _score(last_message, args.oracle, args.repo),
    }
    _atomic_write(arm_root / "receipt.json", json.dumps(receipt, indent=2) + "\n")
    return receipt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--repo-sha", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--task-prompt", type=Path, required=True)
    parser.add_argument("--output-schema", type=Path, required=True)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--reasoning-effort", default="low")
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument(
        "--disposable-worktree",
        action="store_true",
        required=True,
        help="Acknowledge that --approve-for-me may write inside --repo.",
    )
    parser.add_argument(
        "--arms",
        nargs="+",
        choices=tuple(ARM_OVERLAYS),
        default=list(ARM_OVERLAYS),
    )
    parser.add_argument("--cbm-command", type=Path)
    parser.add_argument("--cbm-cache-dir", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.repo = args.repo.resolve()
    args.output_root = args.output_root.resolve()
    task_text = args.task_prompt.read_text(encoding="utf-8")
    receipts = [_run_arm(arm, args, task_text) for arm in args.arms]
    report = {
        "schema_version": "code_intelligence_paired_pilot_v1",
        "evidence_role": "diagnostic_only",
        "comparability": "calibration_only_single_run_per_arm",
        "repo_sha": args.repo_sha,
        "task_id": args.task_id,
        "arms": receipts,
    }
    _atomic_write(
        args.output_root / "report.json", json.dumps(report, indent=2) + "\n"
    )
    statuses_ok = all(item["status"] in {"completed", "blocked"} for item in receipts)
    return 0 if statuses_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

# ruff: noqa: E501
from __future__ import annotations

import json
import re
import sys


ZERO_TEST_PATTERNS = [
    re.compile(r"collected\s+0\s+items", re.IGNORECASE),
    re.compile(r"no\s+tests\s+ran", re.IGNORECASE),
    re.compile(r"ERROR:\s+not\s+found:", re.IGNORECASE),
]


def emit(payload: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))


def text_from_response(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(text_from_response(item) for item in value.values())
    if isinstance(value, list):
        return "\n".join(text_from_response(item) for item in value)
    return ""


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    tool_input = event.get("tool_input")
    command = ""
    if isinstance(tool_input, dict) and isinstance(tool_input.get("command"), str):
        command = str(tool_input["command"])

    combined = f"{command}\n{text_from_response(event.get('tool_response'))}"
    if "pytest" not in command.lower() or not any(
        pattern.search(combined) for pattern in ZERO_TEST_PATTERNS
    ):
        return 0

    emit(
        {
            "decision": "block",
            "reason": "Pytest collected zero tests or used an invalid node id. Fix the target and rerun a meaningful test before treating this as validation.",
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": "XIC execution gate: guessed pytest node ids that collect zero tests are not validation.",
            },
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

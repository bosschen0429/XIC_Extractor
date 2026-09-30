import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _rule_block(tokens: tuple[str, ...]) -> str:
    rules = (ROOT / ".codex" / "rules" / "xic-safety.rules").read_text(
        encoding="utf-8"
    )
    pattern = "pattern = [" + ", ".join(json.dumps(token) for token in tokens) + "]"
    matches = [
        block
        for block in re.findall(r"prefix_rule\((.*?)\n\)", rules, re.DOTALL)
        if pattern in block
    ]
    assert len(matches) == 1, f"expected one rule block for {tokens!r}"
    return matches[0]


@pytest.mark.parametrize(
    ("tokens", "decision"),
    [
        (("git", "reset", "--hard"), "forbidden"),
        (("git", "checkout", "--"), "forbidden"),
        (("git", "clean"), "prompt"),
    ],
)
def test_destructive_git_command_policy(
    tokens: tuple[str, ...], decision: str
) -> None:
    assert f'decision = "{decision}"' in _rule_block(tokens)

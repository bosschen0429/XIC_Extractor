# Codex Operating System

The repo-local agent layer keeps only XIC-specific mechanisms with a current
consumer and a concrete removal loss.

## Placement

- `AGENTS.md`: high-frequency invariants and route pointers.
- `docs/agent/`: detailed contracts loaded only by their matching route.
- `.codex/skills/`: distinctive XIC workflows with domain-specific mechanics.
- `.codex/hooks/` and `.codex/rules/`: deterministic, high-cost safety checks.

Issue scope, TaskFrame continuity, CBM structure, code behavior, and PR delivery
remain separate owners. Do not mirror one owner's contract in another layer.

## Current Hooks And Rules

| Event | Retained decision |
| --- | --- |
| `PreToolUse` | Reject unsupported wrappers and unsafe Git or RAW-background command shapes; flag execution-config verification. |
| `PostToolUse` | Reject zero-test and invalid-node pytest results. |

`.codex/rules/xic-safety.rules` owns destructive Git/filesystem command policy.
The hook fixtures and `tests/test_config_codex_hooks.py` own executable behavior.

## Adoption And Retirement

Add or retain workflow machinery only when it has both:

1. a named current consumer; and
2. a reachable loss if the mechanism is removed.

Prefer an existing Issue, tool, or owner over another checklist. Tests that only
assert the machinery exists are not consumer evidence. When the proof disappears,
delete the rule, hook, skill, helper, docs, and self-referential tests together;
Git history is the recovery path.

## Change Protocol

After changes to hooks, rules, config, skills, or agent guidance, run:

```powershell
git diff --check
.venv\Scripts\python.exe .codex\hooks\fixtures\assert_hook_outputs.py
.venv\Scripts\python.exe -m pytest tests\test_agent_sandbox_doctor.py tests\test_config_codex_hooks.py -q
```

Also validate each changed skill, run focused docs/config tests, and scan the
diff for secrets, private/absolute paths, and accidentally tracked local config.
Repository verification does not prove that the host trusted or activated the
project layer; inspect host hooks separately when activation matters.

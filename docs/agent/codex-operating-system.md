# Codex Operating System

The repo-local agent layer keeps only XIC-specific mechanisms that something
currently uses.

## Placement

- `AGENTS.md`: always-on invariants and pointers to the right document.
- `docs/agent/`: detailed rules, read when the task needs them.
- `.codex/skills/`: XIC workflows with domain-specific mechanics.
- `.codex/rules/xic-safety.rules`: destructive Git protection, covered by
  `tests/test_config_codex_rules.py`.

Environment diagnosis belongs to `scripts/agent_sandbox_doctor.py`. Host
permissions and global hooks are host-owned; a repository edit does not change
them.

## Keep Or Remove A Mechanism

A rule, hook, skill, checker or document stays only while it prevents a real,
plausible loss at a cost worth paying. Prefer guidance or an existing check for
cheap, reversible mistakes. When the loss it guards no longer exists, remove the
mechanism, its docs and its self-referential tests together; Git history is the
recovery path. Tests that only assert the mechanism exists are not a reason to
keep it.

## Verifying Agent-Layer Changes

Run `git diff --check` and scan the diff for secrets, private or absolute paths
and accidentally tracked local config. Then check by what changed:

| Changed | Check |
| --- | --- |
| Wording only | `scripts/check_repository_docs.py` and any check that reads the text |
| When a skill triggers or stops | Representative positive and negative scenarios |
| Executable rule, config, script or sandbox | The real interface and its focused tests |

For the retained rules or environment diagnostic:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_agent_sandbox_doctor.py tests\test_config_codex_rules.py -q
```

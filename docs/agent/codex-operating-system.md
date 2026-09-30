# Codex Operating System

The repo-local agent layer keeps only XIC-specific mechanisms with a current
consumer and a concrete removal loss.

## Placement

- `AGENTS.md`: high-frequency invariants and route pointers.
- `docs/agent/`: detailed contracts loaded only by their matching route.
- `.codex/skills/`: distinctive XIC workflows with domain-specific mechanics.
- `.codex/rules/`: minimal destructive Git protection.

Issue scope, TaskFrame continuity, CBM structure, code behavior, and PR delivery
remain separate owners. Do not mirror one owner's contract in another layer.
Dispatch and fan-in belong to global router skills; repo-local contracts supply
XIC technical inputs, not a second agent workflow.

## Execution Owners

Native command exit codes and existing CI report execution failures. The RAW
runner validates inputs and saves its plan during normal execution. Environment
diagnosis belongs to `scripts/agent_sandbox_doctor.py`, used after setup changes
or a concrete runtime problem.

`.codex/rules/xic-safety.rules` protects destructive Git operations; its focused
coverage lives in `tests/test_config_codex_rules.py`. Host permissions and global
hooks remain host-owned. A repository edit does not change their authorization
or activation state.

## Adoption And Retirement

Judge workflow machinery by the loss it prevents and the work it adds. A named
current consumer and reachable loss are necessary evidence, not sufficient
reasons to retain a gate. Consider how often the failure is plausible, its
consequence, false blocks, recovery effort, and ongoing maintenance; a numeric
score or separate approval packet is not required.

Use guidance or an existing check for low-cost, reversible mistakes. A blocking
mechanism earns its place when a costly failure is detected reliably and the
agent has a usable recovery path. Simplify or remove mechanisms whose friction
outweighs their protection, while preserving the underlying obligation.

Prefer an existing Issue, tool, or owner over another checklist. Tests that only
assert the machinery exists are not consumer evidence. When the proof disappears,
delete the rule, hook, skill, helper, docs, and self-referential tests together;
Git history is the recovery path.

## Change Protocol

For changes to hooks, rules, config, skills, or agent guidance, run
`git diff --check` and scan the diff for secrets, private/absolute paths, and
accidentally tracked local config. Validate each changed skill's format and
references. Select further checks by the behavior affected:

| Changed behavior | Verification |
| --- | --- |
| Wording or description with unchanged routing and permissions | Relevant docs/format/link checks; existing checks that consume the changed text. |
| Skill selection, required reads, completion, approval, or stopping boundaries | Representative positive and negative scenarios covering the changed decision; distinguish observed agent behavior from a manual walkthrough. |
| Executable rule, runtime config, script, sandbox, or subagent execution | Exercise the affected real interface and its focused runtime tests. |

For changes to the retained rules or environment diagnostic:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_agent_sandbox_doctor.py tests\test_config_codex_rules.py -q
```

Instruction changes that alter those execution contracts also require the
affected runtime checks. Pure prose changes do not require unrelated sandbox
tests. Keep method acceptance and final-SHA PR requirements with their owners.

Repository verification does not prove that the host trusted or activated the
project layer; inspect host hooks separately when activation matters.

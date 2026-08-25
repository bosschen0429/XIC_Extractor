# Codex Operating System

This repo uses a small Codex operating system: root rules, nested docs, focused
skills, minimal hooks, and explicit subagent routing. The goal is to catch
high-cost mistakes early while keeping tooling aggressive, explicit, and
decision-directed. The problem to avoid is blind ceremony, not useful tool use.

## Read Routes

| Task branch | Read |
| --- | --- |
| decide where agent guidance belongs | [Placement Model](#placement-model) |
| inspect current hooks or command rules | [Current Repo-Local Hooks](#current-repo-local-hooks) and [Current Repo-Local Rules](#current-repo-local-rules) |
| resume or hand off long-running work | [Durable Task State](#durable-task-state) |
| trace contract to enforcement | [Governance Enforcement Index](#governance-enforcement-index) |
| add, retain, or retire agent machinery | [Adoption Rules](#adoption-rules) |
| promote a repeated lesson | [Self-Improvement Rule](#self-improvement-rule) |
| change hooks, rules, or execution config | [Change Protocol](#change-protocol) and [Trust And Review](#trust-and-review) |

## Placement Model

- `AGENTS.md`: always-loaded repo invariants and hard gates only.
- `docs/agent/*.md`: nested contracts for detailed communication, execution,
  planning, validation, architecture, and public contracts.
- `.codex/skills/`: XIC-specific reusable workflows that need a checklist at
  execution time.
- Reusable lessons update the existing decision owner directly. Delivery
  evidence and chronology stay in the PR/Git history; do not create a parallel
  solution-note lane that future agents must remember to search.
- Productization state, authority, promotion, and historical compatibility route
  through [`docs/product/productization.md`](../product/productization.md).
- Active/private handoff placement routes through
  [`obsidian-handoff-contract.md`](obsidian-handoff-contract.md); PR closeout
  routes through [`communication-review.md`](communication-review.md).
- `.codex/rules/*.rules`: command-prefix policy for destructive or high-impact
  operations.
- `.codex/hooks.json` and `.codex/hooks/*.py`: low-noise lifecycle checks.
- `.codex/agents/*.toml`: project-local agents require a verified capability
  boundary that global agents, skills, owners, and bounded task packets cannot
  provide.

## Durable Task State

For long-running, cross-session, or repeatedly compacted work,
`tools.agent_task_state` owns the minimal machine-readable current state. Store
each task at ignored `output/agent-state/<task-id>/state.json`. The JSON is the
authority; active Markdown handoffs are disposable human projections and must
not be parsed to reconstruct state.

The owner records one task objective, one active node, one owner, the frozen
task/merge-base SHA, acceptance, evidence, decisions, rejected paths,
blockers/waits, external side effects, the next transition, and a monotonic
checkpoint version. Its bounded state machine is:

```text
shaped -> ready -> executing -> verifying -> reviewing -> done
              \-> waiting / needs_decision / blocked
terminal: done / superseded
```

Use it at semantic phase boundaries, not after every tool call. A resume must
provide the current task base SHA and stop on mismatch. Only the recorded owner
may advance a task; that owner may atomically transfer the next state to one
`--new-owner`. Entry into `reviewing` and `done` requires evidence. This pilot
does not schedule agents, infer state from JSONL, trigger compaction, or replace
Issues, goals, plans, PR bodies, and product/runtime state owners.

Initialize state after obtaining the task's frozen or merge-base SHA with a
separate direct Git read:

```powershell
.venv\Scripts\python.exe -m tools.agent_task_state init `
  --state output/agent-state/example/state.json `
  --task-id example --objective "Deliver the bounded slice" `
  --active-node shaping --owner main --base-sha <sha> `
  --acceptance "focused verification passes" --next "Move to ready"
```

Resume with `show`, advance with `transition`, and generate an ignored handoff
with `handoff --output docs/superpowers/handoffs/current/ACTIVE.local.md`.
All three commands require `--current-base-sha` naming that same task base; a
stale state is evidence to inspect and reconcile, not permission to overwrite
it. Ordinary commits on the task branch do not change this base.

## Current Repo-Local Hooks

`.codex/hooks.json` and the referenced hook scripts are the current hook
inventory; fixtures and `tests/test_config_codex_hooks.py` own the behavior
matrix. This owner records only the decision boundary:

| Event | Decision boundary |
| --- | --- |
| `UserPromptSubmit` | Route bounded performance, RAW-validation, productization, and stacked-PR work; reject pasted-secret shapes. Architecture preflight remains model-routed by boundary. |
| `PreToolUse` | Reject unsupported wrappers and unsafe Git/commit/RAW-background shapes; remind on execution-config verification. |
| `PostToolUse` | Reject zero-test and invalid-node pytest results. |

Hooks are guardrails, not full enforcement. Keep them deterministic, fast, and
rarely chatty. If a hook fires too often without changing behavior, remove or
narrow it.

The direct-Git boundary applies to each inner shell step, not to the number of
outer tool calls. Batch independent read steps as parallel executor calls
inside one orchestrator call, reduce their results before returning control,
and expose only bounded summaries, anomalies, counts, and locators to the main
context. Shell compound strings do not provide that output boundary.

Global skills are convenience entrypoints, not XIC authority. A conflict routes
to version-controlled repo owners and hook fixtures; global-skill maintenance is
a separate environment task.

## Current Repo-Local Rules

Rules cover command prefixes where the desired behavior is stable:

- forbid known destructive git rollback commands;
- prompt before untracked-file cleanup, recursive deletion, and
  external/background launch.

A user's explicit request authorizes in-scope push or merge work. Repo-local
execpolicy does not add a second prompt for direct `git push` or `gh pr merge`.
The Git and PR contracts still require the named request, branch verification,
checks, and closeout evidence before those actions run.

Rules apply to Codex approval policy and are evaluated from `.codex/rules/` when
the project `.codex/` layer is trusted. Restart Codex after editing rules.

## Governance Enforcement Index

This is a derived read-back index, not a second policy owner. Each canonical
owner defines the rule; the mechanism and representative case below describe
what the current repo actually enforces. A missing owner/locator, mechanism
mismatch, or behavior change fails the #160 governance read-back.

Index admission is deliberately narrower than contract authorship. Apply this
two-stage predicate whenever a new or changed contract rule is reviewed:

1. Treat the rule as a candidate only when at least one risk signal exists: it
   is independently restated across authoritative surfaces or has drifted
   between them; violation fails open across a destructive or materially
   expensive operation; or the rule protects a public or validation contract.
2. Admit the family only when all proof requirements already exist: one
   canonical owner, one bounded current repo-controlled mechanism with an
   executable locator, and one representative read-back that can distinguish
   behavior drift. A candidate without that mechanism stays in its owner and
   gets a follow-up issue; do not add an aspirational row.

Choose the mechanism from what the repo can actually decide. Use `hard_guard`
for a bounded destructive or expensive action that can be identified before it
runs; `ci_checker` for deterministic tracked state; `advisory_context` when the
repo can surface required context but cannot safely decide the action; and
`human_decision` for an owner decision that cannot be reduced to a deterministic
check. `Residual gap` is scoped to the named row and mechanism. `none` means no
known repo-side gap inside that scope; it does not claim complete contract-layer
coverage, host activation, or prevention beyond an advisory mechanism.

Worked example: the worktree no-RAW test runner was reviewed after its wording
drifted. It remains outside this index: the runner is now owned by
`parameter-settings.md`, while other occurrences are examples or pointers, and
there is no distinct bounded enforcement/read-back family to index. Reconsider
it only if a new risk signal and a current mechanism satisfy both stages above.

The five current rows satisfy the predicate for bounded reasons: docs
retirement and PR validation protect tracked contract state; destructive Git
and RAW launch guard irreversible or expensive actions; and execution-config
changes need a repo-visible advisory/read-back path even when host trust cannot
be enforced from CI.

| Rule family | Canonical owner | Mechanism | Executable locator | Representative read-back case | Residual gap |
| --- | --- | --- | --- | --- | --- |
| `docs_lifecycle_retirement` | `docs/agent/obsidian-handoff-contract.md#retirement-evidence-gate` | `ci_checker` | `tools/diagnostics/docs_placement_guard.py` | `tests/test_docs_placement_guard.py` | `none` |
| `destructive_git_filesystem` | `docs/agent/codex-operating-system.md#current-repo-local-rules` | `hard_guard` | `.codex/rules/xic-safety.rules` | `tests/test_config_codex_hooks.py` | `none` |
| `raw_background_launch` | `docs/agent/execution-gates.md#raw-and-long-runs` | `hard_guard` | `.codex/hooks/xic_pre_tool_guard.py` | `tests/test_config_codex_hooks.py` | `none` |
| `execution_affecting_config` | `docs/agent/codex-operating-system.md#change-protocol` | `advisory_context` | `.codex/hooks/xic_pre_tool_guard.py` | `tests/test_config_codex_hooks.py` | `none` |
| `pr_validation_gate` | `docs/agent/execution-gates.md#pr-verification-gate` | `ci_checker` | `tools/testing/test_shards.py` | `.github/workflows/ci.yml` | `none` |

The ordinary `docs-config` contract in `tests/test_config_codex_hooks.py`
verifies the bounded repository behavior above. Wrapper
payloads are not interpreted. Hooks also do not interpret commands constructed
dynamically inside arbitrary Python, eval, or other process-launching programs;
host sandbox enforcement and the global destructive-action contract remain
authoritative there. This read-back does not claim that CI can observe whether
a particular Codex host has trusted or activated the project layer; that
remains a host-runtime check under Trust And Review.

## Adoption Rules

Add a new rule, hook, skill, subagent, or automation only when it satisfies at
least one condition:

- it blocks a repeated high-cost failure before work starts;
- it improves reuse of an existing owner/helper;
- it closes a validation or contract decision earlier;
- it reduces noisy manual review without hiding product risk.
- it lets the agent use an official plugin, subagent, or existing tool to avoid
  serial guessing.

Do not add one just because a feature exists. Prefer improving an existing hook,
skill, or routing rule over adding a parallel one. Do use already-available
tools generously when they answer a named question.

For an external pack, map each capability to its current owner, transfer only
decision-changing deltas, and require a real XIC consumer before creating a new
repo-local owner.

A repo-local skill must own a distinct recurring XIC decision or workflow that
canonical docs and global skills do not already cover. It needs explicit
trigger/exclusion cases and observable outcome evidence; trigger accuracy alone
does not prove that the skill improves delivery. Retire the skill when it only
restates another owner, emits an ignored packet without a gate, duplicates a
global route without a distinct XIC stop rule, or an explicit effect
adjudication shows ceremony without a decision benefit. Retirement removes
routes and stale refs; move any still-valid invariant to its canonical rule or
contract owner.

Discover the current repo-local skill set from `.codex/skills`; do not maintain
a parallel keep/retire inventory here. When a skill meets a retirement trigger,
adjudicate that named skill against the criteria above and its current route,
consumer, stop, and outcome evidence.

Effect evidence is produced on demand, not by a standing telemetry harness. An
adjudication opens only when a structural ground appears, the same delivery or
review failure recurs after the route was eligible, or the operator opens an
explicit keep/retire decision. The reviewer inspects the relevant existing
issue/PR closeouts and records the eligible task, route used or bypassed,
decision changed or failure prevented, evidence locator, and observed friction.
No evidence defaults to `retain / no effect claim`; it neither validates nor
automatically retires the component, and it does not block unrelated skills.

## Self-Improvement Rule

When an agent correction, repeated failure, or review finding suggests durable
guidance, first decide the scope:

- global rule: applies across repos;
- repo root `AGENTS.md`: high-frequency XIC invariant that should influence
  every turn;
- nested `docs/agent/*.md`: detailed XIC contract that agents should load when
  relevant;
- repo skill: reusable XIC workflow with an execution checklist;
- hook/rule: deterministic early brake for repeated high-cost failure;
- no durable rule: one-off situation.

Treat a new lesson as a candidate, not an immediate repo rule. Promote it only
when the same mechanism recurs, existing issue/PR evidence already shows the
class is broader than one incident, or a deterministic high-cost or irreversible
boundary justifies prevention before recurrence. Until then, keep the evidence
on its existing issue, PR, or appropriate memory owner; do not create a candidate
registry. Promotion adds the smallest decision-changing rule to the existing
owner and removes displaced wording instead of preserving the incident story.

Before adding new guidance, search the relevant owner for an existing rule to
tighten. The permanent-root placement guard enforces the visible-word budget
owned by `tools/diagnostics/docs_policy.py`; when an addition approaches that
budget, replace displaced wording or route branch-specific detail to its nested
owner.

## Change Protocol

Changes to `.codex/config.toml`, `.codex/hooks.json`, `.codex/rules/`, hook
scripts, execpolicy, or subagent TOML are execution-affecting config changes.
Before closeout, verify the consolidated fixture behavior and focused config
tests rather than copying their individual cases into this owner:

```powershell
git diff --check
.venv\Scripts\python.exe .codex\hooks\fixtures\assert_hook_outputs.py
.venv\Scripts\python.exe -m pytest tests\test_agent_sandbox_doctor.py tests\test_config_codex_hooks.py -q
```

The fixture/test pair is the source of truth for direct, optioned, wrapped,
destructive, inert-text, and RAW-background command cases. Closeout also scans
the diff for secrets, private or absolute machine paths, and accidentally
tracked local Codex config. Report repository verification separately from host
activation.

## Trust And Review

Project-local hooks require Codex hook trust. Use `/hooks` in Codex to inspect,
review, trust, or disable changed hook definitions. New or modified hook
definitions are skipped until trusted by the runtime.

Keep repository contract and host activation as separate results:

- `repository_contract=production_candidate` only when the clean-checkout
  `docs-config` hook/config tests pass;
- `host_activation=verified` only after the current Codex runtime reports the
  project rules/hooks trusted and a real host smoke exercises them. Otherwise
  report `host_activation=not_verified`; CI must not promote this state.

## What Not To Automate Yet

- Do not auto-run 85RAW. Keep it explicit and foreground unless the user creates
  a dedicated automation.
- Do not auto-format or rewrite planning docs at stop time.
- Do not auto-create PRs, merge, push, or branch-clean without explicit user
  request.
- Do not auto-promote diagnostic-only evidence into production behavior.

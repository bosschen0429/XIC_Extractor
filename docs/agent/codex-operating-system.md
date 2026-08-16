# Codex Operating System

This repo uses a small Codex operating system: root rules, nested docs, focused
skills, minimal hooks, and explicit subagent routing. The goal is to catch
high-cost mistakes early while keeping tooling aggressive, explicit, and
decision-directed. The problem to avoid is blind ceremony, not useful tool use.

## Placement Model

- `AGENTS.md`: always-loaded repo invariants and hard gates only.
- `docs/agent/*.md`: nested contracts for detailed communication, execution,
  planning, validation, architecture, and public contracts.
- `.codex/skills/`: XIC-specific reusable workflows that need a checklist at
  execution time.
- `docs/solutions/`: searchable reusable lessons from solved XIC problems.
  Follow `docs/solutions/README.md` directly after a non-obvious fix,
  productization decision, or validation lesson; no wrapper skill is required.
- `.codex/rules/*.rules`: command-prefix policy for destructive or high-impact
  operations.
- `.codex/hooks.json` and `.codex/hooks/*.py`: low-noise lifecycle checks.
- `docs/superpowers/plans/2026-06-15-productization-control-plane.md`:
  compatibility index for stable historical productization anchors. Mutable
  lane state lives in the status index, writer authority in the authority
  manifest, readiness/promotion semantics in the product-validation contract,
  and stable capability narratives in product-topic owners.
- `docs/superpowers/handoffs/current/ACTIVE.local.md`: ignored local
  plain-language continuation summary for the active branch or PR workflow. It
  is a compact current-state snapshot, not an append-only log. Rewrite and
  prune it when status changes; use a branch-named ignored local file only when
  multiple branches need simultaneous state. Handoffs do not override the
  structured productization owners, named specs, validation notes, PR body, or
  git state. Keep them under about 200 lines unless explicitly justified.
  Completed phase history belongs in Obsidian by default.
- `docs/superpowers/handoffs/RETENTION.tsv`: zero-record compatibility sentinel
  for the handoff audit. Tracked `current/` or `archive/` files are forbidden;
  active context stays ignored and durable closeout belongs in the PR body.
- Read-side productization status snapshots are ordinary ignored reports under
  `output/` or private Obsidian notes. They do not promote tiers; promote only
  durable public summaries to `docs/superpowers/productization/evidence/`.
- `.codex/agents/*.toml`: currently empty by design. XIC review differences are
  task lenses in `docs/agent/subagent-routing.md`; add a project-local agent only
  for a verified capability boundary that global agents, skills, canonical
  docs, and bounded task packets cannot provide.
- Automations: only for recurring workspace jobs or heartbeat follow-ups that
  would otherwise be forgotten; do not turn every checklist into an automation.

## Current Repo-Local Hooks

The hook set is intentionally small:

- `UserPromptSubmit`: scans the prompt for XIC high-risk keywords and injects
  workflow context for architecture preflight, performance diagnosis, RAW
  validation, productization, or stacked/superseded-PR repair. Mixed behavior
  edits plus RAW validation retain both architecture and RAW preflights;
  synthetic/no-RAW validation and generic heartbeat/schema work stay out of the
  RAW-launch gate unless the prompt explicitly names RAW execution or acceptance.
  The hook also blocks prompt text that looks like a pasted secret. Productization
  prompts point first to
  `docs/superpowers/plans/2026-06-15-productization-control-plane.md` as a
  compatibility lookup, then route mutable lane state, writer authority, and
  readiness/promotion decisions to their separate canonical owners.
  Ordinary review, goal, handoff, closeout, and compaction prompts remain with
  their global workflows and load XIC owners only when the task actually crosses
  an XIC contract boundary.
- `PreToolUse`: rejects nested shell/command wrappers found at the bounded outer
  executable position before inspecting their payload, blocks ambiguous or
  optioned destructive Git forms, commit forms that can sweep unrelated paths,
  and background RAW launches through `Start-Process`. It adds one reminder when
  execution-affecting config changes require the smoke-and-scan contract below.
- `PostToolUse`: catches pytest runs that collected zero tests or used an invalid
  node id. Other failures and governance decisions remain visible in their
  command output or canonical owner instead of being reclassified after every
  tool call.

Hooks are guardrails, not full enforcement. Keep them deterministic, fast, and
rarely chatty. If a hook fires too often without changing behavior, remove or
narrow it.

Global skills such as `handoff` and `worktree-report` are convenience
entrypoints, not pinned XIC authority. If a global skill differs from this
version-controlled repo contract, follow this repo's `AGENTS.md`, this operating
document, the structured productization owners, and the hook fixtures. Update
the global skill later only as an environment-maintenance follow-up.

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

A repo-local skill must own a distinct recurring XIC decision or workflow that
canonical docs and global skills do not already cover. It needs explicit
trigger/exclusion cases and observable outcome evidence; trigger accuracy alone
does not prove that the skill improves delivery. Retire the skill when it only
restates another owner, emits an ignored packet without a gate, duplicates a
global route without a distinct XIC stop rule, or an explicit effect
adjudication shows ceremony without a decision benefit. Retirement removes
routes and stale refs; move any still-valid invariant to its canonical rule or
contract owner.

Structural retirement is the cheap first pass. The current five repo-local
skills were adjudicated against the three structural grounds as follows; this
is a structural retention decision, not an effectiveness claim:

| Skill | Only restates an owner | Ignored packet without a gate | Global duplicate without an XIC stop rule | Structural verdict |
| --- | --- | --- | --- | --- |
| `xic-architecture-preflight` | no; it binds one XIC semantic owner and boundary map | no; its pre-edit contract can stop mutation | no; XIC evidence-spine and authority stops are distinct | retain |
| `xic-docs-compression` | no; it composes the repo audit, absorption, referrer, and retirement gates | no; retirement requires the existing evidence/checker path | no; XIC validation and lifecycle placement are distinct | retain |
| `xic-performance-diagnosis` | no; it adjudicates XIC timing comparability and RAW/XIC call cost | no; only `optimization_ready` opens implementation | no; XIC parity, locality, and authority ceilings are distinct | retain |
| `xic-pr-stack-repair` | no; it owns the XIC stack map and clean-checkout boundary decision | no; stale or cross-ledger stacks stop for rebuild | no; XIC ledgers, retained artifacts, and validation gates are distinct | retain |
| `xic-raw-validation` | no; it binds lane-specific launch or existing-artifact acceptance | no; launch and acceptance have explicit stop gates | no; Thermo DLL, 8RAW/85RAW, and product-authority limits are distinct | retain |

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

Before adding new guidance, search the relevant owner for an existing rule to
tighten. If root `AGENTS.md` grows past about 200 lines, consolidate or move
details into nested docs alongside any additions.

## Change Protocol

Changes to `.codex/config.toml`, `.codex/hooks.json`, `.codex/rules/`, hook
scripts, execpolicy, or subagent TOML are execution-affecting config changes.
Before closeout:

```powershell
git diff --check
Get-Content .codex\hooks\fixtures\prompt_architecture.json -Raw | .venv\Scripts\python.exe .codex\hooks\xic_prompt_router.py
Get-Content .codex\hooks\fixtures\prompt_productization.json -Raw | .venv\Scripts\python.exe .codex\hooks\xic_prompt_router.py

Get-Content .codex\hooks\fixtures\posttool_zero_tests.json -Raw | .venv\Scripts\python.exe .codex\hooks\xic_post_tool_guard.py

.venv\Scripts\python.exe .codex\hooks\fixtures\assert_hook_outputs.py
.venv\Scripts\python.exe -m pytest tests\test_agent_sandbox_doctor.py tests\test_config_codex_hooks.py -q
.venv\Scripts\python.exe -m scripts.agent_sandbox_doctor --command-only --command "git -C C:\repo push origin HEAD"
.venv\Scripts\python.exe -m scripts.agent_sandbox_doctor --command-only --command "git --git-dir C:\repo\.git push origin HEAD"
.venv\Scripts\python.exe -m scripts.agent_sandbox_doctor --command-only --command "cmd /c git push origin HEAD"
codex execpolicy check --pretty --rules .codex\rules\xic-safety.rules -- git add -A
codex execpolicy check --pretty --rules .codex\rules\xic-safety.rules -- git add --all
codex execpolicy check --pretty --rules .codex\rules\xic-safety.rules -- git add .
codex execpolicy check --pretty --rules .codex\rules\xic-safety.rules -- git add README.md
codex execpolicy check --pretty --rules .codex\rules\xic-safety.rules -- git -c safe.directory=C:/repo push origin HEAD
codex execpolicy check --pretty --rules .codex\rules\xic-safety.rules -- git push
```

All `git add` checks and direct `git push` must remain unmatched by repo-local
rules; the invocation-scoped `git -c ... push` recovery remains `prompt`. The
two direct doctor checks with `-C` and `--git-dir` must report
`REWRITE_COMMAND`, matching the hook's inability to route them to an exact
prompt prefix. The nested `cmd /c` check must also report `REWRITE_COMMAND`.
Execpolicy rules match exact argument prefixes, so the pre-tool hook lets direct
`git -c ... push` reach its dedicated prompt rule and keeps direct read-only
`git -C ...` usable. Explicit `bash -c`, `pwsh -Command`, `cmd /c`, `wsl`,
`env`, `command`, and `exec` wrappers are rejected without parsing their
payload, including POSIX-assignment- or initial-call-operator-prefixed forms.
Compound, assignment-RHS, path-qualified, malformed, or unsupported direct Git
forms are also rejected and must be rewritten as one direct command. Optioned
`clone`, `fetch`, `ls-remote`, `pull`, and `push` commands that miss a prompt
prefix are rewrites in both the hook and doctor.

Git subcommands must use canonical lowercase spelling because the execpolicy
rules use exact prefixes. Wrapper words in ordinary simple-command argument or
path positions and option values are data, not wrapper executables. The bounded
locator recognizes only the outer executable, including after a POSIX
assignment prefix or initial call operator. Redirection prefixes, PowerShell
assignment RHS, later compound segments, shell control words, and dynamic
executable expressions remain outside repository wrapper parsing. Any
non-direct Git-bearing shape still fails closed.
The separate RAW hard guard still detects `Start-Process` plus a RAW signal or
propagated RAW argument variable anywhere in the same non-inert lexical segment;
it does not combine unrelated evidence across segments.
An inert text command is exempt only when it has no evaluated subexpression,
command substitution, or unquoted control structure.
Other launcher/control prefixes and dynamically constructed executables remain
outside repository parsing and under host sandbox/global safety authority.

Redirection and command substitution are ambiguous. Quoted Git text is inert
only for top-level `grep`, `findstr`, `Select-String`, `Write-Host`,
`Write-Output`, `echo`, or `printf` commands. `rg` is intentionally excluded
because its `--pre` mode can execute a command for each input path.

Also scan the diff for secrets, private local paths, absolute machine-specific
paths, and accidentally tracked local Codex config.

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

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
- `docs/superpowers/handoffs/RETENTION.tsv`: cleanup inventory for git-tracked
  handoff exceptions only. Force-added files under handoff `current/` or
  `archive/` need one row so PR closeout can distinguish active stubs, public
  evidence, Obsidian-transfer candidates, and removal candidates that still
  require exact manifest plus explicit approval.
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
- `PreToolUse`: blocks wrapped destructive Git commands, commit forms that can
  sweep unrelated paths, and background RAW launches through `Start-Process`.
  It adds one reminder when execution-affecting config changes require the
  smoke-and-scan contract below.
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
- prompt before untracked-file cleanup, recursive deletion, external/background
  launch, `git push`, and `gh pr merge`.

Rules apply to Codex approval policy and are evaluated from `.codex/rules/` when
the project `.codex/` layer is trusted. Restart Codex after editing rules.

## Governance Enforcement Index

This is a derived read-back index, not a second policy owner. Each canonical
owner defines the rule; the mechanism and representative case below describe
what the current repo actually enforces. A missing owner/locator, mechanism
mismatch, or behavior change fails the #160 governance read-back.

| Rule family | Canonical owner | Mechanism | Executable locator | Representative read-back case | Residual gap |
| --- | --- | --- | --- | --- | --- |
| `docs_lifecycle_retirement` | `docs/agent/obsidian-handoff-contract.md#retirement-evidence-gate` | `ci_checker` | `tools/diagnostics/docs_placement_guard.py` | `tests/test_docs_placement_guard.py` | `none` |
| `destructive_git_filesystem` | `docs/agent/codex-operating-system.md#current-repo-local-rules` | `hard_guard` | `.codex/rules/xic-safety.rules` | `tests/test_config_codex_hooks.py` | `none` |
| `raw_background_launch` | `docs/agent/execution-gates.md#raw-and-long-runs` | `hard_guard` | `.codex/hooks/xic_pre_tool_guard.py` | `tests/test_config_codex_hooks.py` | `none` |
| `execution_affecting_config` | `docs/agent/codex-operating-system.md#change-protocol` | `advisory_context` | `.codex/hooks/xic_pre_tool_guard.py` | `tests/test_config_codex_hooks.py` | `none` |
| `pr_validation_gate` | `docs/agent/execution-gates.md#pr-verification-gate` | `ci_checker` | `tools/testing/test_shards.py` | `.github/workflows/ci.yml` | `none` |

The repository-side hook/config gaps are closed by the ordinary `docs-config`
contract in `tests/test_config_codex_hooks.py`. This read-back does not claim
that CI can observe whether a particular Codex host has trusted or activated the
project layer; that remains a host-runtime check under Trust And Review.

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
restates another owner, has no repeated-use evidence, or adds ceremony without
reducing failures or review loops. Retirement removes routes and stale refs;
move any still-valid invariant to its canonical rule or contract owner.

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
Get-Content .codex\hooks\fixtures\prompt_architecture.json -Raw | python .codex\hooks\xic_prompt_router.py
Get-Content .codex\hooks\fixtures\prompt_productization.json -Raw | python .codex\hooks\xic_prompt_router.py

Get-Content .codex\hooks\fixtures\posttool_zero_tests.json -Raw | python .codex\hooks\xic_post_tool_guard.py

python .codex\hooks\fixtures\assert_hook_outputs.py
.venv\Scripts\python.exe -m pytest tests\test_agent_sandbox_doctor.py tests\test_config_codex_hooks.py -q
.venv\Scripts\python.exe -m scripts.agent_sandbox_doctor --command-only --command "git -C C:\repo push origin HEAD"
.venv\Scripts\python.exe -m scripts.agent_sandbox_doctor --command-only --command "git --git-dir C:\repo\.git push origin HEAD"
codex execpolicy check --pretty --rules .codex\rules\xic-safety.rules -- git add -A
codex execpolicy check --pretty --rules .codex\rules\xic-safety.rules -- git add --all
codex execpolicy check --pretty --rules .codex\rules\xic-safety.rules -- git add .
codex execpolicy check --pretty --rules .codex\rules\xic-safety.rules -- git add README.md
codex execpolicy check --pretty --rules .codex\rules\xic-safety.rules -- git -c safe.directory=C:/repo push origin HEAD
codex execpolicy check --pretty --rules .codex\rules\xic-safety.rules -- git push
```

All `git add` checks must remain unmatched by an `allow` rule; direct `git push`
and the invocation-scoped `git -c ... push` recovery must remain `prompt`. The
two doctor checks with `-C` and `--git-dir` must report `APPROVAL_REQUIRED`.
Execpolicy rules match exact argument prefixes, so the pre-tool hook lets direct
`git -c ... push` reach its dedicated prompt rule, but rejects wrapped pushes and
other optioned pushes instead of prompting every read-only `git -C ...` command.

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

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
  Use `$xic-compound` manually after a non-obvious fix, productization decision,
  or validation lesson so future agents do not rediscover the same pattern.
- `.codex/rules/*.rules`: command-prefix policy for destructive or high-impact
  operations.
- `.codex/hooks.json` and `.codex/hooks/*.py`: low-noise lifecycle checks.
- `docs/superpowers/plans/2026-06-15-productization-control-plane.md`:
  current canonical productization tier board and maintenance checklist.
  Machine-checkable contracts live in `docs/superpowers/schemas/`; short-lived
  Markdown development specs can live in `docs/superpowers/specs/`; durable
  behavior narratives belong in product docs, named plans, or active stubs.
- `docs/superpowers/handoffs/current/ACTIVE.local.md`: ignored local
  plain-language continuation summary for the active branch or PR workflow. It
  is a compact current-state snapshot, not an append-only log. Rewrite and
  prune it when status changes; use a branch-named ignored local file only when
  multiple branches need simultaneous state. Handoffs do not override the
  control plane, named specs, validation notes, PR body, or git state. Keep them
  under about 200 lines unless explicitly justified. Completed phase history
  belongs in Obsidian by default.
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
  validation, large-PR review, or stacked/superseded-PR repair. Mixed behavior
  edits plus RAW validation retain both architecture and RAW preflights;
  synthetic/no-RAW validation and generic heartbeat/schema work stay out of the
  RAW-launch gate unless the prompt explicitly names RAW execution or acceptance.
  The hook also blocks prompt text that looks like a pasted secret. Productization
  prompts inject the control-plane requirement so maturity tier claims point to
  `docs/superpowers/plans/2026-06-15-productization-control-plane.md`.
  Handoff/closeout/compaction prompts remind the agent to first resolve the
  active branch handoff, then rewrite and prune that snapshot instead of
  appending chronological notes. They must not direct every branch to the same
  productization status anchor.
- `PreToolUse`: blocks clear destructive git commands and background RAW
  launches through `Start-Process`; adds context when edits touch
  execution-affecting config, root agent contracts, or product/public surfaces
  that may need a control-plane update.
- `PostToolUse`: catches pytest runs that collected zero tests, and points the
  agent to fix the node id instead of treating the run as validation. It also
  reminds write-like product/public surface changes to update the
  productization control plane or explicitly state that no maturity tier changed.
  If product/control-plane state may have changed without a branch handoff being
  part of the update, it reminds closeout to resolve the active branch handoff
  or state why no branch handoff update is needed. It also reminds when a
  touched active handoff exceeds the 200-line target.

Hooks are guardrails, not full enforcement. Keep them deterministic, fast, and
rarely chatty. If a hook fires too often without changing behavior, remove or
narrow it.

Global skills such as `handoff` and `worktree-report` are convenience
entrypoints, not pinned XIC authority. If a global skill differs from this
version-controlled repo contract, follow this repo's `AGENTS.md`, this operating
document, the control plane, and the hook fixtures. Update the global skill
later only as an environment-maintenance follow-up.

## Current Repo-Local Rules

Rules cover command prefixes where the desired behavior is stable:

- forbid known destructive git rollback commands;
- prompt before untracked-file cleanup, recursive deletion, external/background
  launch, `git push`, and `gh pr merge`.

Rules apply to Codex approval policy and are evaluated from `.codex/rules/` when
the project `.codex/` layer is trusted. Restart Codex after editing rules.

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
Get-Content .codex\hooks\fixtures\pretool_git_reset.json -Raw | python .codex\hooks\xic_pre_tool_guard.py
Get-Content .codex\hooks\fixtures\pretool_product_surface_edit.json -Raw | python .codex\hooks\xic_pre_tool_guard.py
Get-Content .codex\hooks\fixtures\posttool_zero_tests.json -Raw | python .codex\hooks\xic_post_tool_guard.py
Get-Content .codex\hooks\fixtures\posttool_product_surface_edit.json -Raw | python .codex\hooks\xic_post_tool_guard.py
python .codex\hooks\fixtures\assert_hook_outputs.py
```

Also scan the diff for secrets, private local paths, absolute machine-specific
paths, and accidentally tracked local Codex config.

## Trust And Review

Project-local hooks require Codex hook trust. Use `/hooks` in Codex to inspect,
review, trust, or disable changed hook definitions. New or modified hook
definitions are skipped until trusted by the runtime.

## What Not To Automate Yet

- Do not auto-run 85RAW. Keep it explicit and foreground unless the user creates
  a dedicated automation.
- Do not auto-format or rewrite planning docs at stop time.
- Do not auto-create PRs, merge, push, or branch-clean without explicit user
  request.
- Do not auto-promote diagnostic-only evidence into production behavior.

# XIC Extractor Agent Contract

Repo-local rules for communication, validation, domain evidence, and code
boundaries. Global Codex rules still apply. Keep this root file small enough to
influence every turn; nested docs carry the longer contracts.

Canonical references:

These are routing targets, not a per-turn preflight checklist. Read only the
file(s) directly relevant to the current task unless a hard default below names
a required preflight.

- Directory layout and scratch hygiene: [`docs/project-layout.md`](docs/project-layout.md)
- Stable domain glossary and product lane vocabulary:
  [`docs/product/domain-glossary.md`](docs/product/domain-glossary.md)
- Python runners, Thermo RAW/DLL paths, validation tiers, and command shapes:
  [`docs/agent/parameter-settings.md`](docs/agent/parameter-settings.md)
- Known diagnostic conclusions: [`docs/diagnostic-ledger.md`](docs/diagnostic-ledger.md)
- Agent operating model, rules, hooks, skills, and automations:
  [`docs/agent/codex-operating-system.md`](docs/agent/codex-operating-system.md)
- Communication and human review surfaces:
  [`docs/agent/communication-review.md`](docs/agent/communication-review.md)
- Obsidian-backed docs retention and branch handoff stub contract:
  [`docs/agent/obsidian-handoff-contract.md`](docs/agent/obsidian-handoff-contract.md)
- Execution gates, PR gates, and local pitfalls:
  [`docs/agent/execution-gates.md`](docs/agent/execution-gates.md)
- Planning, review routing, subagents, and owner migration:
  [`docs/agent/planning-workflows.md`](docs/agent/planning-workflows.md)
- Product validation and LC-MS/MS evidence rules:
  [`docs/agent/product-validation-contract.md`](docs/agent/product-validation-contract.md)
- Product-topic source-of-truth summaries. Current first-pass topics include
  Backfill, Discovery, Alignment, and Presets, but this is an extensible layer:
  [`docs/product/README.md`](docs/product/README.md)
- Productization tier board and maintenance checklist:
  [`docs/superpowers/plans/2026-06-15-productization-control-plane.md`](docs/superpowers/plans/2026-06-15-productization-control-plane.md)
- Reusable solved-problem notes:
  [`docs/solutions/README.md`](docs/solutions/README.md)
- Architecture boundaries, CodeGraph usage, and public contracts:
  [`docs/agent/architecture-public-contracts.md`](docs/agent/architecture-public-contracts.md)
- Full LC-MS/MS domain evidence contract:
  [`docs/lc-msms-evidence-rules.md`](docs/lc-msms-evidence-rules.md)
- Full architecture and decomposition contract:
  [`docs/architecture-contract.md`](docs/architecture-contract.md)
- Repo-local XIC overlay skills: [`.codex/skills`](.codex/skills), only when
  they add an execution checklist beyond the routing docs.

## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues for this repository. See
`docs/engineering-skills/issue-tracker.md`.

### Triage labels

The repo uses the default five-label triage vocabulary. See
`docs/engineering-skills/triage-labels.md`.

### Domain docs

The repo uses a single-context domain layout with explicit shared, targeted,
and untargeted product lanes. See `docs/engineering-skills/domain.md`.

## Hard Defaults

- Start non-trivial wrap-ups with the current verdict: done, blocked, residual
  risk, and next recommended step.
- Before non-trivial edits, confirm intended worktree, branch, and dirty diff
  scope. Do not stage, rewrite, or revert unrelated user changes.
- Before Python, RAW, DLL, or validation commands, read
  `docs/agent/parameter-settings.md` and use documented runners and paths.
- Keep outputs under task-specific `output/` or `docs/superpowers/` paths. New
  diagnostic output groups need a summary or index.
- Keep active handoffs as short current-state snapshots, not logs. Use the
  ignored local `docs/superpowers/handoffs/current/ACTIVE.local.md` by default,
  move long completed history to Obsidian, and prune around the 200-line target
  before substantial continuation.
- For non-trivial branch closeout, condense the current handoff into the PR body
  as the durable closeout surface. Add a repo closeout summary under
  `docs/superpowers/closeouts/` only when the completed phase is intentionally
  public repo evidence for
  public-contract, docs-governance, validation-policy, artifact-retention, broad
  public-surface, or approved deletion work.
- Treat repo docs as the public source-of-truth surface and Obsidian or ignored
  artifacts as the private lab notebook. Public contracts, product authority,
  validation policy, and future-agent rules stay in canonical repo owners; long
  development diary, command transcript, and private/local context do not.
- Active execution plans cannot be Obsidian-only. Keep a short repo stub with
  objective, scope, constraints, next 1-3 actions, verification, and stop rule;
  Obsidian can only provide optional private depth.
- New or risky repo docs outside canonical owner paths need `Doc placement:` and
  `Repo owner:` before commit. Private diary, command log, review rationale, and
  branch sequencing go to Obsidian staged draft or ignored storage.
- For docs-heavy work, use explicit `git add` and inspect staged diff; do not use
  `git commit -a`, `git commit --all`, `git commit -am`, or pathspec commits.
- For tracked docs moving toward Obsidian, first formalize stable claims in a
  repo owner or keep a same-path sanitized stub. Do not `git rm`, archive-move,
  or delete tracked docs without explicit approval after a referrer scan.
- State validation status explicitly: `diagnostic_only`, `shadow_ready`,
  `production_candidate`, `production_ready`, or `inconclusive`.
- Tests passing is not production readiness. For extraction, alignment, scoring,
  and matrix behavior changes, report which method-appropriate evidence was
  used: synthetic tests, lane-specified 8RAW/85RAW, targeted benchmark,
  method-specific real samples, or manual EIC/MS2 review.
- Do not launch 85RAW or likely long RAW runs through background
  `Start-Process` from the Codex shell. Use the foreground heartbeat/timing
  command shapes in `docs/agent/parameter-settings.md`, or get explicit approval
  for an external terminal or automation.
- Search `tools/diagnostics/INDEX.md`, relevant notes, and existing validation
  outputs before inventing a new diagnostic workflow. Search
  `docs/diagnostic-ledger.md` before rerunning known targets or failure modes.
- Use tools aggressively when they reduce uncertainty, parallelize review,
  expose evidence, or close a product decision. Token/cost minimization is not
  the primary objective; avoiding blind, decision-free tool loops is.
- Prefer the simplest rule, implementation, and validation path that preserves
  the same product safety and evidence. Added filters, abstractions, test
  shards, subagents, plugins, or long runs must name the product evidence or
  decision they add; dataset-specific slices are staging evidence, not durable
  product policy.
- Before adding a new validation, fixture, retention, diagnostic,
  productization, or artifact-index workflow, search for an existing owner of
  the same responsibility. If a wheel exists, deepen and reuse that module
  before creating a parallel workflow; a new sibling workflow must state why the
  existing interface is wrong for this case.
- Treat the third instance of the same mechanical responsibility as a refactor
  trigger. Shared mechanics such as artifact path resolution, inventory parsing,
  hashing, externalized-artifact checks, schema checks, replay checks, and stale
  result detection should move behind a small reusable interface while
  phase-specific policy and domain authority statements stay local.
- Treat `.codex/config.toml`, `.codex/hooks.json`, `.codex/rules/`, hook
  scripts, execpolicy, and subagent TOML as execution-affecting config. Changes
  need docs/handoff review, smoke check, and secret/local-path scan.
- When a repeated agent failure suggests new guidance, decide whether the fix
  belongs in global rules, this repo, a skill, a hook, or nowhere. Prefer
  tightening an existing rule over adding another root bullet.

## PR Gate

Before opening, updating, or marking a PR ready, run the CI-equivalent commands
from `.github/workflows/ci.yml` in the current worktree:

CI is layered: pull requests run the required Python 3.12 shard matrix, while
pushes to `main` or `master` also run the Python 3.11 compatibility matrix. The
local PR closeout gate below mirrors the command surface; it is not a request to
rerun every GitHub matrix job locally.
Hosted lint/typecheck jobs use the GitHub uv cache keyed by `pyproject.toml` and
`uv.lock`; self-hosted shard jobs record install/test seconds in the job summary
so future CI tuning is based on observed queue, install, and shard timing.

```powershell
$env:UV_CACHE_DIR='.uv-cache'; uv run ruff check xic_extractor tests
$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_diagnostics_index.py
$env:UV_CACHE_DIR='.uv-cache'; uv run python tools/diagnostics/docs_management_audit.py --repo-only --fail-on-completed-transient --json
$env:UV_CACHE_DIR='.uv-cache'; uv run mypy xic_extractor
$env:UV_CACHE_DIR='.uv-cache'; uv run python -m tools.testing.test_shards --check
$env:UV_CACHE_DIR='.uv-cache'; uv run python -m tools.testing.test_shards docs-config -- -v --tb=short -x
$env:UV_CACHE_DIR='.uv-cache'; uv run python -m tools.testing.test_shards gui -- -v --tb=short -x
$env:UV_CACHE_DIR='.uv-cache'; uv run python -m tools.testing.test_shards targeted-core -- -v --tb=short -x
$env:UV_CACHE_DIR='.uv-cache'; uv run python -m tools.testing.test_shards alignment-core -- -v --tb=short -x
$env:UV_CACHE_DIR='.uv-cache'; uv run python -m tools.testing.test_shards product-gates-activation -- -v --tb=short -x
$env:UV_CACHE_DIR='.uv-cache'; uv run python -m tools.testing.test_shards product-gates-evidence -- -v --tb=short -x
$env:UV_CACHE_DIR='.uv-cache'; uv run python -m tools.testing.test_shards diagnostics-tools -- -v --tb=short -x
```

If a command fails because of real lint/type/test errors, fix the root cause and
rerun the failed gate. If it fails only because sandbox blocks dependency
resolution, executable spawn, or DLL loading, rerun the same command with
approval instead of substituting a narrower check.
Do not replace the shard gate with a monolithic full-suite pytest run unless
debugging the shard runner itself.

## Product North Star

- XIC Extractor is an LC-MS evidence and model-selection system, not a
  single-dataset or CID-NL-only pipeline.
- 8RAW and 85RAW are validation fixtures and stress oracles, not architecture
  boundaries.
- Different Discovery methods require different validation datasets. Do not
  apply CID-NL 8RAW/85RAW fixtures as the default science gate for Pt pattern or
  other evidence providers unless that lane explicitly names them.
- CID-NL, HCD-PI, Delta Mass, RT/iRT, MS1 pattern, shape, standards, library
  matches, and future learned models are evidence providers. They feed
  `EvidenceVector`, `PeakHypothesis`, model selection, and `AuditTrail`; they
  must not directly become permanent matrix-writing authority without an
  explicit activation/export contract.
- Diagnostic TSVs, shadow reports, wrappers, and sidecars prove observability,
  not product usability.
- Product rules must be short, human-explainable, and domain-meaningful. If a
  rule reads like nested dataset-specific qualifiers, treat it as a temporary
  validation slice until it is collapsed into a simpler gate or killed.
- Public safety rules apply whenever selected peak, selected area, confidence,
  reason, matrix identity, workbook schema, TSV schema, or config behavior
  would change: require expected-diff contract and focused output tests.

## Architecture Guardrails

- Preserve dependency direction. Domain logic must not import GUI, workbook
  builders, CLI scripts, process backends, report renderers, or RAW/CSV
  adapters.
- Keep public entry points and compatibility facades thin while moving behavior
  into focused modules.
- Treat `tools/diagnostics/` as maintained product-adjacent code. CLIs
  orchestrate; reusable loading, classification, models, summaries, plotting,
  and writers belong in package modules.
- Diagnostic writers render only. They must not recompute domain evidence or
  re-scan RAW files.
- Move behavior before changing behavior. Add characterization tests before
  moving uncovered behavior.

## Workflow Routing

- Use a goal-shaped contract for long-running, multi-step, cross-turn, or
  repeatedly drifting work. It must name one objective, context, constraints,
  verification, done condition, stop rule, and handoff expectation. Active
  runtime goals still require explicit user request.
- Before non-trivial diagnostics, RAW-backed evidence, matrix activation, or
  evidence-provider work, use `xic-architecture-preflight` and name owner/helper
  reuse, call-cost model, public contract risk, validation gate, and stop rule.
- Before XIC preset/runtime performance work, use `xic-performance-diagnosis` to
  inspect timing artifacts, rank bottlenecks, classify call cost and workload,
  and define correctness gates; then use `xic-architecture-preflight` before code
  edits. This does not replace `xic-raw-validation` before launching or accepting
  RAW-backed validation.
- For broad audits, workflow-rule changes, structural questions, PR/CI work, or
  requested review gates, use the relevant available capabilities instead of
  self-limiting: repo skills, subagents, CodeGraph, GitHub/gh, diagnostics
  indexes, official docs/search, and focused real-data validation where they
  directly improve the decision.
- For large XIC PR review, use `xic-large-pr-review` and review by blast radius:
  shared helpers, public contracts, diagnostics boundaries, RAW locality,
  product-vs-diagnostic claims, then parity evidence. Treat an inline review
  comment as one observed instance, not the full scope: when it implicates a
  shared owner, default, invariant, provenance chain, writer/loader pair, or
  fallback policy, sweep all callsites and sibling boundaries before
  re-delivery or resolution. A second same-class comment on one PR stops
  patch-by-patch handling and triggers a fresh whole-PR class sweep.
- Repo-local execution subagents are opt-in. The main agent owns synthesis,
  edits, final judgment, and verification. Use
  `docs/agent/subagent-routing.md` when subagents are requested.

## Public Contracts

Treat these as public unless an approved plan explicitly changes them:

- CLI commands under `scripts/`
- `xic_extractor.extractor.run`
- `xic_extractor.signal_processing.find_peak_and_area`
- `scripts.csv_to_excel.run`
- config keys and example/default settings
- CSV schemas
- workbook sheet names, order, hidden states, and columns
- HTML report path naming
- run metadata keys

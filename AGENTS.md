# XIC Extractor Agent Contract

Repo-local rules for communication, validation, domain evidence, and code
boundaries. Global Codex rules still apply. Keep this root file small enough to
influence every turn; nested docs carry the longer contracts.

## Task Routes

These are routing targets, not a per-turn checklist. Read only the owner needed
for the active task unless a hard gate below says otherwise.

- Python, Thermo RAW/DLL, validation tiers, and command shapes:
  [`parameter-settings.md`](docs/agent/parameter-settings.md)
- Product vocabulary and topic authority:
  [`domain-glossary.md`](docs/product/domain-glossary.md) and
  [`docs/product/README.md`](docs/product/README.md)
- Evidence and validation policy:
  [`product-validation-contract.md`](docs/agent/product-validation-contract.md)
  and [`lc-msms-evidence-rules.md`](docs/lc-msms-evidence-rules.md)
- Architecture and public contracts:
  [`architecture-public-contracts.md`](docs/agent/architecture-public-contracts.md)
  and [`architecture-contract.md`](docs/architecture-contract.md)
- Execution, PR, planning, review, and handoff mechanics:
  [`execution-gates.md`](docs/agent/execution-gates.md),
  [`planning-workflows.md`](docs/agent/planning-workflows.md),
  [`communication-review.md`](docs/agent/communication-review.md), and
  [`obsidian-handoff-contract.md`](docs/agent/obsidian-handoff-contract.md)
- Existing diagnostics and solved patterns:
  [`diagnostic-ledger.md`](docs/diagnostic-ledger.md),
  [`tools/diagnostics/INDEX.md`](tools/diagnostics/INDEX.md), and
  [`docs/solutions/README.md`](docs/solutions/README.md)
- Productization tiers:
  [`2026-06-15-productization-control-plane.md`](docs/superpowers/plans/2026-06-15-productization-control-plane.md)
- Repo layout, agent system, engineering conventions, and opt-in XIC skills:
  [`project-layout.md`](docs/project-layout.md),
  [`codex-operating-system.md`](docs/agent/codex-operating-system.md),
  `docs/engineering-skills/`, and [`.codex/skills`](.codex/skills)

## Hard Defaults

- Before non-trivial edits, confirm intended worktree, branch, and dirty diff
  scope. Do not stage, rewrite, or revert unrelated user changes.
- In a newly created worktree, run the bootstrap command in
  `docs/agent/parameter-settings.md` before the first project `uv run`. Before
  PR verification, run its offline `--check`; do not infer missing packages
  from a different worktree's ignored `.venv`.
- Before Python, RAW, DLL, or validation commands, read
  `docs/agent/parameter-settings.md` and use documented runners and paths.
- Keep outputs under task-specific `output/` or `docs/superpowers/` paths. New
  diagnostic output groups need a summary or index.
- Keep active plans and handoffs as short, self-contained current-state stubs;
  use the PR body for normal durable closeout. Detailed retention and Obsidian
  routing live in the handoff owners above.
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
- Treat `.codex/config.toml`, `.codex/hooks.json`, `.codex/rules/`, hook
  scripts, execpolicy, and subagent TOML as execution-affecting config. Changes
  follow the smoke and scan contract in `docs/agent/codex-operating-system.md`.

## PR Gate

Before opening a PR, changing its remote head, or marking it ready, follow the
canonical [PR Verification Gate](docs/agent/execution-gates.md#pr-verification-gate).
The root contract intentionally does not mirror its command list. Default CI
must remain clean-checkout safe.

## Product North Star

- XIC Extractor is an LC-MS evidence and model-selection system, not a
  single-dataset or CID-NL-only pipeline.
- 8RAW and 85RAW are lane-specific validation fixtures and stress oracles, not
  architecture boundaries or universal Discovery gates. Pt pattern and other
  methods use the evidence named by their own lane.
- CID-NL, HCD-PI, Delta Mass, RT/iRT, MS1 pattern, shape, standards, library
  matches, and future learned models are evidence providers. They feed
  `EvidenceVector`, `PeakHypothesis`, model selection, and `AuditTrail`; they
  must not directly become permanent matrix-writing authority without an
  explicit activation/export contract.
- Product rules must be short, human-explainable, and domain-meaningful. If a
  rule reads like nested dataset-specific qualifiers, treat it as a temporary
  validation slice until it is collapsed into a simpler gate or killed.

## Architecture Guardrails

- Preserve dependency direction. Domain logic must not import GUI, workbook
  builders, CLI scripts, process backends, report renderers, or RAW/CSV
  adapters.
- Treat `tools/diagnostics/` as maintained product-adjacent code. CLIs
  orchestrate; reusable loading, classification, models, summaries, plotting,
  and writers belong in package modules.
- Diagnostic writers render only. They must not recompute domain evidence or
  re-scan RAW files.

## Workflow Routing

- Before non-trivial diagnostics, RAW-backed evidence, matrix activation, or
  evidence-provider work, use `xic-architecture-preflight` and name owner/helper
  reuse, call-cost model, public contract risk, validation gate, and stop rule.
- Before XIC preset/runtime performance work, use `xic-performance-diagnosis` to
  inspect timing artifacts, rank bottlenecks, classify call cost and workload,
  and define correctness gates; then use `xic-architecture-preflight` before code
  edits. This does not replace `xic-raw-validation` before launching or accepting
  RAW-backed validation.
- For large XIC PRs, use `xic-large-pr-review`. It owns blast-radius expansion,
  reviewer-finding adjudication, same-class sweeps, and thread-resolution gates.

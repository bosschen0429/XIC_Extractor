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
- Productization capabilities, mutable state, writer authority, and promotion:
  [`productization.md`](docs/product/productization.md),
  [`productization_status_index_v1.tsv`](docs/superpowers/validation/productization_status_index_v1.tsv),
  [`productization_authority_manifest.v1.json`](docs/superpowers/schemas/productization_authority_manifest.v1.json), and
  [`product-validation-contract.md`](docs/agent/product-validation-contract.md)
- Historical productization anchor lookup only:
  [`2026-06-15-productization-control-plane.md`](docs/superpowers/plans/2026-06-15-productization-control-plane.md)
- Repo layout, agent system, engineering conventions, and opt-in XIC skills:
  [`project-layout.md`](docs/project-layout.md),
  [`codex-operating-system.md`](docs/agent/codex-operating-system.md),
  `docs/engineering-skills/`, and [`.codex/skills`](.codex/skills)

## Agent skills

### Issue tracker

Issues and PRDs are tracked in this repository's GitHub Issues. See
`docs/agents/issue-tracker.md`.

### Triage labels

Use the five default Matt Pocock triage labels. See
`docs/agents/triage-labels.md`.

### Domain docs

Use the single-context domain documentation layout. See
`docs/agents/domain.md`.

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
- State validation evidence status explicitly using the canonical Evidence
  labels in `docs/product/README.md`.
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
- External structural-context tools are optional reconnaissance. Use one only
  when the current checkout has a healthy matching index; otherwise fall back
  to `rg` and targeted reads. CI, validation, and correctness claims must not
  depend on an external local index.

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
- Runtime consumers submit typed work/resource/artifact/acquisition receipts and
  external telemetry samples to `execution_runtime_owner`; only that owner may
  construct lifecycle, observability, or finalization projections. Adapters must
  enter through its atomic execution/finalization API, not call partial lifecycle
  primitives, accept caller-authored ledgers/counts, or expose settlement-only
  public bypasses. See `docs/agent/architecture-public-contracts.md`.
- Treat persisted JSON/TSV/maps as untrusted projections after deserialization.
  Before a consumer claims domain or runtime semantics, route the complete content
  through the original semantic owner to reconstruct the typed value and recompute
  canonical identity and invariants. Embedded IDs, hashes, status, counts, and
  locators are assertions, not proof, even when the outer envelope is re-sealed;
  omitted content makes the corresponding claim unavailable. See
  `docs/agent/architecture-public-contracts.md#persisted-semantic-rehydration`.

## Workflow Routing

- Before activating a `needs-info` issue, transforming a legacy owner, or
  resuming after a load-bearing premise correction, follow
  `docs/agent/planning-workflows.md#needs-info-activation-and-premise-compilation`.
  Observed legacy behavior is evidence, not a frozen invariant; compile one
  current packet and invalidate its displaced assumptions before production
  edits resume.
- Before non-trivial diagnostics, RAW-backed evidence, matrix activation, or
  evidence-provider work, use `xic-architecture-preflight` and name owner/helper
  reuse, call-cost model, public contract risk, validation gate, and stop rule.
- Before XIC preset/runtime performance work, use `xic-performance-diagnosis` to
  inspect timing artifacts, rank bottlenecks, classify call cost and workload,
  and define correctness gates; then use `xic-architecture-preflight` before code
  edits. This does not replace `xic-raw-validation` before launching or accepting
  RAW-backed validation.
- Review comments are failure instances, not the fix scope. A second actionable
  finding from the same class, or actionable findings on two delivered heads,
  stops patch-by-patch push/reply/resolve work. Classify the root as a PRD/spec
  gap, unbounded design, implementation regression, oracle gap, or unsupported
  reviewer claim; search prior owner findings and tests before editing. If the
  failure universe cannot be bounded, shrink, delete, replace, or redesign the
  mechanism instead of adding another special case. If a product/domain choice
  was never specified, refreeze the PRD rather than encoding a reviewer guess.

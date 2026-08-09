# XIC Agent Router Overlay

The global Codex Agent Router and global workflow skills own generic agent
selection, dispatch, concurrency, model choice, budgets, execution roles,
review loops, and native output contracts. This file adds only XIC-specific
review lenses, hard triggers, and evidence boundaries. It is not a second agent
router and does not own installation or lifecycle policy for external tools.

Stable runners, RAW paths, validation tiers, and command shapes live in
`docs/agent/parameter-settings.md`. Architecture and public-output boundaries
live in `docs/agent/architecture-public-contracts.md`. An unavailable global
review route may be replaced only by a host-provided read-only reviewer.
Otherwise report the unavailable route and do not claim an independent review;
this repo does not define a generic-agent fallback.

## Active Review Lenses

These are XIC-specific prompt overlays, not agent identities. The selected
global agent or skill keeps its native output contract.

| Lens | Use when | XIC-specific focus |
| --- | --- | --- |
| `framing / strategy` | High-risk plan/spec, phase design, handoff productization, or legacy-path concern | Load-bearing premise, product direction, exit rule, and whether phase placement is being mistaken for domain ownership |
| `implementation / public contract` | Code/public contract, CLI/config/schema/parser/test behavior, or diagnostic entrypoint change | Executable call path, schema enforcement, consumer compatibility, diagnostics reuse, and test quality |
| `validation / science evidence` | RAW/science/benchmark decision, 8RAW/85RAW preflight or acceptance, or timing/performance evidence | Method-appropriate oracle, run/gate versus maturity status, LC-MS/MS evidence, performance evidence, and authority boundary |
| `docs / handoff` | Docs/source-of-truth, output handoff, report/review surface, or agent-contract change | Canonical wording, placement, privacy, human review UX, trigger drift, and source-of-truth conflicts |

Task packets add only the relevant lens and XIC constraints. They must name the
decision under review, exact worktree or artifact, evidence ceiling, stop rule,
and any public or scientific authority boundary. Reviewer findings are leads,
not design authority; distinguish observed facts, inference, owner decisions,
and unknowns when that distinction changes the next action.

## Hard Triggers

- Any 85RAW run, likely longer-than-30-minute RAW run, or
  production-equivalent validation must use `xic-raw-validation`. It requires
  an independent `validation / science evidence` preflight before launch and
  acceptance review after completion, or an explicit bypass reason.
- Any CI red or required-check uncertainty starts with the available global CI
  workflow. Use `docs/agent/parameter-settings.md` for XIC runner, path,
  PowerShell, RAW, and DLL facts.
- Any project-agent config, this file, or root `AGENTS.md` workflow-rule change
  needs an independent `docs / handoff` review for capability duplication,
  trigger drift, and output-contract regression.
- Any phase plan that could preserve a bad legacy path, overclaim evidence, or
  add an expensive gate uses the global critical-artifact review workflow with
  the `framing / strategy` lens.
- Any public CLI/config/schema/workbook/TSV/downstream handoff change gets an
  independent fixed-diff review with the `implementation / public contract`
  lens.
- Any diff that adds or changes broad exception handling, fallback/default
  behavior, fail-open/fail-closed gates, missing-artifact behavior, or
  user-facing errors adds a silent-failure check to that fixed-diff review.
- Any diff that adds or materially changes dataclasses, protocols, schema
  models, domain DTOs, public contract rows, or invariant-bearing state adds a
  type-invariant check.
- Any behavior, parser/writer, diagnostic, public output, matrix authority, or
  validation-acceptance change adds a test-quality check or independent
  behavioral verification.
- Any review finding that implicates a shared owner, invariant, provenance
  chain, writer/loader pair, fallback policy, or interacting hook predicate
  expands to its live callsites and sibling boundaries before another patch.
  Classify the failure as a spec gap, unbounded design, implementation
  regression, oracle gap, or unsupported reviewer claim; redesign or delete
  when the failure universe cannot be bounded.
- Long-running, multi-step, cross-turn, or repeatedly drifting work uses the
  global goal workflow. Register an active goal only when the user explicitly
  requests one or the runtime explicitly permits it.

For `validation / science evidence`, name at most two modes from `preflight`,
`acceptance`, `science`, and `performance`.

## XIC Route Map

| Situation | Global route plus XIC overlay |
| --- | --- |
| Non-trivial diagnostic, Matrix/activation, or evidence-provider work | `xic-architecture-preflight`; name owner/helper reuse, call-cost, boundary projections, authority, validation gate, and stop rule |
| XIC preset/runtime performance work | `xic-performance-diagnosis`, then `xic-architecture-preflight` before code edits |
| Expensive RAW launch or existing RAW acceptance | `xic-raw-validation` plus `validation / science evidence` |
| Public contract implementation | Fixed-diff review plus `implementation / public contract` |
| Workflow, AGENTS, hook, or routing change | `docs / handoff`; add `framing / strategy` only when phase or gate behavior changes |
| PR stack repair or retargeting | `xic-pr-stack-repair` before mutations |

Execution and QA agents receive explicit non-overlapping scopes. Scientific or
public authority stays with the canonical XIC owner; no agent role, diagnostic,
sidecar, or review report grants it.

## Acceptance Labels

Validation summaries report two separate axes:

- **Run/gate outcome**: `run_ok` means the command completed and emitted the
  expected artifacts; `gate_ok` means those artifacts satisfy the stated gate.
- **Validation evidence status**: use the canonical Evidence labels in
  `docs/product/README.md`.

A result may be both `run_ok` and `gate_ok` while remaining
`diagnostic_only`. Neither axis grants counted-detection, selected-value,
Matrix, workbook, activation, or export authority without the relevant
explicit contract. For `inconclusive`, name the missing evidence or exit rule.

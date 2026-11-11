# XIC Extractor Agent Contract

XIC-specific task routing and invariants; global rules own general defaults.

## Where To Look

| Task | Read |
| --- | --- |
| Python runner, worktree setup, RAW/DLL, validation scale, environment failure | [`local-execution.md`](docs/agent/local-execution.md) |
| Product terminology | [`domain-glossary.md`](docs/product/domain-glossary.md) |
| Product behavior or method evidence | [`docs/product/README.md`](docs/product/README.md) |
| Readiness claim, scientific decision, or change to published values | [`product-validation-contract.md`](docs/agent/product-validation-contract.md) and [Writer Authority](docs/product/decision-policy.md#writer-authority) |
| Choosing, adding or running tests (local default: `python -m tools.testing.test_selection`); UI demonstration | [`testing.md`](docs/agent/testing.md) |
| Owner boundary, persisted schema, runtime lifecycle, write authority, public contract | [`architecture-contract.md`](docs/architecture-contract.md) |
| UI information hierarchy, interactions, wording, styling or theme roles | [`ui-design.md`](docs/ui-design.md) |
| Imports in startup-sensitive or lightweight readers | [Dependency Direction](docs/architecture-contract.md#dependency-direction) |
| Issue scope, implementation completion, push, PR, review, long RAW run | [`delivery.md`](docs/agent/delivery.md) |
| Current-format Untargeted output as test input | Regenerate with `.venv\Scripts\python.exe -m scripts.run_tissue_validation --dataset 8`; older runs under `output/` are not current-format input |
| Scripts and diagnostics | [`script-tool-lifecycle.md`](docs/agent/script-tool-lifecycle.md) to add or retire one; [`INDEX.md`](tools/diagnostics/INDEX.md) to pick one; [`diagnostic-ledger.md`](docs/agent/diagnostic-ledger.md) before repeating evidence |
| Docs, skills, hooks, rules or agent config | [`codex-operating-system.md`](docs/agent/codex-operating-system.md) |
| GitHub issue mechanics or triage | [`issue-tracker.md`](docs/agent/issue-tracker.md) and [`triage-labels.md`](docs/agent/triage-labels.md) |
| File placement, tracking, where a document belongs | [`project-layout.md`](docs/project-layout.md) |

## Always On

- **Single-case data checks:** read the sample/XIC settings and original data
  first. Answer and stop once the evidence resolves the question; trace code or
  widen validation only for a concrete contradiction or requested fix.
  ND/rejection is a program decision, not proof of absent signal.
- **Code navigation:** for code-level questions or changes, use Codebase Memory
  (CBM) for callers, dependencies and impact once its index matches this checkout;
  read current source for exact text and negative claims.
- **Core model:** one concept, one representation, one source. Name the concept
  a new class, column or value belongs to before adding it; see
  [Core Model](docs/architecture-contract.md#core-model) and
  [Change Locality](docs/architecture-contract.md#change-locality).
- **Ownership:** this codebase is yours: what it's for and how it's built are
  your call, and you'll keep building on it. Merge duplication and remove what a
  change replaces in the same delivery; add no feature, abstraction, defensive
  branch or document the task does not need.
- **Domain purity:** core algorithms work on in-memory domain models. Never mix
  RAW/DLL I/O, multiprocessing, byte hashing or output formatting into
  mathematical or alignment algorithms.
- **Batch cost:** prepare shared inputs outside loops; measure call counts before
  scaling. Deep diagnostics, full re-validation and audits run on demand unless
  no cheaper check can decide this output's correctness.
- **Current state only:** code, comments, tests and docs describe current
  behavior and non-obvious constraints. History stays in Git and PRs.
- **Module naming:** name modules for their capability, following
  [Module Naming](docs/architecture-contract.md#module-naming).
- **Runtime:** use this worktree's documented Python runner. Run output stays in
  ignored `output/`; a tracked artifact needs a current consumer.
- **Performance:** use `xic-performance-diagnosis` for timing, locality or
  performance comparisons.
- **Evidence:** report the Evidence label and actual oracle. Passing tests prove
  regression coverage, not scientific or production readiness.
- **Merge:** acceptance evidence and hosted checks on the final remote SHA
  must both be satisfied; see [Acceptance Review](docs/agent/delivery.md#acceptance-review).

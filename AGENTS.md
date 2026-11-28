# XIC Extractor Agent Contract

XIC-specific task routing and invariants; global rules own general defaults.

## Where To Look

| Task | Read |
| --- | --- |
| Python runner, worktree setup, RAW/DLL, validation scale, environment failure | [`local-execution.md`](docs/agent/local-execution.md) |
| Product terminology | [`domain-glossary.md`](docs/product/domain-glossary.md) |
| Product behavior or method evidence | [`docs/product/README.md`](docs/product/README.md) |
| Readiness claim, scientific decision, public contract or change to published values | [`product-validation-contract.md`](docs/agent/product-validation-contract.md) and [Writer Authority](docs/product/decision-policy.md#writer-authority) |
| Choosing, adding or running tests; UI demonstration | [`testing.md`](docs/agent/testing.md) |
| Which module owns a behavior | [`module-ownership.md`](docs/architecture/module-ownership.md) |
| Scheduling, workers, pools or timing | [`runtime-execution.md`](docs/architecture/runtime-execution.md) |
| Workbench/Inspector backend: job replies, review sources and downloads | [Workbench contract](docs/product/workbench.md#boundaries) and [loopback HTTP harness](tests/workbench_http.py) |
| UI information hierarchy, interactions, wording, styling or theme roles | [`ui-design.md`](docs/ui-design.md) |
| Imports in startup-sensitive or lightweight readers | [`dependency-direction.md`](docs/architecture/dependency-direction.md) |
| Issue scope, implementation completion, push, PR or review | [`delivery.md`](docs/agent/delivery.md) |
| Untargeted output as test input | [Replay only the stage you changed](docs/agent/local-execution.md#replay-only-the-stage-you-changed) from a capture whose upstream stages match current code; regenerate with the [8RAW runner](docs/agent/local-execution.md#validation-scale) only for what no replay covers. Other runs under `output/` are not current-format input |
| Scripts and diagnostics | [`script-tool-lifecycle.md`](docs/agent/script-tool-lifecycle.md) to add or retire one; [`INDEX.md`](tools/diagnostics/INDEX.md) to pick one; [`diagnostic-ledger.md`](docs/agent/diagnostic-ledger.md) before repeating evidence |
| Docs, skills, hooks, rules or agent config | [`codex-operating-system.md`](docs/agent/codex-operating-system.md) |
| GitHub issues | [Operations](docs/agent/issue-tracker.md); [labels](docs/agent/triage-labels.md) for triage |
| Explaining an algorithm mechanism to the user | Show the user guide's figures first, e.g. [`rt-drift.md`](docs/user/rt-drift.md); to redraw them for another run, use the generator in [`assets/README.md`](docs/user/assets/README.md) |
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
  [Core Model](docs/architecture/core-model.md) and
  [Change Locality](docs/architecture/core-model.md#change-locality).
- **Change scope:** Merge duplication and remove what a change replaces in the
  same delivery; add no feature, abstraction, defensive branch or document the
  task does not need.
- **Domain purity:** core algorithms work on in-memory domain models. Never mix
  RAW/DLL I/O, multiprocessing, byte hashing or output formatting into
  mathematical or alignment algorithms.
- **Shortest path to evidence:** default to the valid inputs in hand. Require
  an input, stage or environment only if removing it changes the answer or
  invalidates its evidence. Coupling an independent check to an unrelated
  requirement forces callers to bypass its protection to avoid that requirement.
- **Batch cost:** prepare shared inputs outside loops; measure call counts before
  scaling. Within one operation, reuse unchanged inputs and results; recheck at
  a later boundary when a change there could invalidate the result.
- **Current state only:** code, comments, tests and docs describe current
  behavior and non-obvious constraints. History stays in Git and PRs.
- **Module naming:** name modules for their capability, following
  [Module Naming](docs/project-layout.md#module-naming).
- **Runtime:** use this worktree's documented Python runner. Run output stays in
  ignored `output/`; a tracked artifact needs a current consumer.
- **Performance:** follow
  [`xic-performance-diagnosis`](.codex/skills/xic-performance-diagnosis/SKILL.md)
  for timing, locality or performance comparisons.
- **Evidence:** say what the check actually established. For scientific or
  readiness claims, use the [evidence labels](docs/agent/product-validation-contract.md#evidence-labels).
- **Merge:** acceptance evidence and hosted checks on the final remote SHA
  must both be satisfied; see [Acceptance Review](docs/agent/delivery.md#acceptance-review).

# XIC Extractor Agent Contract

XIC-specific invariants and where to find the detailed rules. Global Codex
rules own general operating defaults.

## Where To Look

| Task | Read |
| --- | --- |
| Python runner, worktree setup, RAW/DLL, validation scale, environment failure | [`local-execution.md`](docs/agent/local-execution.md) |
| Product terminology | [`domain-glossary.md`](docs/product/domain-glossary.md) |
| Product behavior or method evidence | [`docs/product/README.md`](docs/product/README.md) |
| Readiness claim, scientific decision, or change to published values | [`product-validation-contract.md`](docs/agent/product-validation-contract.md) and [Writer Authority](docs/product/decision-policy.md#writer-authority) |
| Choosing, adding or running tests; UI demonstration | [`testing.md`](docs/agent/testing.md) |
| Owner boundary, persisted schema, runtime lifecycle, write authority, public contract | [`architecture-public-contracts.md`](docs/agent/architecture-public-contracts.md); [`architecture-contract.md`](docs/architecture-contract.md) when the owner boundary moves |
| UI styling or theme roles | [UI Presentation](docs/architecture-contract.md#ui-presentation) |
| Imports in startup-sensitive or lightweight readers | [Dependency Direction](docs/architecture-contract.md#dependency-direction) |
| Issue scope, push, PR, review, long RAW run | [`delivery.md`](docs/agent/delivery.md) |
| Current-format Untargeted output as test input | Regenerate with `.venv\Scripts\python.exe -m scripts.run_tissue_validation --dataset 8`; older runs under `output/` are not current-format input |
| Scripts and diagnostics | [`script-tool-lifecycle.md`](docs/agent/script-tool-lifecycle.md) to add or retire one; [`INDEX.md`](tools/diagnostics/INDEX.md) to pick one; [`diagnostic-ledger.md`](docs/diagnostic-ledger.md) before repeating evidence |
| Docs, skills, hooks, rules or agent config | [`codex-operating-system.md`](docs/agent/codex-operating-system.md) |
| GitHub issue mechanics or triage | [`issue-tracker.md`](docs/agent/issue-tracker.md) and [`triage-labels.md`](docs/agent/triage-labels.md) |
| File placement, tracking, where a document belongs | [`project-layout.md`](docs/project-layout.md) |

## Always On

- **Structure:** use Codebase Memory (CBM) for callers, dependencies and impact
  once its index matches this checkout; read current source for exact text and
  negative claims.
- **Change locality:** ownership, serialization, identity, derived-fact and
  internal-defense changes follow
  [Change Locality](docs/architecture-contract.md#change-locality).
- **Domain purity:** core algorithms work on in-memory domain models. Never mix
  RAW/DLL I/O, multiprocessing, byte hashing or output formatting into
  mathematical or alignment algorithms.
- **Batch cost:** move shared preparation out of loops and measure call counts
  at small scale before scaling up. Deep diagnostics, full re-validation and
  audits run on demand, not in the normal path, unless they decide this
  output's correctness and nothing cheaper does.
- **Current state only:** code, comments, tests and docs describe current
  behavior and non-obvious constraints. History stays in Git and PRs.
- **Module naming:** name modules for their capability, following
  [Module Naming](docs/architecture-contract.md#module-naming).
- **Runtime:** use this worktree's documented Python runner. Run output stays in
  ignored `output/`; a tracked artifact needs a current consumer.
- **Performance:** use `xic-performance-diagnosis` for timing, locality or
  performance comparisons.
- **Evidence:** report the Evidence label and the method's actual oracle.
  Passing tests prove regression coverage, not scientific or production
  readiness.
- **Merge:** hosted checks on the final remote SHA decide merge readiness.

# XIC Extractor Agent Contract

This root file is the high-frequency control plane. Global Codex rules still
apply. Read only the route triggered by the current task; the linked owner
carries definitions, command details, and low-frequency exceptions.

## Task Routes

| Trigger | Required route |
| --- | --- |
| Project Python, worktree bootstrap, RAW/DLL, validation scale, command shape, or sandbox friction | Choose one branch in [`parameter-settings.md#read-routes`](docs/agent/parameter-settings.md#read-routes). |
| Product term, method evidence, readiness claim, or validation policy | Route through [`docs/product/README.md`](docs/product/README.md), [`domain-glossary.md`](docs/product/domain-glossary.md), and [`product-validation-contract.md`](docs/agent/product-validation-contract.md). |
| Test selection, new regression coverage, fixture design, or `ledger_snapshot` | Read [`testing.md`](docs/agent/testing.md). |
| Semantic owner, Target/Untarget lifecycle, persisted decision, runtime receipt, writer, schema, or public contract | Choose one branch in [`architecture-public-contracts.md#read-routes`](docs/agent/architecture-public-contracts.md#read-routes), then use [`architecture-contract.md`](docs/architecture-contract.md) when the owner boundary changes. |
| Issue scope, task shaping, Current Packet, refreeze, out-of-scope finding, or **Issue-to-PR scope firewall** | Read [`planning-workflows.md`](docs/agent/planning-workflows.md). |
| Clean-checkout artifact, long RAW run, CI evidence, push, PR, or ready-for-review | Choose one branch in [`execution-gates.md#read-routes`](docs/agent/execution-gates.md#read-routes). |
| Review communication, PR receipt, handoff, or Obsidian placement | Read [`communication-review.md`](docs/agent/communication-review.md) and [`obsidian-handoff-contract.md`](docs/agent/obsidian-handoff-contract.md). |
| Script/diagnostic creation, reuse, rerun, exposure, or retirement | Read [`script-tool-lifecycle.md`](docs/agent/script-tool-lifecycle.md), search [`tools/diagnostics/INDEX.md`](tools/diagnostics/INDEX.md), and check [`diagnostic-ledger.md`](docs/diagnostic-ledger.md). |
| Productization state, activation, promotion, or writer authority | Read [`productization.md`](docs/product/productization.md) and its linked current manifests/indexes. |
| Hook, rule, agent config, skill, Git command policy, or long-task state | Choose one branch in [`codex-operating-system.md#read-routes`](docs/agent/codex-operating-system.md#read-routes). |
| GitHub issue mechanics or triage | Read [`issue-tracker.md`](docs/agent/issue-tracker.md) and [`triage-labels.md`](docs/agent/triage-labels.md). |
| File placement or repository layout | Read [`project-layout.md`](docs/project-layout.md). |

## Always-On Gates

- **Mutation gate:** before non-trivial edits, verify worktree, branch, and dirty
  scope. Preserve unrelated changes; stage only task files.
- **Task shape:** name the objective, current owner/evidence, boundaries,
  acceptance, and stop rule before implementation. Direction-changing
  uncertainty escalates through the planning route before mutation continues.
- **Scope budget:** implement the requested behavior and reachable regressions.
  Adjacent cleanup, future-proofing, compatibility, and defensive branches need
  a current acceptance requirement, consumer, or reachable trust boundary.
- **Test value:** use the cheapest existing observation that can disprove the
  change. Add or modify a test only for current observable behavior, a public
  contract, or a reproducible regression with a stable oracle. Cleanup and
  retirement remove obsolete tests with the code and close through existing
  behavior, referrer census, artifact inspection, or diff review—not tombstone
  tests for private helpers, deleted paths, or one-time repository state.
- **Final-state artifacts:** code, comments, tests, and docs describe current
  behavior and non-obvious constraints. Cleanup history and rejected options
  stay in Git/PR history unless a current consumer needs the rationale.
- **Runtime owner:** use the active worktree's documented Python runner and
  bootstrap. Run-specific artifacts stay under ignored task-specific `output/`;
  tracked artifacts require a current runtime, CI, or public consumer.
- **Git boundary:** run one direct Git command per shell step. Parallelize
  independent reads inside one orchestrator call and return bounded summaries,
  anomalies, counts, and locators. Routine sync uses
  `git pull --ff-only --no-stat` after inspecting only the current branch.
- **Long-task spine:** long-running or cross-session work uses ignored
  `output/agent-state/<task-id>/state.json` via
  `python -m tools.agent_task_state`. Validate the task base on resume, advance
  only at semantic phase transitions, and render handoffs from that state.
- **Boundary preflight:** use `xic-architecture-preflight` for a new/replaced
  semantic owner, writer authority, runtime/public contract, or scientific
  evidence launch. Performance work first uses `xic-performance-diagnosis`;
  RAW launch or artifact acceptance uses `xic-raw-validation`.
- **Evidence claim:** report the canonical Evidence label and the actual method-
  appropriate oracle. Passing tests proves engineering regression coverage,
  not scientific or production readiness.
- **Execution config:** changes to `.codex` hooks, rules, config, execpolicy, or
  subagent definitions run the smoke, focused tests, diff check, and secret/
  local-path scan in the operating-system change protocol.
- **PR gate:** before push, PR creation, remote-head change, or ready-for-review,
  follow the SHA-bound [PR Verification Gate](docs/agent/execution-gates.md#pr-verification-gate).
  Hosted checks on the final remote SHA remain merge authority.
- **Simplify pass:** before closeout, delete machinery whose removal preserves
  the current requirement and tests. An abstraction needs two current callers,
  configuration a current second value, and compatibility a current consumer.

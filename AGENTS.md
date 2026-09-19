# XIC Extractor Agent Contract

This root file carries only high-frequency XIC deltas; Global Codex rules own
general opportunity cost, minimal delivery, and test necessity. Load a route
only for the decision being made, and reuse still-current context. The table
is not a checklist for every edit. A local change within an established owner
uses the affected source, existing tests, and the relevant branch below.

## Task Routes

| Trigger | Required route |
| --- | --- |
| Project Python, worktree bootstrap, RAW/DLL, validation scale, command shape, or sandbox friction | Choose one branch in [`parameter-settings.md#read-routes`](docs/agent/parameter-settings.md#read-routes). |
| Product terminology lookup | Read the relevant entry in [`domain-glossary.md`](docs/product/domain-glossary.md). |
| Product behavior or method evidence decision | Use [`docs/product/README.md`](docs/product/README.md) to locate the relevant topic owner. |
| Readiness claim or validation policy | Read [`product-validation-contract.md`](docs/agent/product-validation-contract.md). |
| Test selection, new regression coverage, fixture design, or `ledger_snapshot` | Read [`testing.md`](docs/agent/testing.md). |
| Decide or change semantic ownership, Target/Untarget lifecycle, persisted schema/decision, runtime receipt, write authority, or a public contract | Choose the relevant branch in [`architecture-public-contracts.md#read-routes`](docs/agent/architecture-public-contracts.md#read-routes); use [`architecture-contract.md`](docs/architecture-contract.md) when the owner boundary changes. |
| Issue scope, task shaping, Current Packet, refreeze, out-of-scope finding, or **Issue-to-PR scope firewall** | Read [`planning-workflows.md`](docs/agent/planning-workflows.md). |
| Clean-checkout artifact, long RAW run, CI evidence, push, PR, or ready-for-review | Choose one branch in [`execution-gates.md#read-routes`](docs/agent/execution-gates.md#read-routes). |
| Review communication or PR receipt | Read [`communication-review.md`](docs/agent/communication-review.md). |
| Private or cross-session handoff, or Obsidian placement | Read [`obsidian-handoff-contract.md`](docs/agent/obsidian-handoff-contract.md). |
| Create, expose, consolidate, or retire a script/diagnostic; select or repeat scientific diagnostic evidence | Use [`script-tool-lifecycle.md`](docs/agent/script-tool-lifecycle.md) for lifecycle changes; search [`INDEX.md`](tools/diagnostics/INDEX.md) when selecting a tool and [`diagnostic-ledger.md`](docs/diagnostic-ledger.md) when reusing or repeating evidence. Routine invocation of an already selected command uses its help and runner. |
| Productization state, activation, promotion, or writer authority | Read [`productization.md`](docs/product/productization.md) and its linked current manifests/indexes. |
| Change a hook, rule, agent config, or skill; resolve Git command-policy behavior | Read [`codex-operating-system.md`](docs/agent/codex-operating-system.md). |
| GitHub issue mechanics or triage | Read [`issue-tracker.md`](docs/agent/issue-tracker.md) and [`triage-labels.md`](docs/agent/triage-labels.md). |
| Add or move a maintained file, change tracking, or alter repository layout | Read [`project-layout.md`](docs/project-layout.md). |

## Always-On Gates

- **Mutation gate:** before non-trivial edits, verify worktree, branch, and dirty
  scope. Preserve unrelated changes; stage only task files.
- **Mission lock:** the current Issue/Current Packet is the durable task
  authority when one exists. TaskFrame keeps only its locator, volatile
  decisions/progress, and evidence receipts; do not copy the packet's scope or
  acceptance into another contract.
- **Technical convergence:** Main resolves in-scope technical unknowns with the
  cheapest decisive evidence and smallest reversible step. Ask one concrete
  operator question only when missing intent or an operator-only value/authority
  can change the outcome; block or hold is interim, never delivery.
- **Structural observation:** use Codebase Memory (CBM) first for callers,
  dependencies, impact, and reachability; check index coverage once, then use
  targeted source reads only for exact text, reported gaps, or negative claims.
- **Necessity proof:** prefer existing owners and mechanisms. Added design must
  serve a present need worth its maintenance cost; use the
  [design principles](docs/architecture-contract.md#design-principles).
  Ownership, serialization, derived-fact, and internal-defense changes use the
  XIC technical criteria in [Change Locality](docs/architecture-contract.md#change-locality).
  [`testing.md`](docs/agent/testing.md) owns test worth.
- 批次路徑將共享準備移出迴圈，先以呼叫次數與小規模量測確認成本，再放大資料量。深度診斷、全圖重驗與審計預設按需執行；只有會影響本次輸出正確性、且沒有較便宜替代檢查時，才放進正常執行路徑。
- **Responsibility boundary & domain purity:** core domain algorithms operate purely on in-memory domain models. Never mix RAW/DLL I/O, multiprocessing, byte hashing, or finalization formatting into mathematical or alignment algorithms. Downstream internal routines must not re-validate authoritative upstream entities.
- **Final-state artifacts:** code, comments, tests, and docs describe current
  behavior and non-obvious constraints. Cleanup history and rejected options
  stay in Git/PR history unless a current consumer needs the rationale.
- **Module naming:** name new or renamed modules for their actual capability
  and responsibility, following [Module Naming](docs/architecture-contract.md#module-naming).
- **Runtime owner:** use the active worktree's documented Python runner and
  bootstrap. Run-specific artifacts stay under ignored task-specific `output/`;
  tracked artifacts require a current runtime, CI, or public consumer.
- **Git boundary:** run one direct Git command per shell step. Parallelize
  independent reads inside one orchestrator call and return bounded summaries,
  anomalies, counts, and locators. Read-only `diff`/`log`/`show` may end with
  `| Select-Object -First N` or `| head -n N`, where N is a positive literal;
  this is an output excerpt, not proof of complete inspection. Routine sync uses
  `git pull --ff-only --no-stat` after inspecting only the current branch.
- **Specialized gates:** performance work first uses
  `xic-performance-diagnosis`; RAW launch or artifact acceptance uses
  `xic-raw-validation`.
- **Evidence claim:** report the canonical Evidence label and the actual method-
  appropriate oracle. Passing tests proves engineering regression coverage,
  not scientific or production readiness.
- **Agent guidance changes:** use the
  [operating-system change protocol](docs/agent/codex-operating-system.md#change-protocol)
  for docs, skills, hooks, rules, config, and subagent definitions. Select its
  checks by the behavior changed; execution changes require smoke and focused
  runtime tests.
- **PR gate:** before push, PR creation, remote-head change, or ready-for-review,
  follow the SHA-bound [PR Verification Gate](docs/agent/execution-gates.md#pr-verification).
  Hosted checks on the final remote SHA remain merge authority.

## Codex Code Review Rules

- Main neutrally adjudicates reviewer findings as evidence leads. Adopt only a
  current linked-issue/public-contract violation or reachable observable loss
  naming its obligation, actor/input/path, and consumer-visible loss; severity
  or a refreeze request does not replace the mission.
- Review changed behavior, reachable production callers, and current consumers.
  Hypothetical or test-only findings without a real entry are non-blocking;
  route pre-existing out-of-scope losses to their owner.

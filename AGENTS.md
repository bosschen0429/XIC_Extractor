# XIC Extractor Agent Contract

This root file carries only high-frequency XIC deltas; Global Codex rules own
general opportunity cost, minimal delivery, and test necessity. Read only the
triggered route; its owner carries detail.

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
| Script/diagnostic creation, reuse, rerun, exposure, or retirement | Read [`script-tool-lifecycle.md`](docs/agent/script-tool-lifecycle.md), search [`tools/diagnostics/INDEX.md`](tools/diagnostics/INDEX.md), and check [`diagnostic-ledger.md`](docs/diagnostic-ledger.md). |
| Productization state, activation, promotion, or writer authority | Read [`productization.md`](docs/product/productization.md) and its linked current manifests/indexes. |
| Hook, rule, agent config, skill, Git command policy, or long-task state | Read [`codex-operating-system.md`](docs/agent/codex-operating-system.md). |
| GitHub issue mechanics or triage | Read [`issue-tracker.md`](docs/agent/issue-tracker.md) and [`triage-labels.md`](docs/agent/triage-labels.md). |
| File placement or repository layout | Read [`project-layout.md`](docs/project-layout.md). |

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
- **Necessity proof:** default to the current owner, mechanism, observation, and
  tests. New design or test must prove need. Design needs a current consumer and
  removal loss: abstraction needs two callers, configuration a second value,
  and compatibility a current consumer. [`testing.md`](docs/agent/testing.md)
  owns the test-worth gate.
- 同一份未變更輸入，由正式 owner 完成計算或驗證後，下游重用結果，只檢查本次消費所需的身分與綁定；重算或深度重驗必須指出改變的輸入、新的信任邊界或可觸達的失效機制，不能只以「更保險」為由。
- 批次路徑將共享準備移出迴圈，先以呼叫次數與小規模量測確認成本，再放大資料量。深度診斷、全圖重驗與審計預設按需執行；只有會影響本次輸出正確性、且沒有較便宜替代檢查時，才放進正常執行路徑。
- **Final-state artifacts:** code, comments, tests, and docs describe current
  behavior and non-obvious constraints. Cleanup history and rejected options
  stay in Git/PR history unless a current consumer needs the rationale.
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
- **Execution config:** changes to `.codex` hooks, rules, config, execpolicy, or
  subagent definitions run the smoke, focused tests, diff check, and secret/
  local-path scan in the operating-system change protocol.
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

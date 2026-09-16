# Trigger Cases

## Should Trigger

- "這些 bound docs 真的重要嗎，還是只是被 referrer 綁住？"
- "整理 docs/superpowers 的 validation 歷史，找哪些資訊已經 stale 或重複。"
- "Backfill validation 文件太多，幫我壓縮成目前有效的 repo surface。"
- "不要逐檔 stub，先判斷哪些歷史驗證還有資訊價值。"
- "把 Obsidian/source-copy/stub 文件整理流程固化，避免 agent 再把引用當重要性。"
- "這些 repo_product_doc 可留 repo，但是真的都消化完了嗎？"
- "同一主題是不是只要一份 owner，其他文件該當 support 或進 Obsidian？"
- "用 writing-plans 寫完計畫後，這份 plan 執行完到底該去哪？"
- "把這段長對話裡的文件整理規則變成可重複的流程。"
- "route-retained 數量很多，但我懷疑裡面很多只是過期驗證或重複歷程。"

## Should Not Trigger

- "幫我開 PR" -> use global `pr-closeout`; read XIC readiness/artifact owners
  only when they are relevant.
- "跑 8RAW 驗證" -> use `xic-raw-validation`.
- "調查 performance bottleneck" -> use `xic-performance-diagnosis`.
- "新增 product doc 的使用者說明" -> ordinary docs edit unless retention/compression is the issue.
- "修一個 validation checker bug" -> use architecture/preflight and implementation flow.
- "修改 README quick start 文案" -> ordinary docs edit unless route/lifecycle/retention is the issue.

## Near Neighbors

- global `pr-closeout`: durable PR/branch narrative.
- global `code-review`: explicit fixed-point PR diff review.
- `agent-memory`: consult configured private context when it can change the
  classification; source-copy follows the canonical retirement contract.
- `skill-creator`: refine this skill's instructions and trigger wording.

## Owner And Authorization Walkthroughs

These are manual decision scenarios, not automated evals or observed agent
behavior. Apply the current owner contract to each proposed output:

- A completed plan has an ordinary exact referrer: absorb its durable claim and
  retarget the referrer before retirement; history remains in Git/PRs.
- The named compatibility index has a live checker consumer: retain its current
  machine contract until the consumer has a validated replacement.
- An active plan contains the next implementation actions: route construction
  to the owning Issue and disposable next actions to an ignored handoff.
- Two candidate owners can be resolved from current repo consumers: inspect the
  decisive evidence and proceed under existing authorization.
- Safe classification is complete but required operator-only retirement approval
  is absent: produce the reviewable owner/referrer result and ask for that
  authority before the destructive step.

# Delta review: Issue #180 refreeze v2.2 Slice C budget

## 決策

**Approved.** Slice C may resume under a 275-production-LOC cap and an
800-production-LOC total. The measured change affects estimate accuracy only;
the approved owner, product rule, invariants, two-type limit, one-PR boundary,
public surfaces, acceptance gates, and rollback are unchanged.

## Pinned input and coverage

- Reviewed: all 390 lines of `output/issue180_m9_prd_refrozen_v2.md`, refreeze
  v2.2 dated 2026-08-05, SHA-256
  `0816F23FB4F8B89CAA60E0FC5D15364C3243439F2586F29967255DE3106B54A4`.
- Before-review delta: v2.1 explicitly capped Slice C at 175 production LOC and
  the complete PR at 700.
- Trigger evidence: the incomplete Slice C projection type and row-preparation
  diff measured 190 added production LOC before acceptance-row and terminal-QMV
  closure.
- Not reviewed: correctness of the unfinished code diff or RAW evidence.
- Project type/oracle: bounded product projection; expected diff plus checked
  manifest/QMV and zero unrelated diff.

## Readiness gates

All five readiness gates remain explicit and unchanged: observable loss, M6
projection ownership, composite row/cell identity and state closure, forbidden
second-writer/M1 strategies, and adversarial expected-diff/QMV oracles all pass.

## Finding and design disposition

- **[resolved before verdict] v2.1 under-budgeted the existing authority
  handoff.** The missing work is not a new feature: complete manifest rows and a
  terminal QMV validator are required to delegate numeric authority to the
  existing owners. Deleting them would violate the plan. The bounded correction
  is 275 LOC for C and 800 total; A+B remains capped at 525.

| Challenge | Disposition | Oracle |
| --- | --- | --- |
| Delete checked-manifest/QMV closure | reject | would create projection-owned write authority |
| Delegate to existing manifest/QMV owners | keep | file-level checked manifest and expected diff |
| Split the single adoption transaction | unchanged | operator retained one rollback transaction |
| Unbounded budget increase | reject | 525 A+B / 275 C / 800 total hard stops |

## Provenance and handoff

| Decision | Before review | Disposition | Invalidates design? |
| --- | --- | --- | --- |
| C <=175 / total <=700 | explicit v2.1 estimate | superseded by measured incomplete diff | no |
| C <=275 / total <=800 | missing v2.1 | approved bounded correction | no |
| two types, four modules, one PR, M1 frozen | explicit | unchanged | no |

No human decision remains before resuming Slice C. Crossing 275 for C or 800
total is another hard stop and requires a design split, not another estimate
increase.

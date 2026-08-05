# Delta review: Issue #180 refreeze v2.1 LOC budget

## 決策

**Approved.** Production implementation may resume under the v2.1 per-slice
budget. This delta changes only the measured LOC allowance; M1, semantic owners,
identity/state invariants, product rule, one-PR transaction, public surfaces,
validation gates, and rollback remain identical to the approved v2 design.

## Pinned input and coverage

- Reviewed: all 389 lines of `output/issue180_m9_prd_refrozen_v2.md`, refreeze
  v2.1 dated 2026-08-05, SHA-256
  `C9D07D77B3DAEB1416BCBC49734699644D2AA36F829314FD50EFA3929C1E8F50`.
- Before-review delta: v2 explicitly allowed 350 added production LOC total.
- Trigger evidence: the first Gate A Slice A/B working diff measured 501 added
  production LOC while its focused test, ruff, and mypy gates passed.
- Operator decision: retain one final PR and raise the budget rather than split
  the transaction or remove fail-closed validation.
- Not reviewed: correctness of the current code diff or Gate B/C evidence.
- Missing evidence: none for the budget decision; code correctness remains with
  Gate A and fresh code review.

## Implementation-readiness gates

| Gate | Before review | Evidence anchor | Verdict |
| --- | --- | --- | --- |
| Observable loss | explicit, unchanged | v2.1 `Problem and observable loss` | pass |
| Semantic owner | explicit, unchanged | v2.1 owner table | pass |
| Identity/cardinality/state | explicit, unchanged | v2.1 invariants | pass |
| Forbidden strategies | explicit, unchanged | v2.1 forbidden list | pass |
| Counterexample/oracle | explicit, unchanged | v2.1 adversarial oracle | pass |

## Finding and disposition

- **[resolved before verdict] The v2 LOC estimate was falsified.** A 501-line
  M2 implementation cannot fit the 350-line total even before Slice C. Silently
  continuing would violate the stop rule; deleting closure validation would
  weaken the approved design. V2.1 replaces the estimate with a 700-line total,
  capped at 525 for combined M2 facts/mapping A+B and 175 for M6/runtime
  projection C.

| Challenge | Disposition | Reason / oracle |
| --- | --- | --- |
| Delete validation to regain 350 | reject | would reopen partial/crossed/synthetic-vote states |
| Delegate to existing M8/M2 validators | keep | v2.1 still reuses them; no second validator authority |
| Split into two PRs | operator rejected | complete opt-in adoption remains one rollback transaction |
| Raise an unbounded total | reject | per-slice and total caps remain hard stops |

## Transaction map

| Slice | Budget | Owner | Oracle | Rollback |
| --- | ---: | --- | --- | --- |
| A+B facts and exact-owner mapping | <=525 production LOC | M2, consuming M8.1-validated facts | Gate A mixed-state/census/cardinality suite | remove opt-in M2 operation/type |
| C checked product projection | <=175 production LOC | existing M6/runtime projection and manifest/QMV owners | expected diff, zero unrelated diff, Gate B/C | withdraw M9 manifest/QMV and revert squash |

## Decision provenance

| Decision | Before review | Review disposition | Source | Invalidates design? |
| --- | --- | --- | --- | --- |
| 350 production LOC total | explicit v2 estimate | superseded | observed 501-line Slice A/B diff | no; estimate only |
| 700 total / 525 A+B / 175 C | missing in v2 | operator-added and approved | budget checkpoint decision | no |
| One final PR | explicit v2 | retained by operator | checkpoint decision | no |
| M1 frozen and exact-owner-only | explicit v2 | unchanged | approved v2 contract | no |

No human decision remains before resuming Gate A. Crossing either slice cap or
the 700-line total is another hard stop; it cannot be waived during code review.

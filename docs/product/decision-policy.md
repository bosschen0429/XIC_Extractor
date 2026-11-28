# Decision Policy

The cell decision selects one value or one typed no-write reason per cell;
writers render that decision and nothing else. This page owns who may write a
value, the closed set of no-write reasons, and when a Targeted value is
withheld. Selection rules live with their owners:
[Targeted selection](targeted-selection.md), [Backfill](backfill.md),
[Alignment](alignment.md) and the [Quant Matrix](quant-matrix.md).

| Question | Rule | Detail |
| --- | --- | --- |
| Who selects a cell's value? | Only the cell decision; writers render it and never take a value from review rows, counts or diagnostics. | [Writer authority](#writer-authority) |
| What does a cell without a value say? | Exactly one reason from a closed set; never zero or absence. | [No-write reasons](#no-write-reasons) |
| When is a Target value withheld? | When low S/N, sparse scans and no majority continuous run all hold: `not_quantifiable`. | [Targeted quantitative support](#targeted-quantitative-support) |

## Writer Authority

Only the decision owner selects a cell's value and state. A writer renders that
decision; it does not recompute it, and it does not take a value or state from
review rows, manifests, counts or diagnostics.

The Targeted workbook and the Untargeted Matrix are published only by their
workflow writers; Targeted and Untargeted keep their own writers. Output is
published only after the write completed; a failed write leaves the previous
output in place.

Evidence providers, candidate rows, write-ready labels, quality blockers,
sidecars, replays, review packets, galleries and diagnostics can explain or
gate a decision, but none grants writer authority. A new writer path needs its
own contract, expected diff and focused tests under the
[Expected-Diff Contract](../agent/product-validation-contract.md#expected-diff-contract).

## No-Write Reasons

A cell decision either selects a value or returns one typed no-write reason.
The closed set is:

| Reason | Meaning |
| --- | --- |
| `not_searched` | No search ran for this cell |
| `searched_no_observation` | A completed search found no candidate peak |
| `unresolved` | Candidate evidence exists but does not resolve to one supported peak (for CID-NL, `HOLD` with candidate peaks) |
| `rejected_observation` | The method rejected the cell (for CID-NL, `REJECT`) |
| `not_quantifiable` | An observed peak cannot support a reliable quantity (below) |
| `unavailable` | The evaluation was technically unsupported or failed |

Human-facing text is rendered from these typed facts and is never parsed back
into scientific logic. Failure, ambiguity or missing positive evidence never
serializes as chemical absence, zero or a selected value.

## Targeted quantitative support

For a Target cell, an observed integral is not automatically fit for numeric
publication. The cell decision returns `not_quantifiable` when all three hold:
the retained signal quality reports low S/N, fewer than half of the selected
raw XIC scans are nonzero, and no continuous run contains at least half of
those nonzero scans.

- Low S/N alone, sparse outer flanks around a continuous body, or missing
  quality metadata do not establish this combined failure. These majority
  conditions are an operational support policy, not a calibrated LOD/LOQ or
  chemical-absence claim.
- The selected integration, original trace and method evidence stay retained.
  NL or an ISTD identity cannot override inadequate MS1 support.
- The rule applies only to Target decisions. The writer renders it as blank
  numeric cells with the `不足以定量` label, not zero or ND, without
  re-evaluating the signal.

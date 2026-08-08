# Issue #195 Backfill decision ledger evidence

Validation status: `diagnostic_only`.

Source artifact: `issue159_full_universe_provisional_matrix_20260807_7eef01e2`.
Its legacy manifest asserts `7eef01e273ee62b91bd6c07e4a455994f76ca37b`;
the current manifest owner cannot validate that assertion. The command opened
no RAW files. The artifact is stale-revision diagnostic cost evidence and does
not claim current-head parity or a validated producing revision.

## Reproduction

```powershell
.venv\Scripts\python.exe -m tools.diagnostics.backfill_decision_ledger `
  --artifact-root <retained-artifact-root> `
  --output-dir output/issue195_backfill_decision_ledger `
  --expected-code-revision 7eef01e273ee62b91bd6c07e4a455994f76ca37b
```

The ignored output contains `backfill_decision_ledger.tsv`,
`backfill_decision_summary.json`, and `backfill_decision_summary.md`.
The verified run emitted `365,176` ledger lines (header plus `317,036`
candidate rows and `48,139` group-settlement rows). SHA-256 values were
`F54F447EEEFA0CC826B7815B5AD454A7D2B44F87C8847146CA3AEA35CA6E6CE2` for
the ledger and
`4EBC06B5A3611F034455A0FD166569AF9EC069CA67829CE0FAA10F4332E26BEF` for
the JSON summary.

## Observed evidence

| Evidence | Result | Authority ceiling |
| --- | ---: | --- |
| Discovery candidates | 317,036 | Owner-rehydrated candidate fields |
| Populated / unique physical feature IDs | 79,443 / 79,443 | Owner-rehydrated `ms1_feature_row_id` |
| Duplicate physical feature IDs | 0 | Candidate-layer defect not reproduced |
| Multi-seed candidates | 59,214 | Declared scan IDs; RAW scan content not replayed |
| Final `primary_loser` rows | 36,608 / 48,139 (76.0464%) | Persisted projection only |
| Display request proxy duplicates | 133,547 / 3,083,400 (4.331%) | Not exact request equivalence |
| Owner Backfill | 909.689 s; 1,843,631 XIC; 761 RAW calls; 878,879,685 points | Aggregate cost only |

The downstream owner-assignment/group/request portion of the pairing hypothesis
is inconclusive because the retained artifact omits complete owner payloads,
owner-minted logical request IDs/equivalence classes, and request-to-batch/call/
point lineage.

The retained manifest uses legacy schema
`untargeted_provisional_matrix_run_v1`; the current manifest owner rejects that
schema. The summary therefore records manifest rehydration as unavailable. It
does not promote the legacy manifest's embedded assertions, including the
asserted producer revision. Candidate fields
remain separately owner-rehydrated through their Discovery receipts; alignment
and cost inputs remain labeled as projections/aggregates.

The machine summary distinguishes all five diagnosis layers: A (same candidate,
other MS2 evidence) was not reproduced; B (same physical peak, distinct
scientific hypotheses) is inconclusive; C (Alignment owner consolidation) is
aggregate-only/partial; D (winner/loser settlement) has only a final projection
and no pre-Backfill knowability proof; E (exact request/super-window/batch reuse)
is unavailable.

## Decision and order

Value priority: owner-valid early-loser-settlement shadow before exact request
coalescing. A large post-Backfill loser population is observed, while the
smaller request-reuse number is only a display proxy.

Dependency order:

1. Decision ledger (this PR).
2. Owner-valid early-loser-settlement shadow/instrumentation.
3. Owner-minted exact request identity, then exact-safe coalescing.
4. Artifact-format work after row lifecycle/cardinality stabilizes.
5. Recovery/resumability last, after the winning execution shape stabilizes.

Falsifiers are: losers cannot be known before Backfill without changing
scientific output; owner-minted exact identities reveal materially larger reuse;
or comparable owner-correlated evidence attributes negligible Backfill cost to
later losers.

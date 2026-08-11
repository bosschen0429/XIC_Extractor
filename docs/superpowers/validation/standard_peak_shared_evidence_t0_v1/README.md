# Standard Peak shared-evidence T0 characterization v1

This packet can support only the no-write #240 shared-fact owner deepening after
PR #249 merges green; it cannot cut over the Standard Peak route or write a
Matrix value. The retained evidence closes SP-011–SP-014, while review found a
load-bearing stage-policy owner gap now isolated in #252. Next, merge this
corrected T0 packet and activate #240 from the then-current `master`; #250,
#252, and #251 remain separate downstream gates.

## Status

- Issue: GitHub #239, child of #238; architecture parents #113 and #159.
- Frozen source revision: `db3fa5e6c1d2756afe1c600b57a81ef17aef2443`.
- Characterization branch: `codex/issue-239-standard-peak-characterization`.
- Evidence label: `diagnostic_only`.
- Write authority: `none`.
- Scope: current Standard Peak production/replay path only. Pt, ModeTrack,
  broad Backfill activation, report UX, public preset redesign, and Targeted
  orchestration are excluded by #239.
- Runtime effect: none. This packet adds no production type, parser, adapter,
  cache, CLI/config/GUI field, writer, schema, dependency, or authority.
- RAW lanes: none. RAW validation was neither required nor authorized.
- Budget exception: the operator explicitly authorized one additional tracked
  docs/index file on 2026-08-11. The required packet directory still contains
  exactly four files; the fifth changed docs file is the pre-existing
  `docs/superpowers/validation/ARTIFACT_INVENTORY.tsv`, whose four new rows are
  mandatory for the repository retention gate.

## Result in one sentence

The reusable physical facts fit the existing `xic_extractor/peak_detection`
owner, but nine load-bearing Standard Peak stage-policy rows do not yet have an
implemented package owner; #252 must either deepen the existing Backfill policy
owner under exact parity or keep the route blocked.

Terminal disposition: PROMOTE_TO_T1

Route cutover status: BLOCKED_PENDING_250_252_251

This disposition authorizes only the already-frozen no-RAW T1 characterization
and owner-deepening scope in #240. It does not activate a downstream issue,
authorize production behavior, or authorize a Matrix write.

`PROMOTE_TO_T1` applies only to the shared facts in SP-011–SP-014. It does not
promote the historical stage policies, quantitation projection, compatibility
route, or authority/finalization rules. Those rules remain blocked by their
single implementation children and deletion gates in `rule_disposition_v1.tsv`.

## Boundary-closure refreeze

The post-T0 boundary and code review found that the original serial issue stack
encoded optional sequencing as native causality, left the final successor of
many Standard Peak rules implicit, combined an exact-parity Owner Backfill move
with an unresolved raw-versus-Gaussian Matrix-value decision, and then falsely
assigned twelve diagnostic policy/orchestration rows to an Owner Backfill
module that did not implement them. The programme was refrozen before
production implementation:

```text
#239 -> #240 shared no-write fact seam
          -> #241 Owner Backfill fact migration -> #250 T3E-Q quantitation
          -> #242 Discovery adapter
          -> #243 Alignment relation adapter
          -> #244 optional Targeted duplicate census

#241 + #243 -> #252 T3E-P Backfill stage-policy adoption
#250 + #252 -> #251 live authority/finalization compatibility closure
#251 -> #245 CLI -> #246 GUI -> #247 retirement
```

#242 and #244 are not retirement prerequisites. #244 closes `not_needed`
without a branch when its owner-derived census finds no duplicate Targeted
physical-fact call.

The rule ledger now records one planned successor owner, implementation child,
runtime disposition, downstream input, parity/expected-diff rule, and deletion
gate for every SP rule. Planned does not mean implemented: SP-003/004/006/015/
022/023/032/033/034 remain blocked on #252. SP-007 and SP-030 are #251
compatibility composition; SP-019, SP-021, and SP-042 are #252 offline oracles.
A live successor cannot remain in diagnostic code, and #251 cannot invent a
policy that its delegated owner does not implement.

### Quantitation truth table

| Surface | #240/#241 exact-parity value | Final decision owner |
| --- | --- | --- |
| Boundary placement | Gaussian15-smoothed positive AsLS residual | `xic_extractor/peak_detection` |
| Shape evidence | own-max relative-intensity shape on the smoothed trace | peak facts plus the named stage policy |
| Evidence quantitation fact | Gaussian15 positive-residual area with complete identity/provenance | `xic_extractor/peak_detection` |
| `AlignedCell.area` / Matrix value | current compatibility projection, kept distinct from the Gaussian fact | #250 expected-diff gate plus existing activation/writer owner |
| Legacy Standard Peak area | falsification/expected-diff reference only | offline oracle |

Smoothing remains mandatory; no smoothing ablation is requested. #250 may
close either by retaining the current compatibility projection or by promoting
the Gaussian value through an approved cell-level expected diff. Neither result
allows a refactor to disguise a scientific value change.

The original parent refreeze is recorded at
https://github.com/Chao-hu-Lab/XIC_Extractor/issues/238#issuecomment-5252117893;
the review correction and policy-adoption boundary are tracked by #252.

## Performance diagnosis baseline

The current-head performance verdict is `baseline_needed`, not “faster.” Static
call-path inspection bounds the topology, owner-derived telemetry names the
counts that a later run can render, and retained runs falsify the assumption
that a local no-RAW speedup necessarily improves the complete target stage.

- Retained no-RAW replay: 13.546 s to 9.201 s, with 676 best-shift summaries
  byte-identical and 1,352 auxiliary artifacts skipped.
- Retained complete 8RAW stage: shift batch 18.903 s to 19.123 s and chunk
  26.033 s to 26.742 s; calibration moved 3.099 s to 2.403 s. Publication still
  passed with 210 writes and unchanged public hashes.
- Current-head wall time is deliberately `unknown`. It is
  `direction_changing=FALSE` for T1 because T1 is a no-write exact-behavior
  owner move. A fresh measured baseline becomes direction-changing before a
  production cutover and belongs to #241.
- No fixed percentage promotion threshold is used. Full-path evidence and
  correctness gates decide whether later work proceeds.

The detailed accounting and evidence status are in
`call_cost_baseline_v1.tsv`. Static rows say `not_observed`; they are not runtime
measurements.

## Sources and sealing

All code/test observations are sealed by the frozen Git revision above. Tracked
retained evidence is additionally pinned below so normal tests never depend on
ignored `output/` content.

| Source | SHA-256 | Use |
| --- | --- | --- |
| `docs/solutions/validation/standard-peak-best-shift-only-no-full-run-gain.md` | `7c33ffd9e9a0cbb3de8894de713aff9b32df77e4c4ce85b63f6c7f3fe0d0839d` | no-RAW and complete-stage timing falsification; LF-normalized text hash |
| `docs/superpowers/validation/quant_matrix_promotion_validation_packet_v2/artifacts/heldout_oracle__summary.json` | `4e90c569f6a9c408f45f637efaf81677337af3c252f37ab01285d5312e61f20a` | held-out boundary/area oracle result |
| `docs/superpowers/validation/quant_matrix_promotion_validation_packet_v2/artifacts/85raw_large_cohort__standard_peak_activation_inputs_summary.json` | `46e2fb03577cd7cf803442a029b029a8390eed584502fc7f7de98c5a6b293d8e` | 7,307/4,613 activation population |
| `docs/superpowers/validation/quant_matrix_promotion_validation_packet_v2/quant_matrix_promotion_packet_v2_summary.json` | `da676819bcde2738c9b2ea587900ec6e4e71089d51886233d4a4e0c4ff792d4d` | bounded promotion packet |
| `docs/superpowers/validation/quant_matrix_promotion_validation_packet_v2/quant_matrix_validation_evidence_summary.json` | `3539644b9c9472ccb36d27860e107a5b48cd3033bc07e4a591b1d9b9d8cc97f5` | validation evidence summary |
| `docs/superpowers/validation/quant_matrix_promotion_validation_packet_v2/real_bundle_readiness/quant_matrix_promotion_readiness_summary.json` | `5ce191aa4efd7d3119e5d58a92f3e12a98c5005d506c0e0a5f2570b9684c4a2f` | read-only 511-row bundle state |
| `docs/superpowers/validation/quant_matrix_default_product_activation_v1/quant_matrix_default_product_activation_summary.json` | `4dc030aafca63b02f213f86a3ae1f977788cb9fb522aaf789f5cc7464e056708` | actual 511-write activation summary |
| `docs/superpowers/validation/productization_status_index_v1.tsv` | `01f82c06dfc4d627565e20307512b7e418b6194bf45bde6186177c3d67edb993` | current productization status |
| `docs/superpowers/schemas/productization_authority_manifest.v1.json` | `cde7d135dda6c2b38bc6ee5c03da9012ae9bf936f57186dfec1c5808aea6f9d0` | authority manifest |
| `docs/finding_class_ledger.tsv` | `a98868fce414ea3d29797d074275ac546fb014ec37a328356190c12e3dd4ec57` | recurring classes A/B/C/D/E/G |
| `docs/diagnostic-ledger.md` | `76e652b89b498abdb23ad742c532ef5715768a830bd73825db99c8f689a1d5bc` | retained owner/cost evidence; LF-normalized text hash |
| `tools/diagnostics/INDEX.md` | `c7facd47b1ffd05124f5fa9055db931610098a21cd3db3dfd01e10ffa21e7c23` | retained diagnostic/oracle index |

Persisted artifacts remain untrusted projections after deserialization. The
packet records their observations, but a runtime consumer must still rehydrate
through the original semantic owner and recompute identity, counts, hashes, and
invariants before claiming authority.

## Method and complete population

The census starts at all current callers of
`run_standard_peak_backfill_preset(...)`: `scripts/run_alignment.py`,
`gui/workers/discovery_worker.py`, and
`tools/diagnostics/dna_dr_product_ready_stage_replay.py`. It then follows every
reachable value-, evidence-, authority-, artifact-, and publication-changing
branch. The packet uses `rule_disposition_v1.tsv` as the closed population.

| Population segment | Rule IDs | Characterization |
| --- | --- | --- |
| Entrypoints and preset resolution | SP-001–SP-002 | caller contract and pre-standard artifact selection |
| 1. Retained gate and review queue | SP-003–SP-007 | eligibility, exclusions, seed identity, evidence states, queue routing |
| 2. RAW overlay acquisition/reuse | SP-008–SP-016 | sample-local batch topology, receipts, cache sealing, trace facts, family policy, rendering |
| 3. Reconciliation groups | SP-017 | global group index remains stage policy |
| 4. Shift-aware evidence | SP-018–SP-020 | trace availability, source-family best shift, optional artifacts |
| 5. Calibration pack and thresholds | SP-021–SP-022 | population and threshold remain calibration policy |
| 6. Standard Peak machine gate | SP-023–SP-024 | machine conjunction and independent/manual oracle |
| 7. MS1 authority bundle | SP-025–SP-029 | authority mode, rescued scope, trace seal, floor, sidecar authority |
| 8. Shadow projection | SP-030–SP-036 | full population, current-state re-derivation, HOLD/REJECT/value/identity |
| 9. Productization | SP-037–SP-046 | activation, uniqueness, seed guard, schema, writer, policy/oracle, exact diff |
| 10. Chunk consolidation | SP-047–SP-051 | summary validity, queue coverage, conflict, global projection, formal output |
| 11. Publication and stage replay | SP-052–SP-055 | backups/hashes, no-op cleanup, copied-input seal, publication check |

Rendering-only work is isolated in SP-016 and SP-020. The evidence-only route
produces the same semantic evidence without those artifacts; therefore those
branches cannot change evidence, policy, authority, selected values, identity,
or publication.

## Architecture preflight

### Existing owner and helper reuse

The proposed shared owner is the existing `xic_extractor/peak_detection`
package:

- `ms1_trace_detection.detect_ms1_trace_peak` owns finite trace validation and
  detected/not-detected/unassessable outcomes.
- `ms1_morphology` owns Gaussian15 smoothing, positive AsLS residual, and
  positive area in seconds.
- `ms1_shape_identity` owns own-max normalization, local shape similarity, and
  competing-peak summaries.

`xic_extractor.alignment.matrix_handoff.integration_from_peak_trace` remains an
Alignment compatibility adapter. Discovery must not import it. T1 may deepen
or compose the peak owner and keep that adapter compatible; it may not create a
parallel Standard Peak owner.

### Rules that do not migrate

- Retained-gate inclusion, family verdicts, source-family best-shift policy,
  calibration thresholds, and seed guards remain stage policy.
- Review queues and manual/held-out packs remain review/oracle orchestration.
- The MS1 authority bundle remains the only Standard Peak path that can mint
  `product_authorized` evidence for its exact cell scope.
- Shadow projection remains non-writing context until exact authority exists.
- Productization, expected-diff audit, Matrix writer, consolidation, and
  publication remain with their existing owners.

The shared facts therefore grant no Matrix/write authority and cannot collapse
HOLD, REJECT, technical-unavailable, or authority-denied states.

### Public-contract risk, gate, and stop rule

- Public-contract risk for T0: none; no production files change.
- T1 gate: exact no-RAW behavior and fail-closed compatibility only.
- Production-cutover gate: owner-derived counts plus full correctness evidence
  in #241 and later frozen children.
- Stop if any fact needs a new owner/type/public schema, changes a threshold or
  authority, imports Alignment into Discovery, trusts a partial universe, or
  requires RAW.

## Findings

### Workflow-neutral peak facts

Four fact families are safe candidates for the existing peak owner:

1. finite sample-local trace identity, availability, apex, and detected state;
2. Gaussian15-smoothed trace and positive residual/area facts;
3. own-max normalized shape and local/absolute similarity; and
4. coverage, local/global dominance, competing-peak, and interference metrics.

Each has a positive case, counterexample, retained oracle, and fail-closed
behavior in the rule census. No threshold, family verdict, source-family shift,
activation scope, or write decision is included in these facts.

### State separation

- Technical unavailable: required trace/artifact cannot be assessed; no
  scientific negative is inferred.
- HOLD/review: evidence exists but is missing, conflicting, or insufficient for
  the stage question.
- REJECT: the named question was evaluated and contradicted or hard-blocked.
- Authority denied: supportive evidence exists but lacks the exact authority,
  scope, seal, threshold, or seed attribution required to write.

These states have explicit rows and are mechanically checked as distinct.

### Recurring class closure

| Class | T0 answer |
| --- | --- |
| A — owner recomputation | Caller-authored counts, IDs, status, hashes, and locators are assertions. Overlay acquisition owns receipts; semantic owners rehydrate complete content; expected-diff and publication rederive actual state. |
| B — physical identity | Shared fact identity is sample-local trace plus family/peak context and canonical arrays/bounds. Authority identity remains `peak_hypothesis_id × sample_stem × sealed source row/artifact`, with uniqueness checked before write. |
| C — complete population | The rule census is exact and tested; preset queue ranks, shadow population, policy oracle coverage, and consolidation coverage fail closed on missing/duplicate rows. |
| D — canonical form | Existing owners keep accepted spellings, enum/status vocabulary, canonical TSV columns, lowercase SHA where required, and normalized paths. T0 adds no alternate spelling. |
| E — declared fields | Packet TSV schemas are exact; missing or undeclared semantic fields fail. Existing runtime owners validate their own complete payloads rather than accepting partial caller projections. |
| G — receipt sealing | Git revision and tracked SHA seal characterization. Runtime cache/authority/publication receipts remain owner-minted and invalidate on request, artifact, source, path-stat, schema, count, or hash mismatch. |

No authoritative raw `findings.tsv` is tracked in the checkout. The following
retained receipt freezes the local reviewer-os census that corrected the
earlier “no current collector evidence” statement:

| Field | Frozen value |
| --- | --- |
| Run date/repository | 2026-08-11; `Chao-hu-Lab/XIC_Extractor` |
| Exact 30-PR universe | 174,176,178,181,182,183,187,188,189,190,192,194,204,206,207,209,211,214,215,216,225,227,228,229,230,231,233,235,237,249 |
| PRs with retained finding rows | 174,176,182,183,188,189,190,192,194,204,206,207,215,225,228,229,230,231,233 |
| Collector result | 122 non-reply review findings; raw TSV SHA-256 `48b613fe0dd0cdc042a44e83467805f5aa6636dc28b54fe2b0fcebe3c7632ed3` |
| reviewer-os revision | `f93fa90450b99263c0616b7a5655f3e363a20c47` |
| `fetch_findings.py` SHA-256 | `700a9abde28ee0ab2b04c26b3509c28926b1c2080607361caf2587e1a69a7f26` |
| `class_census.py` SHA-256 | `bcb9bcb95022507e52f962efbcdf1071c08adcc7226555a440fa0e20564892e1` |
| Inputs/options | collector `--repo Chao-hu-Lab/XIC_Extractor --pr <exact-universe>`, replies excluded; census against `docs/finding_class_ledger.tsv` |
| Census result | 122 findings, eight open classes, 35 ledger-unclassified instances |
| Retained summary locator | this receipt plus `historical_failure_matrix_v1.tsv` HF-013 and HF-018 |

The raw collector rows are intentionally not presented as a closed
clean-checkout oracle; only their exact universe, tool identity, digest, and
bounded summary are retained. The five applicable programme-level gaps are
HF-014–HF-018: non-causal dependencies, missing successor allocation,
quantitation conflict, bound-recipe regression, and false policy-owner
allocation. The other 35 instances remain missing classification evidence.
They do not establish a defect in the no-write SP-011–SP-014 fact move, but
they prevent any claim that whole-route review history is closed.

## Assumptions and limitations

- Code/test behavior is characterized only at the frozen revision. A descendant
  must refreeze or re-run this packet before relying on it.
- Historical 8RAW/85RAW artifacts are retained evidence, not fresh T0 runs and
  not authority for a new scientific claim.
- The old 909.689 s / 1,843,631-XIC Owner Backfill diagnostic describes a
  broader stale-revision path, not the current Standard Peak preset baseline.
- Current-head wall time, hardware noise, and end-to-end effect size remain
  unknown. They are intentionally deferred because T0/T1 are no-write and no-RAW.

## Smallest next evidence loop

The corrected parent `plan-spec-review` authorizes only #240 after PR #249
merges green. Activate #240 from the then-current `master`, rerun its required
performance and architecture preflights, and perform an exact no-RAW owner
deepening: make the four fact families callable from `peak_detection`, use the existing
`alignment.matrix_handoff.integration_from_peak_trace` path as the first
exact-parity consumer, preserve all stage policies, and prove byte/decision
parity with focused tests. Do not begin T1 in this worktree.

The first measured production-cutover loop is #241: run the internal shadow
consumer with owner-derived acquisition/trace/artifact counts and compare the
complete route, not a microbenchmark. RAW remains gated separately and no 85RAW
run is implied by this packet. The later route cannot reach #251 until #250
closes the quantitation projection and #252 closes the stage-policy adoption;
a non-promoting #252 terminal returns the route to #238 instead of guessing.

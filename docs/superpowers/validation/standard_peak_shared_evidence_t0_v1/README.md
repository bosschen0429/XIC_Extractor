# Standard Peak shared-evidence T0 characterization v1

## Status

- Issue: GitHub #239, child of #238; architecture parents #113 and #159.
- Frozen source revision: `db3fa5e6c1d2756afe1c600b57a81ef17aef2443`.
- Characterization branch: `codex/issue-239-standard-peak-characterization`.
- Evidence label: `diagnostic_only / no-write`.
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

The reusable physical facts already fit the existing
`xic_extractor/peak_detection` owner, while family policy, review routing,
Backfill authority, artifact finalization, and Matrix publication remain with
their current owners; no duplicate semantic owner is needed.

Terminal disposition: PROMOTE_TO_T1

This disposition authorizes only the already-frozen no-RAW T1 characterization
and owner-deepening scope in #240. It does not activate a downstream issue,
authorize production behavior, or authorize a Matrix write.

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
| `docs/solutions/validation/standard-peak-best-shift-only-no-full-run-gain.md` | `ca69646c6140692eac97af93472904bb909cab6334903a5dc11957408e15940e` | no-RAW and complete-stage timing falsification |
| `docs/superpowers/validation/quant_matrix_promotion_validation_packet_v2/artifacts/heldout_oracle__summary.json` | `4e90c569f6a9c408f45f637efaf81677337af3c252f37ab01285d5312e61f20a` | held-out boundary/area oracle result |
| `docs/superpowers/validation/quant_matrix_promotion_validation_packet_v2/artifacts/85raw_large_cohort__standard_peak_activation_inputs_summary.json` | `46e2fb03577cd7cf803442a029b029a8390eed584502fc7f7de98c5a6b293d8e` | 7,307/4,613 activation population |
| `docs/superpowers/validation/quant_matrix_promotion_validation_packet_v2/quant_matrix_promotion_packet_v2_summary.json` | `da676819bcde2738c9b2ea587900ec6e4e71089d51886233d4a4e0c4ff792d4d` | bounded promotion packet |
| `docs/superpowers/validation/quant_matrix_promotion_validation_packet_v2/quant_matrix_validation_evidence_summary.json` | `3539644b9c9472ccb36d27860e107a5b48cd3033bc07e4a591b1d9b9d8cc97f5` | validation evidence summary |
| `docs/superpowers/validation/quant_matrix_promotion_validation_packet_v2/real_bundle_readiness/quant_matrix_promotion_readiness_summary.json` | `5ce191aa4efd7d3119e5d58a92f3e12a98c5005d506c0e0a5f2570b9684c4a2f` | read-only 511-row bundle state |
| `docs/superpowers/validation/quant_matrix_default_product_activation_v1/quant_matrix_default_product_activation_summary.json` | `4dc030aafca63b02f213f86a3ae1f977788cb9fb522aaf789f5cc7464e056708` | actual 511-write activation summary |
| `docs/superpowers/validation/productization_status_index_v1.tsv` | `01f82c06dfc4d627565e20307512b7e418b6194bf45bde6186177c3d67edb993` | current productization status |
| `docs/superpowers/schemas/productization_authority_manifest.v1.json` | `cde7d135dda6c2b38bc6ee5c03da9012ae9bf936f57186dfec1c5808aea6f9d0` | authority manifest |
| `docs/finding_class_ledger.tsv` | `a98868fce414ea3d29797d074275ac546fb014ec37a328356190c12e3dd4ec57` | recurring classes A/B/C/D/E/G |
| `docs/diagnostic-ledger.md` | `06bc12287bd408fc7c4e79de60c5586e35cebed4cb2c2742e1cd08e1582ebf72` | retained owner/cost evidence |
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

No authoritative current `findings.tsv` collector artifact exists in the tracked
checkout. That absence is recorded as missing evidence in
`historical_failure_matrix_v1.tsv`; the authoritative class ledger above is
still fully mapped. Discovery of a current collector that contradicts this
packet stops promotion and returns the decision to #238.

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

After this packet, update #238 and rerun `plan-spec-review`. If that review keeps
the parent frozen answers intact, #240 may perform T1 as an exact no-RAW owner
deepening: make the four fact families callable from `peak_detection`, preserve
the Alignment adapter and all existing stage policies, and prove byte/decision
parity with focused tests. Do not begin T1 in this worktree.

The first measured production-cutover loop is #241: run the internal shadow
consumer with owner-derived acquisition/trace/artifact counts and compare the
complete route, not a microbenchmark. RAW remains gated separately and no 85RAW
run is implied by this packet.

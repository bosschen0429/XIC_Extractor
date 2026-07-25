# CID-NL Oracle Packet Preflight v1

Status: `no_raw_contract_complete_formal_execution_blocked`.

This no-RAW preflight answers one bounded Issue #125 question: can the available
CID-NL metadata already support a blinded formal holdout and recovery claim?

## Result

The no-RAW acceptance contract is implemented, locally verified, and
independently approved after hosted-review remediation and a same-class
fail-closed sweep. Formal evaluation still cannot run yet. Two different
evidence sets remain explicit.

The legacy machine-conditioned context contains:

- 511 unique source/successor/sample authority decisions;
- 87 source-to-successor transitions across 85 samples;
- 147 `write_authorized` legacy cells;
- 337 detected-baseline context cells;
- 27 omitted no-target cells;
- 78 paired-overlay summary locators;
- 2,595 Matrix identity rows.

These rows are not the tested tracer's proposal denominator or oracle truth.
The tracked 85RAW universe-closure packet proves only that the old authority
universe is internally closed.

The target configuration and owner-confirmed experimental design provide a
separate, partial independent reference source:

- seven isotope-labelled internal standards are physically expected in every
  biological and pooled-QC injection;
- six have NL116 and are expected-positive controls for the current NL116-only
  Untargeted Discovery method;
- `[13C,15N2]-8-oxo-Guo` has NL132 and is physically present but outside the
  current Untargeted method scope;
- every paired analyte has unknown presence and must be judged from its own
  evidence.

The seven anchors are recorded in
`cid_nl_oracle_reference_anchor_manifest.tsv`. The bounded 12-case calibration
has now been independently labelled, sealed before machine reveal, and
reintegrated for boundary-area sensitivity. These calibration cases still do
not constitute a formal holdout reference universe because no formal blinded
case/sampling manifest is bound to them.

## Sample-Material Boundary

The two known DNA-and-RNA mixed biological samples are:

- `TumorBC2286_DNAandRNA`;
- `TumorBC2304_DNAandRNA`.

All other biological samples are DNA-only. Pooled QC samples are a separate
stratum; their DNA/RNA composition is not inferred from their names.

`8-oxo-Guo` is RNA-applicable in the two mixed biological samples, but
applicability does not assert presence. Its paired labelled internal standard
is expected to be physically present in all injections, while remaining outside
the NL116-only Untargeted proposal scope.

External spectral-library comparisons must preserve both sample-material and
neutral-loss scope. An RNA or NL132 record cannot become an expected-positive
case for a DNA-only biological sample or the current NL116-only tracer.

## Integration Boundary

This oracle judges consistent baseline treatment, attributable peak choice,
acceptable boundaries, and deterministic integration. It does not require the
absolute internal-standard concentration or equal observed area across
biological and pooled-QC injections. The pooled QC contains all seven internal
standards, but pooling, matrix, and preparation effects may change effective
concentration or response.

## Remaining Blockers

Formal holdout execution remains blocked by:

- no populated and sealed tested-tracer proposal manifest because a specific
  tracer run does not yet exist;
- no populated and sealed independent reference universe or formal blinded
  case/sampling manifest;
- no populated and sealed preregistered NARROW-scope membership manifest;
- seven preregistered statistical parameter values are not yet frozen because
  their values require observed prevalence, independent group counts, reviewer
  calibration, and intended claim precision;
- no populated and sealed canonical #123 processing-lineage binding;
- no formal-holdout evidence/review bundle;
- calibration numeric tables are tracked, but the 12 blind plot bytes still
  lack a durable portable archive locator;
- no #128 performance budget.

The boundary/area comparison is no longer a blocker. The frozen holdout rule
requires the machine interval to remain inside the conservative human envelope
and retain at least `98%` of its deterministically reintegrated area. This is a
validation tolerance, not a runtime threshold; the active 2% sustained
baseline-return rule is unchanged.

Therefore proposal-conditioned precision, formal recovery, dataset
false-positive rate, and false-negative rate remain unavailable.

## Artifacts

- `cid_nl_oracle_reference_anchor_manifest.tsv`: seven physical-presence
  anchors and method-specific expected-detection scope.
- `cid_nl_oracle_gate_table_v1.tsv`: 25-gate inventory with 15 frozen gates,
  one frozen completeness rule whose inputs arrive later, seven named
  preregistered parameters, one calibration-retention dependency, and one #128
  external performance parameter.
- `cid_nl_oracle_verdict_table_v1.tsv`: total, precedence-ordered
  GO/NARROW/REDRAW/`inconclusive` decision function.
- `cid_nl_oracle_synthetic_contract_suite_v1.tsv`: 25 no-RAW state, policy,
  boundary, replay, denominator, and scope cases.
- `cid_nl_oracle_owner_policy_seal_v1.json`: owner, time, scope, rationale, and
  decision hashes for the two explicitly frozen policy values.
- `independent_spec_review.md`: durable fixed-snapshot review history and final
  approval of the hosted-review remediation snapshot.
- `cid_nl_oracle_packet_preflight_summary.json`: decision, observed context,
  partial anchors, and blockers.
- `cid_nl_oracle_packet_preflight_sources.tsv`: portable source locators,
  hashes, retention status, and bounded claim roles.
- `cid_nl_oracle_packet_preflight_checks.tsv`: established facts and remaining
  fail-closed blockers.

The four legacy source artifacts are ignored local inputs under their existing
`output/` paths. This packet records their observed SHA-256 values, but does not
retain their bytes. The paired-overlay source contains summary locators only;
it is not a replayable overlay evidence bundle.

The bounded RAW-backed calibration now has a separate tracked packet at
`../cid_nl_oracle_istd_calibration_2raw_v1/`. Its sealed human labels,
human-versus-machine comparison, and area-sensitivity rows are tracked
byte-for-byte. The 12 blinded plot bytes and scan-level evidence remain in
ignored task output and are hash-bound but not yet durably portable; the
formal portability gate remains `inconclusive`.

## Next Evidence Loop

The proposal, formal sampling, reference-universe, review/adjudication, and
narrow-scope, lineage schemas and sealing protocols are now defined and backed
by executable negative validators. Populate and seal them
only after a specific tracer, independent reference frame, and preregistered
case design exist. Before formal review, freeze values for the seven named
statistical parameters and inherit the #128 performance budget. The full
510-cell ISTD non-inferiority gap remains at at most two Untargeted deficits
relative to Targeted, and automated coverage remains at least 90%. The
completed calibration does not need another RAW replay.

## Authority

This packet remains `diagnostic_only`. A bounded 2RAW calibration ran, but it
changed no selected area, counted detection, Matrix, ProductWriter, workbook,
GUI, activation, export, or compound-identification authority.

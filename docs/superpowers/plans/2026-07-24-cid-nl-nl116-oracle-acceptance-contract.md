# CID-NL / NL116 Blinded Oracle And Tracer Acceptance Contract

Doc placement: repo_support_doc
Doc kind: spec
Doc lifecycle: active
Repo owner: docs/product/discovery.md
Doc exit rule: Mark implemented, rejected, or retired after #125 closes and
durable oracle rules are absorbed into the Discovery product owner.

Date: 2026-07-24
Issue: [#125](https://github.com/Chao-hu-Lab/XIC_Extractor/issues/125)
Parent map: [#118](https://github.com/Chao-hu-Lab/XIC_Extractor/issues/118)
Validation status: `diagnostic_only`

## Problem

The CID-NL / NL116 successor tracer needs an independent way to judge whether
it found the right feature, selected the right peak and quantitative region,
and applied Targeted and Untargeted policy correctly.

Targeted output, legacy output, machine scores, and prior machine calls cannot
serve as their own oracle. Without a blinded and replayable oracle, a successor
can appear successful by reproducing an old decision, overfitting a review set,
or hiding identity and policy errors inside one average score.

The long-term operating goal is machine judgment for routine batches. Human
review establishes, calibrates, and periodically audits the oracle; it is not a
per-batch approval requirement.

## Success

This issue is complete when it defines an implementation-ready packet contract
that can:

1. independently adjudicate feature identity, peak/integration quality, and
   Targeted/Untargeted policy outcome;
2. measure proposal-conditioned precision and recovery against a bounded
   reference universe without allowing the tested tracer to define its own
   denominator;
3. produce a preregistered GO, NARROW, or REDRAW decision without granting
   product-writing authority.

Exact case counts, folds, tolerances, and model thresholds remain calibration
outputs. They are not guessed in this contract.

The deterministic oracle packet is the required #125 deliverable. The ML annex
does not block a deterministic tracer unless that tracer makes an ML,
new-batch-automation, or unseen-family-generalization claim.

## Authority Boundary

The oracle and all artifacts described here remain `diagnostic_only`.

They grant no:

- Matrix authority;
- Backfill writer authority;
- counted-detection authority;
- selected-area authority;
- workbook or GUI authority;
- activation, export, or `ProductWriter` authority;
- compound-identification claim beyond the evidence actually present.

Passing this contract supports planning and bounded tracer evaluation only.

## Non-Goals

- Choosing an ML architecture or feature representation.
- Requiring ML for the first deterministic tracer.
- Proving compound identity from CID-NL/NL116 or peak shape alone.
- Defining #128 runtime ownership, cancellation, retry, cleanup, or cache
  lifecycle.
- Replacing lane-specific promotion and expected-diff contracts.
- Requiring a human to review every future batch.
- Selecting a universal sample count, fold count, or tolerance before
  calibration.

## Independent Judgment Dimensions

Each case receives separate judgments. One global accept/reject label is not
enough.

### 1. Feature / `PeakHypothesis` identity

Question: does the available evidence support a reproducible, attributable
chromatographic feature and CID-NL/NL116 `PeakHypothesis`?

Without an authentic standard or another accepted identification profile, this
does not establish a named compound or molecular structure.

### 2. Peak, apex, boundary, and area

Question: is the selected sample-local peak attributable to the hypothesis, and
are its apex and left/right boundaries acceptable?

The human reviewer does not type a visual "true area." The reviewer confirms or
corrects the peak and boundaries; a frozen deterministic integrator calculates
the adjudicated area.

This packet judges integration correctness, not absolute-concentration
accuracy. A consistent baseline policy, attributable peak, acceptable
boundaries, and deterministic integration are sufficient for the area
judgment. Internal-standard concentration, calibration curves, linearity,
LOD/LOQ, and absolute quantification are outside this tracer oracle.

If feature identity is rejected or no applicable peak exists, this dimension is
`not_applicable`, not `unassessable`.

### 3. Policy outcome

The oracle inherits the canonical shared-kernel axes:

| Axis | Values / reference |
| --- | --- |
| `observation_state` | `observed`, `missing`, `absent`, `unassessable`, `ambiguous`, `failed` |
| `observation_origin` | `discovery`, `alignment`, `backfill` |
| `selection_state` | selected-hypothesis decision/audit, or no selection |
| workflow projection | Targeted and Untargeted outcome below |

Targeted and Untargeted workflow projections are adjudicated separately using:

- `select_value`;
- `leave_missing`;
- `hold`.

`review` is a workflow state before human adjudication. `unassessable` is a
completed `observation_state`, not a projection outcome. `failed` is not
`absent`; `ambiguous` is not silently reduced to `leave_missing`.

Fine-grained morphology and method-development details remain reason codes.
Observation state, origin, selection, and projection remain orthogonal
machine-readable fields.

The minimum projection crosswalk is:

| Observation / selection | Allowed projection |
| --- | --- |
| `observed` with an eligible selected hypothesis | `select_value` or policy-specific `hold` |
| completed `absent` | `leave_missing` |
| pre-evaluation/incomplete `missing` | `hold` |
| `unassessable`, `ambiguous`, or `failed` | `hold` |

Policy-specific gates may narrow `select_value` to `hold`; they cannot upgrade a
non-observed state to `select_value`.

## Evidence Claim Boundary

Evidence roles remain distinct:

- authentic standard, attributable library MS2, isotope-labelled internal
  standard, or a separately frozen profile can provide stronger identity
  support;
- blinded RAW/EIC/MS2 review adjudicates the available case evidence;
- Targeted and legacy outputs are comparison benchmarks, not truth;
- synthetic cases test mechanics and policy boundaries, not real-world
  recovery or false-positive rates.

Physical presence and method detectability remain separate. The seven
isotope-labelled internal standards are fixed-spike expected-presence controls
for every biological and QC injection in the 85RAW fixture. Their paired
analytes are not expected-presence controls: each analyte must be judged from
its own sample evidence. A paired internal standard may support RT, identity,
peak selection, and run-quality interpretation; it cannot upgrade an analyte
to `observed` or `select_value`.

The current Untargeted Discovery acceptance scope is NL116-only. Six internal
standards with `neutral_loss_da=116.0474` are method-applicable expected
detections. `[13C,15N2]-8-oxo-Guo` is physically present but has
`neutral_loss_da=132.0423`; its absence from an NL116-only proposal set is an
explicit method-scope exclusion, not a tracer false negative. Targeted can
search both neutral losses from per-target configuration, but that broader
Targeted capability does not silently expand the Untargeted acceptance scope.

Uniform fixed spiking establishes expected presence, not equal observed area.
The pooled QC injections contain all seven internal standards, but their
effective concentration or response is not required to equal individual
biological samples. Matrix, preparation, and pooling effects remain observable
evidence rather than oracle failure.

Strong benchmark anchors do not transfer compound-identification confidence to
cases that lack the same evidence.

`leave_missing` is the workflow projection of a completed `absent` observation:
the declared RAW, m/z/RT window, MS1/MS2 evidence, and method capability contain
no sufficiently attributable value. It is not a claim of biological absence.

## Blinded Review Procedure

### Calibration

A small calibration set establishes:

- reviewer fields and instructions;
- visual evidence sufficiency;
- boundary and area comparison procedure;
- use of `unassessable`;
- candidate tolerance proposals.

Calibration cases do not count toward final tracer performance.

After calibration, the review schema, deterministic integration method,
acceptance metrics, tolerances, and GO/NARROW/REDRAW rules are frozen before
formal holdout review.

### Frozen boundary and area comparison

The 2RAW × 6 NL116 internal-standard calibration freezes the following
formal-holdout comparison for cases where the human reviewer accepts feature
identity and records an observed peak:

- the human left/right coordinates are a conservative outer envelope of the
  visibly attributable peak, not exact threshold-crossing truth;
- the machine interval must be contained within that human envelope;
- the machine area, recomputed with the same frozen trace morphology and
  deterministic integrator, must retain at least `98%` of the area inside the
  human envelope.

Coordinate equality, fixed minute deltas, and boundary IoU are descriptive
diagnostics, not pass/fail gates. The `98%` retained-area floor is a
formal-holdout validation tolerance; it is not a new runtime boundary threshold
and does not replace the active `2%` sustained baseline-return rule.

The calibration observed a minimum retained-area ratio of `98.9616%` across 12
accepted cases. The owner judged the resulting tail-area differences
acceptable and preferred one reproducible machine policy over repeated manual
boundary placement. The formal holdout tests this frozen rule once. A holdout
failure is not repaired by tuning the rule against revealed holdout outcomes.

The sealed human labels, human-machine comparison, and area-sensitivity tables
are tracked. The 12 blind plot bytes remain hash-bound local evidence without a
durable portable archive. Until those existing bytes are pinned and bound to a
retention seal, `calibration_evidence_portability` is `inconclusive` and no
formal GO may inherit the `0.98` gate. Closing this portability gap requires no
new RAW replay.

### First blinded pass

The reviewer may see:

- sample and candidate coordinates;
- expected neutral loss/product coordinates and RT search range;
- full-window and local EIC views;
- relevant MS1/MS2 scans and acquisition metadata;
- neighbouring candidates and possible ownership conflicts;
- declared missing evidence.

The reviewer must not see:

- scores, component scores, rankings, reason codes, suggested peak/apex/boundary
  decisions, threshold outcomes, or calls from the tested tracer,
  legacy/Targeted pipeline, or any other evaluated system;
- counted-detection or selected-area result;
- any comparison field derived from an evaluated-system output.

The reviewer independently records feature identity and sample-local
peak/boundary judgment.

The machine-readable review and append-only adjudication owner is
[`cid_nl_oracle_review_adjudication_schema.v1.json`](../schemas/cid_nl_oracle_review_adjudication_schema.v1.json).
It supports the current single-domain-reviewer constraint through hidden
duplicates without inheriting the older lockbox requirement for two human
reviewers per case.

### Cross-sample policy pass

After the sample-local judgment is sealed, the reviewer may see the aligned
cross-sample context for the same `PeakHypothesis`.

This pass adjudicates Targeted and Untargeted policy, including whether
evidence-based re-extraction/Backfill is allowed. Cross-sample support cannot
invent a sample-local peak that was not observed.

### Unblinded adjudication

The confirmatory scoring label is frozen before any machine or legacy result is
revealed. Machine and legacy results may then be revealed for failure taxonomy
and method-development analysis only. This unblinded pass cannot change the
confirmatory label.

Packet integrity checks and every case-level invalidation decision are completed
before reveal by the still-blinded reviewer or by a custodian who cannot see
machine/legacy outcomes. The valid-case manifest and its denominator are then
sealed and hashed.

If corruption, misbinding, or missing required evidence is discovered only
after reveal, that case is not selectively removed and the frozen metric is not
recomputed. The affected gate fails closed as `inconclusive`; a quantitative
claim requires a new preregistered packet and seal. The event records the
reason, reviewer or custodian, time, affected gate, and superseding packet when
one exists.

Any correction needed before reveal remains append-only. Original verdicts are
never overwritten.

### Reviewer repeatability

The formal packet includes a small number of hidden duplicate cases separated
in presentation order. Only inconsistent judgments require adjudication.

Each underlying `case_id` remains one primary analysis unit. Before review, the
manifest designates one presentation as primary and links every hidden duplicate
to that same `case_id`. Only the preregistered primary presentation may enter
precision, recovery, disagreement, `unassessable`, confidence-interval, or gate
denominators. Duplicate presentations and any duplicate-triggered adjudication
are reported only as repeatability/adjudication evidence; they never add weight
to the underlying case or revise the frozen denominator.

The duplicate fraction and stopping rule are frozen during calibration; the
packet does not expand human work merely to reach a cosmetically large count.

### Review custody and roles

The packet manifest names:

- oracle owner;
- holdout custodian;
- domain reviewer;
- packet producer;
- gate runner.

One person may occupy multiple roles in the current solo-research setting, but
the manifest records that limitation and prevents the domain reviewer from
seeing the sealed machine mapping before label freeze.

A second qualified domain reviewer is not assumed to exist. If one becomes
available, calibration and hard/ambiguous cases receive independent double-
blind review under a preregistered consensus rule. Otherwise, hidden duplicates
and blinded self-recheck measure intra-reviewer repeatability; the absence of
inter-reviewer evidence is reported as a limitation on the claim.

## Packet Composition

The packet combines two complementary sets.

### Bounded dataset and independent reference universe

Before sampling, the packet freezes:

- dataset and `AcquisitionSource` identities;
- eligible samples, scans, m/z/RT space, and evidence scope;
- inclusion and exclusion rules;
- independent reference-source types;
- source hashes and universe-construction procedure.

The tested tracer does not define the reference universe. Reference positives
may come from authentic/labelled standards, accepted internal-standard or
profile anchors, an independently generated benchmark, or blinded systematic
review of the bounded search space. Each source retains its own claim strength.

The 85RAW sample-material strata are also frozen:

- `TumorBC2286_DNAandRNA` and `TumorBC2304_DNAandRNA` are the two known
  DNA-and-RNA mixed biological samples;
- all other biological samples are DNA-only;
- pooled QC samples are a separate stratum and are not inferred to be DNA-only
  or RNA-containing from their names.

RNA-applicable analyte hypotheses such as `8-oxo-Guo` may be evaluated for
sample applicability in the two mixed biological samples. Applicability does
not assert that the analyte is present. QC analyte presence is judged from
evidence or a separate QC composition contract, not inferred from pooling.
External spectral-library comparisons must preserve these material and
neutral-loss strata; an RNA or NL132 library record cannot become an
expected-positive case for a DNA-only sample or the current NL116-only tracer.

Two denominators remain separate:

- proposal-conditioned precision / false-positive behavior over tracer
  proposals;
- reference-universe recovery over independently supported positives,
  including positives the tracer never proposed.

The tested-tracer proposal manifest has two lifecycle steps. Issue #125 freezes
its required fields, lineage bindings, and sealing protocol before tracer
implementation. A populated proposal manifest cannot be created until a
specific tested tracer and normalized `ProcessingRequest` exist; it is frozen
after that tracer runs and before machine mappings are revealed or formal
holdout labels are opened. Missing populated proposals therefore block formal
holdout execution, not completion of the oracle contract itself. Placeholder
or legacy machine-conditioned rows must not be relabelled as tested-tracer
proposals.

The canonical shape is
[`cid_nl_tested_tracer_proposal_manifest_schema.v1.json`](../schemas/cid_nl_tested_tracer_proposal_manifest_schema.v1.json).
It records one `PeakHypothesis × AcquisitionSource × workflow_policy` row and
must include every candidate emitted by the sealed tracer attempt before any
denominator filtering. The proposal-conditioned positive denominator is
computed from frozen `select_value` rows. `hold` and `leave_missing` remain
auditable rows but are not positive proposals. Independent-reference recovery
uses the separate reference universe and can therefore count supported
positives that the tracer never proposed.

When a universe is sampled rather than exhausted, the manifest records the
sampling frame, stratum, inclusion probability, and analysis weight.
Candidate-conditioned precision is not reported as dataset-level false-positive
rate.

### Stratified challenge set

It deliberately covers:

- clear supported features;
- clear unsupported/noise cases;
- neighbouring peaks and ambiguous ownership;
- wrong apex, truncated local window, and incomplete boundaries;
- observed values and evidence-based re-extraction;
- true `leave_missing` cases;
- Targeted-only prior/evidence cases;
- expected Targeted/Untargeted divergence;
- hard policy and lineage guardrails.

### Random representative set

It is sampled from the frozen proposal and independent-reference universes to
estimate their corresponding precision, recovery, disagreement, and
`unassessable` rates. Recovery is never estimated from tracer proposals alone.

The reviewer is blinded to whether a case came from:

- machine accept;
- machine reject/hold;
- a matched real-data negative or near miss;
- a benchmark-positive source.

Matched negatives should resemble the candidate distribution in m/z, RT,
signal, and acquisition context so the reviewer is not cued by obvious empty
windows.

### Staged workload

The packet runs in bounded waves:

1. calibration;
2. first formal challenge and random wave plus hidden duplicates;
3. targeted expansion only when a case class is uncovered, a result is near a
   frozen decision boundary, or reviewer repeatability is insufficient;
4. stop when the evidence already determines GO, NARROW, REDRAW, or
   `inconclusive`.

Formal rules do not change between waves.

Cases added by step 3 are labelled `adaptive_supplemental`. Because their
selection can depend on first-wave results, they do not enter the frozen random
representative set's numerator, denominator, rates, confidence intervals, or
representative metric thresholds.

Adaptive cases may still diagnose an uncovered class, estimate reviewer
repeatability, or support a challenge-class decision whose evaluation unit,
rule, and threshold were frozen before unblinding. Any quantitative claim over
adaptive cases uses a separately reported estimand and denominator. Its
eligible frame, trigger, inclusion probability or analysis weight, uncertainty
method, and stopping cap must be preregistered before machine, legacy, or
interim results are inspected; otherwise the result remains descriptive only
and can inform a later preregistered packet, not revise this packet's frozen
representative metrics.

The canonical case/sampling shape is
[`cid_nl_formal_holdout_sampling_manifest_schema.v1.json`](../schemas/cid_nl_formal_holdout_sampling_manifest_schema.v1.json).
It records one blinded presentation per row while keeping `case_id` as the
primary analysis unit. Hidden duplicates are repeatability-only, and adaptive
supplemental rows cannot enter the frozen representative denominator.

Reference-universe construction follows
[`2026-07-25-cid-nl-reference-universe-protocol.md`](2026-07-25-cid-nl-reference-universe-protocol.md)
and
[`cid_nl_reference_universe_manifest_schema.v1.json`](../schemas/cid_nl_reference_universe_manifest_schema.v1.json).
The protocol freezes source eligibility, independent enumeration, material and
method scope, deduplication, sampling weights, review, sealing, and
post-reveal fail-closed behavior.

## Synthetic Contract Suite

Synthetic cases are scored separately from human RAW review and cover at least:

- one clean peak with correct apex and boundaries;
- no peak / noise;
- neighbouring peak and ambiguous ownership;
- truncated window, wrong apex, and wrong boundary;
- Targeted re-extraction allowed while Untargeted feature creation is not;
- `hold`, `leave_missing`, and `unassessable` never becoming detected values;
- deterministic identity, decision, and lineage on replay.

Synthetic success cannot establish scientific recovery or false-positive rate.

The executable no-RAW fixture is
[`cid_nl_oracle_synthetic_contract_suite_v1.tsv`](../validation/cid_nl_oracle_packet_preflight_v1/cid_nl_oracle_synthetic_contract_suite_v1.tsv).
It contains clean, absent/noise, neighbour ambiguity, wrong apex/boundary,
Targeted-prior, state-collapse, unassessable, replay, duplicate-weighting,
reference-independence, complete observation/projection crosswalk, and NL132
scope-exclusion cases. Focused tests apply
the frozen crosswalk and gate expectations; they do not emulate tracer
scientific performance.

## Evaluation Units

Results are reported at two levels:

- `PeakHypothesis` / identity-family level;
- `PeakHypothesis × sample × policy` level.

Sample-cells from the same hypothesis are not treated as independent compounds.
Uncertainty estimates and split manifests report the independent hypotheses,
families, batches, studies, and instruments represented.

## Statistical Design

The primary-source research owner is
[`2026-07-25-cid-nl-oracle-statistical-design-research.md`](2026-07-25-cid-nl-oracle-statistical-design-research.md).
Each formal estimand is frozen through
[`cid_nl_oracle_statistical_design_manifest_schema.v1.json`](../schemas/cid_nl_oracle_statistical_design_manifest_schema.v1.json).

The frozen design rules are:

- proposal precision and independent-reference recovery remain separate
  estimands and sampling frames;
- challenge-set composition is not deployment prevalence;
- a complete bounded census is reported descriptively for that finite frame;
  it does not receive a binomial sampling interval for itself;
- an independent equal-probability Bernoulli sample may use a preregistered
  Wilson or exact interval;
- stratified, weighted, or clustered samples require design-aware,
  cluster-aware uncertainty using the recorded inclusion probabilities and
  grouping keys;
- hidden duplicates estimate intra-rater repeatability only and retain their
  two pre-adjudication labels;
- automation reports coverage jointly with error among decided cases,
  positive-class coverage, and unconditional reference recovery;
- an incomplete reference frame supports only bounded known-reference
  recovery, not full-dataset sensitivity or false-negative rate.

Case counts follow the preregistered estimand, acceptable bound or interval
width, confidence level, design value, power, prevalence/stratum mixture,
cluster design, and review budget. No universal count is frozen by this
contract.

## Targeted / Untargeted Relationship

Both policies consume the same RAW evidence and shared evidence core.

- Targeted has a prespecified hypothesis and bounded scope. This is additional
  prior information, not permission to accept a lower-quality peak.
- Untargeted must establish a new hypothesis without the same prior.
- Ambiguous ownership, unusable boundaries, or insufficient sample-local
  evidence cause both policies to `hold`.

Acceptance uses:

- Targeted dominance on shared assessable cases;
- bounded Untargeted non-inferiority;
- explicitly explainable policy divergence.

Targeted should not have worse correct recovery, hard-error, peak, boundary, or
area behavior on in-scope paired cases. Untargeted may miss a preregistered
bounded subset when the only legitimate difference is Targeted prior support.
The allowable gap is frozen after calibration and before formal holdout.

For the full 85RAW expected-positive anchor gate, the six NL116 internal
standards define `85 × 6 = 510` sample-cells. The owner expects no practical
Targeted/Untargeted difference for these easy-to-recognize anchors and allows at
most two fewer correct Untargeted cells than Targeted. This relative gate does
not hide shared misses: absolute ISTD recovery remains part of the independent
reference-recovery gate.

The owner, capture time, exact scopes, rationales, and canonical decision hashes
for this two-cell tolerance and the 90% automated-coverage floor are retained in
[`cid_nl_oracle_owner_policy_seal_v1.json`](../validation/cid_nl_oracle_packet_preflight_v1/cid_nl_oracle_owner_policy_seal_v1.json).
The durable human-readable source record is
[`2026-07-25-cid-nl-oracle-owner-decisions.md`](2026-07-25-cid-nl-oracle-owner-decisions.md).

Raw Targeted/Untargeted agreement is not itself a success metric.

## End-To-End Responsibility Localization

The same oracle cases retain stage-level outcomes:

- Discovery: candidate / `PeakHypothesis` generation;
- Alignment: cross-sample identity linkage without false merge or split;
- Backfill/re-extraction: selection only after Discovery, Alignment, and
  sample-local evidence prerequisites;
- policy projection: final Targeted and Untargeted outcome.

The end-to-end result remains primary. Stage results locate the failed owner so
the project does not patch only the final projection.

## Hard Blockers And Tolerance-Based Metrics

The following cannot be averaged away:

- Targeted-only authority leaking into Untargeted;
- `hold`, `unassessable`, or insufficient evidence becoming a selected value;
- overwriting a trusted existing value;
- collapsing missing, failure, ambiguity, and detected states;
- tracer lineage/provenance mismatch under an otherwise valid oracle packet.

False accepted features contribute to proposal-conditioned precision rather
than acting as a zero-tolerance hard blocker. The owner prioritizes recovery of
true features because downstream feature filtering remains available and
false-positive control is expected to improve iteratively. This does not permit
authority leaks or non-observed states to become selected values.

Peak, boundary, area, precision, recovery, repeatability, reproducibility, and
performance use preregistered tolerances.

Routine automation may abstain or return `hold` on at most `10%` of primary
cases, so automated coverage must be at least `90%`. Human-adjudicated
`unassessable` remains a separate completed observation state and packet-
sufficiency metric; it is not folded into the machine-abstention rate.

`unassessable` is neither pass nor fail. Excessive `unassessable` means the
packet is insufficient and cannot produce GO.

A corrupt, misbound, unsealed, or prematurely revealed oracle packet invalidates
the evaluation and produces `inconclusive`; it is not evidence that the tracer
itself failed.

## Verdict

Calibration produces a machine-readable gate table. Every gate defines:

- `gate_id` and claim scope;
- evaluation unit and denominator;
- formula and threshold;
- required inputs and unknown handling;
- pass/fail outcome and failure verdict;
- precedence.

The decision function is total and fail-closed:

1. invalid oracle-packet integrity, blind-seal, or custody evidence produces
   `inconclusive`;
2. missing required evaluation input, insufficient independent units/precision,
   or excessive preregistered `unassessable` produces `inconclusive`;
3. any tracer hard-blocker gate failure under a valid oracle produces `REDRAW`;
4. a non-hard scientific or performance failure with a preregistered,
   evidence-supported narrower scope produces `NARROW`;
5. any remaining required-gate failure with no passing narrower scope produces
   `REDRAW`;
6. all required gates passing produces `GO`.

`NARROW` requires a positive bounded claim that passed its own gates. Mere lack
of evidence is `inconclusive`. The frozen gate table, not interpretation after
results are known, determines the verdict.

Every candidate NARROW claim must exist in a sealed
[`cid_nl_oracle_narrow_scope_manifest_schema.v1.json`](../schemas/cid_nl_oracle_narrow_scope_manifest_schema.v1.json)
manifest before machine mappings or holdout labels are revealed. The manifest
binds exact case membership, membership predicate, required gates, allowed
overall failed gates, independent-unit parameter, and precedence. The verdict
reducer derives scope pass from raw gate results. Without a passing sealed
scope, a non-hard scientific failure is `REDRAW`, not an invitation to choose a
successful subgroup afterward.

The machine-readable owners are:

- [`cid_nl_oracle_gate_table_v1.tsv`](../validation/cid_nl_oracle_packet_preflight_v1/cid_nl_oracle_gate_table_v1.tsv);
- [`cid_nl_oracle_verdict_table_v1.tsv`](../validation/cid_nl_oracle_packet_preflight_v1/cid_nl_oracle_verdict_table_v1.tsv).

Every gate now has a denominator, statistic, threshold kind, required inputs,
unknown outcome, failure verdict, precedence, freeze stage, and evidence basis.
Numerical values unsupported by calibration or an owner decision are named
preregistered parameters that must be sealed before formal holdout. They are
not `UNSET` runtime defaults. Issue #128 remains the external owner of the
performance parameter.

## ML-Ready Contract

ML is the intended long-term route to routine machine judgment, but it is not
required to make the first tracer valid.

This section and the ML validation estimands are a conditional annex. They
become required only for an ML automation, operational-generalization, or
unseen-family claim.

### Model responsibility

The first ML model is a shared evidence provider. It estimates:

- feature support;
- peak/apex/boundary quality;
- area reliability;
- uncertainty and out-of-distribution concern.

Explicit Targeted and Untargeted policy gates consume those outputs plus their
allowed priors. The shared model does not use Targeted/Untargeted mode as a
shortcut feature.

An end-to-end model may later run as a diagnostic challenger. It receives no
authority before independent evidence shows that it improves the simpler
shared-evidence design.

### Allowed inputs

An input manifest records each feature and when it is available during real
inference.

Allowed inputs may include:

- RAW-derived trace and scan evidence;
- candidate m/z/RT coordinates;
- peak morphology and MS1/MS2 support;
- allowed cross-sample context;
- frozen, unlabeled, deployment-matched QC calibration.

Forbidden inputs include:

- human verdict;
- scores, component scores, rankings, threshold outcomes, calls, or other
  intermediate/final decision outputs from the tested tracer, legacy pipeline,
  or any other evaluated system;
- counted detection or final selected area;
- post-review corrections;
- any field created only after the prediction being evaluated.

Evaluated-system outputs may be retained only in a comparison manifest and
revealed after model predictions are frozen. They cannot enter feature
construction, preprocessing, fitting, calibration, threshold selection, or
inference.

### Automation and abstention

The model may output `hold` for low confidence, evidence conflict,
out-of-distribution data, or incomplete evidence.

Report automated coverage separately from error among automated cases.
Promotion increases safe coverage; it does not eliminate holds by weakening the
gate.

Routine batches are machine-decided. Human review is reserved for held/drifted
cases, periodic audit, method/instrument change, and corpus maintenance.

### No silent online learning

Each run binds a fixed model, feature schema, threshold, and calibration
version.

Unlabeled batch-local QC may execute a frozen deployment calibration protocol.
Drift and holds can trigger a retraining proposal but cannot mutate the active
model.

A replacement model repeats grouped validation and uses a new sealed holdout.
The prior model remains replayable and available for rollback.

## ML Validation Estimands

The supporting research is recorded in
[`2026-07-24-cid-nl-ml-validation-research.md`](2026-07-24-cid-nl-ml-validation-research.md).

The two estimands use separate manifests and sealed evaluation strata. Each
manifest records:

- `estimand_id`;
- development- and evaluation-membership predicates;
- independent unit and group keys;
- allowed unlabeled adaptation protocol;
- claim boundary;
- holdout lifecycle.

### Operational generalization — first priority

Question: can a known hypothesis be judged in a new sample/batch without full
human review?

- The same hypothesis may occur in development and holdout.
- Source specimen, technical replicate, reinjection, acquisition batch, study,
  and instrument dependencies are grouped as applicable.
- A complete new batch/study slice tests the intended deployment claim.
- Its manifest is distinct from the unseen-family manifest.

### Unseen-family Discovery generalization — stronger second gate

Question: can the method handle a feature/identity family absent from
development?

- The same `PeakHypothesis` cannot cross development and holdout.
- Known isotope, adduct, in-source-fragment, compound, or profile family
  relationships remain in one group.
- Unknown grouping relationships are recorded as evidence limitations.
- Its manifest is distinct from the operational manifest.

The two estimands produce separate results. Operational success cannot be
reported as unseen-family Discovery success.

### Leakage prevention

- Learned preprocessing, feature selection, thresholds, hyperparameters, and
  probability calibration are fitted inside the training fold.
- Development uses grouped nested validation where selection is required.
- The sealed formal holdout evaluates a frozen pipeline once.
- A holdout that influences a later design change becomes
  `development_exposed`; it remains a regression fixture but loses independent
  confirmation authority.

### Deployment-matched unlabeled QC

New-batch pooled QC, internal standards, injection order, RT drift, and
instrument metadata may be used only when the same frozen protocol is available
in production.

Report separately:

- zero-shot performance with no new-batch adaptation;
- deployment-adapted performance using the declared unlabeled QC protocol.

## Metric Bundle

No single accuracy score is sufficient.

Report:

- identity false positives and supported-feature recovery;
- apex, boundary, and deterministic-area agreement;
- policy confusion among `select_value`, `leave_missing`, and `hold`;
- automated coverage and error among automated cases;
- paired Targeted dominance, Untargeted non-inferiority, and divergence reasons;
- same-condition, new-batch, and unseen-family results;
- uncertainty by independent hypothesis/family and batch;
- reviewer repeatability and adjudication count;
- replay/reproducibility status.

Performance metrics include elapsed time, RAW opens, scan calls, points, memory,
artifact I/O, cache rebuild/replay, and review-evidence generation/reopen cost.
Issue #128 owns the exact resource, cancellation, retry, cleanup, and cache
lifecycle contract.

Scientific correctness cannot be traded for speed. Scientific pass with failed
performance produces NARROW/optimization work, not a relaxed oracle.

## Provenance And Replay

Each case and review event bind to the canonical #123 processing-lineage
identities:

- `AcquisitionSource`;
- `ProcessingRequest`;
- `ProcessingAttempt`;
- `EvidenceArtifact`;
- kernel object reference;
- schema version, semantic role, and content digest;
- staleness and supersession relation;
- blinded assignment and sealed machine/legacy mapping;
- source universe and sampling stratum;
- append-only reviewer and adjudication events.

`case_id` is an oracle locator over these bindings, not a substitute for them.
The same retained inputs must rebuild the same review evidence. Missing source
bytes are recorded as `not_replayable`; historical provenance is not invented.

Issue #123 / `docs/product/processing-lineage.md` owns scientific request,
attempt, artifact, review binding, integrity, staleness, and supersession.
Issue #128 owns execution outcome, cancellation, retry scheduling, cleanup,
volatile cache lifecycle, and runtime observability. A versioned handoff links
the two without redefining either state model.

The oracle-specific adapter shape is
[`cid_nl_oracle_lineage_binding_schema.v1.json`](../schemas/cid_nl_oracle_lineage_binding_schema.v1.json).
It does not mint replacement identities. It binds proposal rows, holdout cases,
review events, comparisons, and gate results to #123 identities and records
unavailable historical bytes as `not_replayable` rather than manufacturing
provenance.

## Remaining Calibration Decisions

Calibration must still preregister:

- case counts and staged expansion limits;
- hidden duplicate fraction;
- exact identity-family grouping implementation;
- independent-unit/precision design;
- `nl116_istd_recovery_floor`;
- `proposal_precision_floor`;
- `reference_recovery_floor`;
- `reviewer_repeatability_floor`;
- `unassessable_rate_ceiling`;
- `automated_selective_risk_ceiling`;
- performance budget inherited from #128.

The Targeted/Untargeted 510-cell gap, machine automated coverage, and
boundary/area rules are already frozen. Folds/repeats remain conditional ML
annex decisions rather than deterministic-tracer blockers. The remaining
parameters require observed candidate prevalence, independent group counts,
reviewer calibration, intended claim precision, or the #128 budget. Their
formula, uncertainty role, freeze stage, and fail-closed handling are already
defined.

## Acceptance Packet Deliverables

Before tracer implementation or promotion planning, #125 must produce:

1. case-universe, sampling-design, tested-tracer proposal, independent
   reference, and narrow-scope manifest schemas plus sealing protocols;
2. a blinded review schema and append-only adjudication schema;
3. a calibration packet with proposed frozen tolerances;
4. a formal challenge/random holdout manifest schema and preregistration
   lifecycle; populated rows follow a real sealed tracer attempt and
   independently constructed reference frame;
5. a synthetic contract suite specification;
6. a machine-readable metric/gate/verdict preregistration;
7. an authority statement retaining `diagnostic_only`;
8. explicit #123 lineage bindings and #128 runtime handoff requirements.

For an ML automation or generalization claim, the conditional annex additionally
requires two separate ML split/input/holdout-lifecycle manifests: operational
and unseen-family.

No formal RAW replay is authorized by this document. A later replay requires
the XIC RAW validation and architecture preflights plus an explicit bounded run
scope.

## No-RAW Completion State

Issue #125's acceptance-contract design is complete without pretending the
formal evaluation has run:

- proposal, holdout sampling, reference-universe, review/adjudication, and
  lineage schemas are defined;
- reference construction, review custody, correction, and sealing protocols
  are defined;
- the gate inventory and verdict function are total and fail-closed;
- the 25-case synthetic contract suite exercises the required state, policy,
  boundary, lineage, and denominator mechanics;
- the 2RAW calibration remains the only RAW-backed evidence and is not rerun.

Formal holdout execution remains blocked until a real tracer attempt, populated
and sealed proposal/reference/sampling/review/lineage manifests, preregistered
remaining parameter values, retained evidence bundle, and #128 performance
budget exist. This completed contract remains `diagnostic_only` and grants no
product authority.

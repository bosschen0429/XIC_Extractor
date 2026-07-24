# CID-NL / NL116 Blinded Oracle And Tracer Acceptance Contract

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

### First blinded pass

The reviewer may see:

- sample and candidate coordinates;
- expected neutral loss/product coordinates and RT search range;
- full-window and local EIC views;
- relevant MS1/MS2 scans and acquisition metadata;
- neighbouring candidates and possible ownership conflicts;
- declared missing evidence.

The reviewer must not see:

- machine score or machine call;
- legacy or Targeted final call;
- counted-detection or selected-area result;
- machine-selected apex or boundary.

The reviewer independently records feature identity and sample-local
peak/boundary judgment.

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

If post-reveal inspection shows that a packet was corrupt, misbound, or missing
required evidence, the case is invalidated under a preregistered rule rather
than relabelled. The invalidation event records the reason, reviewer, time, and
affected gate.

Any correction needed before reveal remains append-only. Original verdicts are
never overwritten.

### Reviewer repeatability

The formal packet includes a small number of hidden duplicate cases separated
in presentation order. Only inconsistent judgments require adjudication.

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

Two denominators remain separate:

- proposal-conditioned precision / false-positive behavior over tracer
  proposals;
- reference-universe recovery over independently supported positives,
  including positives the tracer never proposed.

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

## Evaluation Units

Results are reported at two levels:

- `PeakHypothesis` / identity-family level;
- `PeakHypothesis × sample × policy` level.

Sample-cells from the same hypothesis are not treated as independent compounds.
Uncertainty estimates and split manifests report the independent hypotheses,
families, batches, studies, and instruments represented.

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

- false feature identity;
- Targeted-only authority leaking into Untargeted;
- `hold`, `unassessable`, or insufficient evidence becoming a selected value;
- overwriting a trusted existing value;
- collapsing missing, failure, ambiguity, and detected states;
- tracer lineage/provenance mismatch under an otherwise valid oracle packet.

Peak, boundary, area, recovery, repeatability, reproducibility, and performance
use preregistered tolerances.

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
- machine/legacy final call;
- counted detection or final selected area;
- post-review corrections;
- any field created only after the prediction being evaluated.

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

## Remaining Calibration Decisions

Calibration must still preregister:

- case counts and staged expansion limits;
- hidden duplicate fraction;
- exact identity-family grouping implementation;
- folds/repeats for development validation;
- boundary and area tolerances;
- Targeted/Untargeted non-inferiority gap;
- acceptable `unassessable` and automated-coverage ranges;
- uncertainty/confidence reporting method;
- performance budget inherited from #128.

These choices require observed candidate prevalence, independent group counts,
reviewer calibration, and intended deployment precision.

## Acceptance Packet Deliverables

Before tracer implementation or promotion planning, #125 must produce:

1. a case-universe manifest and sampling strata;
2. a blinded review schema and append-only adjudication schema;
3. a calibration packet with proposed frozen tolerances;
4. a formal challenge/random holdout manifest;
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

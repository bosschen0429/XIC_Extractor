# Shared Processing Kernel

This page owns the workflow-neutral scientific contract shared by Targeted and
Untargeted processing. The kernel is a collaboration of the existing
`peak_detection`, `alignment`, result, and projection owners. The kernel is
not a reason to create a generic `core/` package.

CID-NL is the first production tracer through this contract, not the product
boundary. Additional Discovery providers may consume different source evidence
and use different candidate-generation strategies without creating another
Alignment, ObservationRevisit, Backfill, cell-decision, or writer lifecycle.

## Current Contract

Targeted and Untargeted use the same scientific-owner interfaces and differ at
their application boundaries:

- Discovery providers own method-specific input interpretation, candidate
  generation, and initial evidence formation.
- Shared downstream owners consume typed observations, provenance,
  capabilities, and explicit provider strategies.
- Targeted owns target-list/search constraints, fixed-row projection, and its
  output contracts.
- Untargeted owns general search configuration, group-row projection, and its
  Matrix/review contracts.
- Writers serialize completed owner results. They do not rescan RAW data,
  regroup observations, or recompute selected values.

Shared means that lifecycle, authority, terminal states, and public contracts
do not fork by Discovery provider. Shared does not mean method-blind.
Scientifically necessary interpretation or integration differences can be
selected through typed provenance, declared capabilities, or a
provider-supplied strategy.

## Product Model

XIC has one shared scientific processing lifecycle:

`SearchScope -> provider-specific Discovery/capture -> shared observation/evidence contract -> PeakHypothesis -> CrossSample grouping -> CrossSampleGroupingResult -> optional bounded Backfill -> FinalizedScientificAlignment -> projection binding/subjects -> ProductionCellDecision -> projection completion -> result finalizer`

A configured Target constrains search and fixes a projection row. A
configured Target does not prove a sample-local observation, group
membership, or selected value.
Untargeted derives group rows from finalized CrossSample groups. The two
applications keep separate HTML flow, row policy, output schemas, and writer
authority over the same finalized scientific-owner results.

## Deep Interfaces

Each interface is deliberately narrow. Implementations may split internally,
but callers cannot bypass the semantic owner.

| Interface | Input -> output | Authority and deliberate loss |
| --- | --- | --- |
| Search scope | Target-list or Discovery/provider owner -> closed provider/capture request | Constrains what may be searched; does not claim what was observed |
| Discovery provider | exact SearchScope + canonical sample + provider inputs -> owner-backed observations and `PeakHypothesis` values | Owns provider-specific evidence interpretation behind the shared contract; never rows, groups, values, or artifacts |
| CrossSample | owner-backed hypotheses/observations -> canonical groups, dispositions, membership, lineage | Sole group/membership authority; does not integrate traces or select values |
| Backfill | one missing cell plus established Target/group context -> bounded revisit result | May produce the first usable observation for that cell; cannot originate the Target/group, mint membership, or select a value |
| Scientific alignment closure | closed request/group/membership/refinement and completed cell evidence -> `FinalizedScientificAlignment` | Validates shared scientific closure; does not construct workflow rows or render outputs |
| Projection binding/subjects | finalized core + typed Target/Untarget scope -> row identity/binding + explicit cell subjects | Constructs subjects only; no value, count, or inclusion decision |
| ProductionCellDecision | explicit subject + closed observations/receipts -> typed write or no-write | Sole selected-value owner; cannot create row or group identity |
| Projection completion | same projection owner + cell decisions -> inclusion, counts/reasons, review/archive routing | Does not recompute evidence or selected values |
| Result finalizer | discriminated request + shared core + completed projection -> immutable workflow result | Validates closure only; no RAW, regrouping, revisit, selection, or rendering |

`TargetCellSubject` binds one stable `TargetKey`, exact `TargetDefinition`,
canonical sample, `TargetRowBinding`, and optional finalized group.
`GroupCellSubject` binds one finalized canonical group and canonical sample.
Membership and value selection remain independent: member/value, member/null,
nonmember/Target-local-value, and nonmember/null are all valid outcomes.

The Target post-cell projection and Untarget writer row summary use
`accepted_cell_count` for the numeric cell population. Detection counts describe
their explicit evidence policy and are separate from numeric availability.
Public writers preserve the cell owner's value/no-write answer.
Missing NL, a false detection-count flag, or a review reason cannot independently
replace an accepted value with ND.

An identity-bearing CrossSample member combines canonical sample identity with
an owner-backed `PeakHypothesis` identity. The member kind tag is part of
identity. Canonical serialization and
digest mechanics come from `xic_extractor.processing_identity`; CrossSample and
the Untargeted projection still own their domain payloads and minting decisions.

## Evidence And State Invariants

- A `TraceGroup` contains traces from one sample and records the query context.
  Workflow constraints may annotate the request but cannot become observed
  evidence.
- A `PeakHypothesis` references one trace context. Its integration bounds stay
  within that context, and its evidence/audit cannot contain counted-detection
  or Matrix-write decisions.
- Hypothesis comparison uses the shared selection owner. Adapters may supply
  explicit constraints or strategies, but their inputs and effects remain in
  the audit; adapters do not independently rerun selection afterward.
- CrossSample owns one edge-eligibility decision for initial, claim-derived,
  and repair-derived candidates. Only eligible edges may connect a refinement
  neighborhood; review-only or excluded evidence cannot silently become
  membership.
- `FinalizedScientificAlignment` closes the request, sample universe, groups,
  dispositions, membership/lineage, and terminal observation/repair state
  before either workflow projection consumes it.

Technical availability, provider result, identity/model result, human review,
and product authority are orthogonal axes. A provider may define results such
as SUPPORT, HOLD, or REJECT, but shared owners consume its typed result and
provenance rather than assuming every provider uses CID-NL states. Failure,
ambiguity, or missing positive evidence must not silently serialize as chemical
absence, zero, selected value, or writer authority.

The closed core no-write reasons are `not_searched`,
`searched_no_observation`, `unresolved`, `rejected_observation`,
`not_quantifiable`, and `unavailable`. Human-facing text is rendered from these
typed facts and is never parsed back into scientific logic.

## Lifecycle Operations

### Discovery

Discovery is provider-specific. CID-NL, HCD-PI, Delta Mass, and future
providers may consume different evidence and run different candidate
generation or initial interpretation. Each provider publishes owner-backed
observations/hypotheses through the shared evidence contract. Targeted and
Untargeted can give a provider different SearchScope values without creating
separate downstream owners.

### Alignment

Alignment establishes or evaluates cross-sample correspondence from the
observations and hypotheses it receives. CrossSample alone owns initial
grouping, membership, canonical group identity, disposition, and split/merge
lineage. A configured Target identity may constrain search or binding, but a
fixed Target row is not group identity.

CrossSample alone may change membership and mint a successor group. Direct
local evidence wins over a trajectory prior; no universal monotonic trend is
assumed.

### ObservationRevisit

ObservationRevisit describes the bounded local revisit performed by Backfill,
not a separate receipt interface. Final cell composition consumes completed
cell evidence and the CrossSample coverage handoff. The Backfill owner below
defines what may be revisited; CrossSample and ProductionCellDecision retain
membership and selected-value authority respectively.

### Backfill

Backfill handles a missing cell under an already owner-backed Target or
existing-group context. The two workflows define a missing cell differently:

- Untargeted CID-NL: every activation-eligible sample without direct SUPPORT
  for a non-rejected group that passes the group support gate. A sample with a
  HOLD or REJECT assessment for that group is also a missing cell.
- Targeted diagnostic: a `no_candidate` cell whose Target binds exactly one
  group with owners. `ambiguous` and `failed` cells are not searched.

Backfill can perform one bounded, context-directed RAW/evidence revisit.
Backfill can produce the first usable observation for that cell. Backfill
cannot bootstrap a Target/group that has no prior owner-backed context.

An accepted Backfill result enters the cell-observation contract as cell
evidence. The existing `CrossSampleGroupingResult` keeps membership fixed;
`ProductionCellDecision` decides value/no-write. Backfill does not rebuild
the grouping result or trigger another grouping pass.

### Cell Decision And Projection

`ProductionCellDecision` consumes finalized evidence for one explicit Target or
Group subject and is the sole selected-value owner. A Backfill observation is
not automatically group membership or a writable value.

The decision retains its selected `IntegrationResult` for downstream peak
coordinates and shape fields, including when that observation is not
quantifiable. A no-observation or unresolved decision carries no selected
integration. Matrix projection consumes this answer without selecting again;
the existing evidence and value/no-write basis continue to define decision
identity.

Projection remains workflow-specific. Targeted retains one row per `TargetKey`
and applies its fixed-row inclusion/count/review policy. Untargeted derives one
request-scoped group-row key per finalized group and applies its Matrix/review
policy. One projection is never the other projection's oracle.

## Policy Boundary

| Concern | Shared scientific owner | Targeted application | Untargeted application |
| --- | --- | --- | --- |
| Discovery inputs | Provider contract and typed evidence | Target/mz/RT/role/pairing constraints | Configured provider evidence space |
| Physical observations and hypotheses | `peak_detection` owners | Supplies constrained requests | Supplies general requests |
| Cross-sample identity | CrossSample group/membership/disposition/lineage | Target identity constrains search/binding only | Group identity is inferred from evidence |
| Missing-cell Backfill | Backfill owns the bounded revisit | Supplies Target Backfill context | Supplies Group Backfill context |
| Selected value | `ProductionCellDecision` | Uses `TargetCellSubject` | Uses `GroupCellSubject` |
| Row/count/output policy | Does not own | Targeted projection/writer | Untargeted projection/writer |

## Module Boundaries

- `discovery` owns evidence-provider candidate generation and adapters into the
  shared observation/evidence contract.
- `peak_detection` owns sample-local traces, observations, hypotheses,
  integration, evidence composition, and selection.
- Alignment CrossSample, ObservationRevisit, Backfill, cell-decision, and
  result modules remain distinct semantic owners.
- `extraction` owns Targeted orchestration/policy and the retained execution
  adapter used by the finalized pure-CID-NL route; it does not own Alignment
  decisions or output rendering.
- Targeted and Untargeted projections and writers remain separate over the same
  finalized scientific result.
- Compatibility facades remain only for a current public or production
  consumer. Tests and historical artifacts are not consumer evidence.
- Do not add a generic `utils/`, an all-purpose `core/`, or storage-first model
  to reconnect these owners.

## Verification And Failure Boundaries

Changes to this contract must prove that the affected current entry reaches the
named owner, failures remain fail-closed, and the workflow-specific output
oracle is unchanged unless an expected difference is separately approved.

- A new Discovery provider enters through the shared evidence contract; it
  does not introduce a provider-specific Alignment/Repair/Backfill/Writer stack.
- Provider-specific integration or interpretation belongs behind typed
  provenance, capability, or strategy seams and remains auditable.
- Backfill requests without an owner-backed Target/group context are rejected.
- Backfill evidence cannot implicitly create membership or a selected value.
- Writers cannot rescan RAW data or recompute scientific decisions.
- Current public commands, schemas, and persisted receipts remain stable unless
  their current consumer and observable change are explicitly approved.

## Related Contracts

- [`discovery.md`](discovery.md) owns Discovery provider behavior.
- [`alignment.md`](alignment.md) owns CrossSample, repair, and
  finalized-alignment behavior.
- [`backfill.md`](backfill.md) owns missing-cell Backfill details.
- [`decision-policy.md`](decision-policy.md) owns selected-value resolution.
- [`processing-lineage.md`](processing-lineage.md) owns processing and delivery
  identity.
- [`execution-runtime.md`](execution-runtime.md) owns scheduling, cancellation,
  cleanup, recovery, and observability.
- [`../architecture-contract.md`](../architecture-contract.md) owns dependency
  direction and package responsibilities.

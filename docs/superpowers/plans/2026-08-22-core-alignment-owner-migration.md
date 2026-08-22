# Core Alignment Owner Migration — Current Packet

Packet ID: `xic-core-alignment-owner-migration-r1`

Revision: `1`

Base SHA: `f459f11ce1ba68a106095160ba453fc288288914`

Status: `frozen / local issue draft / implementation not started`

Canonical packet: this file

Supersedes: none

This is the local execution index for the next architecture phase. It may be
copied into a GitHub Issue only after explicit authorization. The durable
architecture and vocabulary remain in the linked owner docs; PR bodies will own
their exact diffs, verification, rollback, and residual risk.

## Objective

Replace the current highly coupled Alignment/Targeted split with deep semantic
owners so Targeted and Untargeted share capture, CrossSample, ModeTrack,
ObservationRepair, and ProductionCellDecision implementations, while retaining
different typed search scopes and output projections.

User value: one scientific meaning per decision, less duplicate behavior, a
smaller review surface, traceable null/value outcomes, and a codebase where old
migration assets are actually removed after consumers move.

## Current reality and loss

| State | Evidence | Observable loss |
| --- | --- | --- |
| Targeted and Untargeted are only partially converged | [`shared-processing-kernel.md`](../../product/shared-processing-kernel.md) and current `extraction/` versus `alignment/` routes | Equivalent evidence can gain different semantics by entry point |
| Alignment transports a mutable owner Matrix through several authorities | `xic_extractor/alignment/pipeline.py`, `matrix_identity.py`, `production_decisions.py`, and writers | Grouping, repair, value, inclusion, and rendering changes have a wide blast radius |
| Cross-sample identity is commonly ordinal/display-shaped | Current `FAM...` construction and [`processing-lineage.md`](../../product/processing-lineage.md) | Ordering/parallelism and product row identity are not cleanly separated |
| ModeTrack capability already exists but is a large diagnostic island | `xic_extractor/alignment/identity_coherence/` and `mode_track_*` modules | Replacing it would duplicate a useful model; leaving it isolated preserves two trajectory meanings |
| Backfill currently mixes repair, promotion, evidence, and Matrix concerns | Current Backfill/owner/transaction/acceptance files and [`backfill.md`](../../product/backfill.md) | A repaired observation can be confused with membership or writer authority |
| Machine evidence is often projected to large string tables | Current Backfill TSV and optional strings-only Parquet shadow | Unnecessary I/O/storage and weak native typing; humans rarely inspect the full population |

Operator observation: current Targeted behavior can remain null even when an NL
tag and corresponding MS1 peak were observed. That is a behavior hypothesis to
diagnose and promote later, not permission to loosen rules during structural
migration.

## Premise ledger

| Premise | State | Consequence |
| --- | --- | --- |
| Untargeted is the general lifecycle; Targeted is a constrained specialization | `contract` | One shared scientific implementation, separate search/projection variants |
| Broad candidate discovery is not Untargeted-only | `contract` | Targeted may inspect broad dR/R/MeR evidence while retaining only configured targets |
| ModeTrack should be reused, deepened, and upgraded | `contract` | `identity_coherence` capability migrates into `alignment/mode_track/`; no replacement algorithm module |
| CrossSample owns membership and group lineage | `contract` | ModeTrack proposes only; Backfill cannot imply membership |
| Backfill owns bounded local ObservationRepair | `contract` | ProductionCellDecision remains the sole selected-value owner |
| A singleton cannot establish cross-sample trend or fill other samples by itself | `contract` | Singleton groups are provisional; direct local evidence or independent repair is required |
| One universal monotonic RT drift describes all features | `rejected` | Sensitivity evidence includes multi-direction ISTDs and high-detection unknown features |
| Structural cleanup should also tune science behavior | `rejected` | Every behavior delta moves to a separately promoted lane |

## Frozen boundaries

In scope:

- establish request-stable group/member/disposition/lineage contracts;
- deepen existing ModeTrack behind an immutable proposal-only interface;
- isolate ObservationRepair from membership and value authority;
- create one ProductionCellDecision implementation with typed Target/Group
  subjects and typed no-write reasons;
- create immutable finalized core/result variants and Target/Untarget row
  projections;
- reduce the pipeline to a typed composition root;
- separate evidence archive, analytical handoff, delivery, and explanation;
- replace loose output flags with an owner-derived `ArtifactPlan`; and
- delete superseded code, shims, tests, schemas, and diagnostics after the named
  consumer census reaches zero.

Out of scope for structural PRs:

- changing selected peak/area, thresholds, RT models, Backfill eligibility,
  counted detection, row inclusion, public schemas, default artifacts, or GUI;
- activating Target local fill, ModeTrack reassignment, Untarget row-key public
  cutover, QC correction, Parquet v2, DNP/MA handoff, or new writer authority;
- inferring cross-request Untarget row continuity; and
- running 85RAW or other expensive RAW validation without the RAW gate.

## Owner and asset disposition

| Current asset | Disposition | Successor / exit oracle |
| --- | --- | --- |
| `cross_sample_peak_groups.py`, owner grouping/consolidation facts | `reuse_after_cleanup` | `alignment/cross_sample/`; identity/disposition/lineage characterization and consumer migration |
| `identity_coherence/`, ModeTrack producers/finalization | `reuse_after_cleanup` | `alignment/mode_track/`; proposal-only interface, no Matrix/Backfill/writer imports |
| Owner Backfill request/reuse/transaction/evidence facts | `reuse_after_cleanup` | `alignment/backfill/`; one bounded ObservationRepair epoch and truth-table closure |
| `production_decisions.py`, `machine_decision.py`, value-bearing `AlignedCell` fields | `replace_or_retire` | `alignment/cell_decision/`; parity oracle then field/implementation deletion |
| `matrix_identity.py`, `provisional_matrix.py`, `owner_matrix.py` mixed pre-final Matrix roles | `replace_or_retire` | distinct CrossSample/observation snapshots plus `alignment/result/`; final Matrix only after closure |
| `pipeline.py` domain branches and loose metadata transport | `replace_or_retire` | `alignment/workflow.py`; typed composition only |
| Targeted-specific alignment/rescue paths | `reuse_after_cleanup` then `compatibility_only` | Target projection adapters over shared owners; remove fallback algorithm after CLI/GUI/direct consumers migrate |
| `output_levels.py` and caller-authored emit booleans | `replace_or_retire` | delivery-owned `ArtifactPlan`; parity plus exact artifact-set oracle |
| Existing Backfill TSV and string-Parquet adapters | `compatibility_only` | finite reader/adapter census; typed Parquet v2 is a separate promotion |
| Alignment TSV/XLSX/HTML writers | `reuse_after_cleanup` | delivery renderers consuming finalized decisions only |
| `shared_peak_identity_explanation/` and heavy debug assets | `unknown` per named consumer | keep only queried explanation primitives; externalize/retire ownerless diagnostics |

Tests protect product invariants and public compatibility, not old module
shapes. A test moves to the successor interface or is deleted with its retired
implementation unless it still proves a named compatibility or rejection path.

## Known compatibility consumers

Initial production census at the packet base:

- `scripts/run_alignment.py` calls `alignment.pipeline.run_alignment`;
- `xic_extractor/extraction/untargeted_provisional_matrix_workflow.py` calls the
  same entry;
- current Targeted CLI, GUI, and `xic_extractor.extractor.run` retain their
  public output behavior;
- `alignment_matrix.tsv`, `alignment_review.tsv`, identity/evidence sidecars,
  Targeted CSV/workbook, Alignment XLSX/HTML, config keys, filenames, order, and
  reason/count/value semantics are public contracts; and
- tests and historical artifacts are oracles, not consumer evidence.

Each migration PR must refresh the executable caller/reader census from its
base SHA. A compatibility adapter is legal only when it names a still-live item
from that census and has an explicit removal condition.

## Dependency-ordered PR stack

| Lane | Structural outcome | Merge gate | Exit/cleanup |
| --- | --- | --- | --- |
| 1. CrossSample | Canonical member identity, group digest, disposition, split/merge lineage | Characterization parity; order/parallel/retry/ambiguity counterexamples | Remove ordinal identity use from internal decisions; retain display adapter only |
| 2. ModeTrack | Absorb existing capability behind immutable snapshot/proposal interface | Model-set parity, proposal determinism, nontraining/unassessable/crossing cases | Delete duplicate/obsolete trajectory paths and ownerless diagnostic entry points |
| 3. Backfill | One bounded ObservationRepair epoch and transaction | Repair/membership/value truth table; exact receipt; no loop | Remove legacy repair paths and Backfill-owned writer decisions |
| 4. Cell decision | One deep Target/Group ProductionCellDecision | Current value/state/count/inclusion parity; no-write taxonomy; cross-projection rejection | Remove fallback area/reason logic and value-bearing legacy aliases |
| 5. Final results/projections | Immutable core plus Targeted/Untargeted result variants and row identities | Closure/failure/immutability/order tests; current schema parity | Remove mutable Matrix transport and optional-mode structures |
| 6. Workflow/archive/delivery | Typed composition root, compact archive, ArtifactPlan, atomic bundle | Artifact-set parity, render-only writers, failure atomicity, consumer roundtrip | Delete emit flags, loose ledgers, stale writers/adapters with zero consumers |
| 7. Behavior promotions | Separately chosen Target fill, ModeTrack sensitivity, Untarget key/schema, Parquet v2, or handoff activation | Expected diff, authority binding, method-appropriate real evidence/manual review, rollback | Promote or kill each lane independently |
| 8. Final purge | No superseded owner, shim, migration schema, test, doc, or diagnostic remains | Fresh referrer/caller census, clean-checkout checks, public contract review | Delete the migration packet after durable claims and closeout are absorbed |

One PR closes one semantic owner lane. Do not split a coherent owner into
model/serializer/test/docs PRs, and do not combine the entire stack into one
mega-PR.

## Validation and promotion rules

- Structural lanes use characterization parity plus maintainability gain. A
  changed scientific value relabels the work as behavior promotion.
- New or changed checks must be proven capable of failing by breaking the exact
  guarded invariant once.
- Evidence status uses the canonical Evidence labels. Tests alone never prove
  production readiness.
- SDO/LEK uses HCD evidence; MixSTDs may use CID/HCD; each Target method uses its
  declared evidence provider. ModeTrack sensitivity includes multi-direction
  ISTDs and several high-detection unknown features.
- Any 85RAW or other RAW-backed acceptance follows `xic-raw-validation` first.
- DNP/MA analytical handoff activates only after a real consumer roundtrip over
  typed samples/features/cells/lineage.
- Documentation-only closeout runs link/format checks, `git diff --check`, and
  secret/local-path scan; it grants no runtime or product authority.

## Stop and rollback

Stop and refreeze when a real Targeted/Untargeted caller proves incompatible
scientific semantics at a proposed shared seam; a structural lane produces an
unapproved product diff; a public consumer cannot be enumerated; or owner
closure requires caller-authored authority. Do not add a flag or fallback to
hide the conflict.

Every lane is rollbackable by reverting its coherent commit/PR while the prior
public path remains authoritative. A compatibility facade never contains a
second fallback algorithm, so rollback selects the prior owner rather than
silently mixing both.

## Done when

The migration is complete only when both workflows run through the frozen deep
interfaces, current public behavior is preserved or separately promoted, the
final result/delivery/archive contracts close, all named consumers have moved,
and superseded code, adapters, flags, tests, schemas, diagnostics, and this
temporary execution packet are removed or absorbed into their durable owners.

Durable owners:

- [`architecture-contract.md`](../../architecture-contract.md)
- [`architecture-public-contracts.md`](../../agent/architecture-public-contracts.md)
- [`domain-glossary.md`](../../product/domain-glossary.md)
- [`shared-processing-kernel.md`](../../product/shared-processing-kernel.md)
- [`alignment.md`](../../product/alignment.md)
- [`backfill.md`](../../product/backfill.md)
- [`processing-lineage.md`](../../product/processing-lineage.md)
- [`product-validation-contract.md`](../../agent/product-validation-contract.md)

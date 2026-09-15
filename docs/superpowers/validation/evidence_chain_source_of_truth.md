# XIC Evidence Chain Source Of Truth

Last updated: 2026-06-21

Validation status: `diagnostic_only` for this document. This file maps evidence
vocabulary, evidence owners, promotion boundaries, and authority boundaries. It does
not grant `ProductWriter` authority, change the default matrix, or replace an
expected-diff contract.

## Reading Model: Evidence -> Gate -> Writer

Every claim in this system moves through three layers, and most misreads come
from collapsing them into one.

- **Evidence**: an observation exists (an MS1 peak, an MS2/neutral-loss tag, a
  trace, an RT match). Evidence only says "there is something to look at," and on
  its own is never permission to write.
- **Gate**: a decision turns evidence into an outcome (accept, review, hold, or
  block). A gate can stop a claim even when the underlying evidence is real, for
  example when peak ownership is ambiguous.
- **Writer (`ProductWriter`)**: only a registered writer scope, backed by an
  expected diff and cell provenance, may change the public matrix/workbook/CSV.

Each provider section below ends with an "Authority state" line naming the
highest layer that provider can reach on its own. Seeing signal is layer one;
writing the public matrix is layer three; the gates in between are not optional.

Terminology note for LC/MS-MS readers:

- "Public matrix" means the quantitative output that downstream users will
  consume, including workbook and CSV exports.
- `ProductWriter` is the software boundary that writes that public output. It
  is not a new type of mass-spectrometry evidence.
- A "gate" is a QC/acceptance decision layer: it decides whether real evidence
  is accepted, sent to review, held, or blocked.
- `expected-diff` is the predeclared list of output cells allowed to change.
- A "sidecar" is an auxiliary evidence/provenance file. It can prove where a
  decision came from, but it is not a raw MS1/MS2 signal.
- NL means neutral loss; in Chinese prose this guide uses "neutral loss" or
  "中性損失" for the same LC-MS/MS concept.

## Why This File Exists

The codebase already has several maintained sources of truth, but they answer
different questions:

| Source | Owns | Does not own |
| --- | --- | --- |
| `docs/product/domain-glossary.md` | Stable domain vocabulary, lane boundaries, and the shared evidence spine. | Current counts, active tier, writer scopes. |
| `docs/architecture-contract.md` | Dependency direction and the rule that evidence providers feed the spine before any matrix/export contract. | Current validation packet status. |
| `docs/lc-msms-evidence-rules.md` | Domain interpretation of MS1/MS2/RT/Backfill evidence and promotion constraints. | Current product lane state. |
| `docs/superpowers/plans/2026-06-15-productization-control-plane.md` | Compatibility lookup for retired control-plane lane keys and historical anchors. | Current tier, lane, writer scope, or promotion state. |
| `docs/superpowers/validation/productization_status_index_v1.tsv` | Machine-checkable current lane status and public-surface flags. | Domain meaning of each evidence field. |
| `docs/superpowers/schemas/productization_authority_manifest.v1.json` | Allowed and forbidden writer scopes. Unknown scopes fail closed. | Scientific sufficiency of evidence providers. |
| This file | Human-maintained evidence-chain map across code, diagnostics, and promotion boundaries. | Machine enforcement, counts, tier changes, or writer authority. |

Rule: if a durable evidence provider, evidence state, gate, or authority
boundary changes, update this file in the same change set. If a tier, active
lane, writer scope, matrix schema, selected area, counted detection, workbook,
or default output changes, also update the applicable status index, authority
manifest, control schema, and promotion contract.

## Shared Evidence Spine

All product lanes should eventually express evidence through this spine. Internal
datasets such as 8RAW and 85RAW are validation fixtures, not architecture
boundaries.

| Spine term | Primary owner | Meaning | Authority boundary |
| --- | --- | --- | --- |
| `Trace` | `xic_extractor/peak_detection/traces.py` | One XIC signal with RT/m/z window, sample, and source context. | Signal evidence only. |
| `TraceGroup` | `xic_extractor/peak_detection/traces.py` | Related traces compared for a target, family, or hypothesis. | Signal evidence only. |
| `IntegrationResult` | `xic_extractor/peak_detection/hypotheses.py` | Selected apex, boundaries, area, baseline, and morphology-area context. | May provide a value source, but not write authority by itself. |
| `EvidenceVector` | `xic_extractor/peak_detection/hypotheses.py` | Typed support, concerns, caps, facts, quality flags, MS1/MS2/RT/CWT/boundary context. | Feeds selection/model decisions. |
| `AuditTrail` | `xic_extractor/peak_detection/hypotheses.py` | How a hypothesis was selected, rejected, merged, or blocked. | Provenance only. |
| `PeakHypothesis` | `xic_extractor/peak_detection/hypotheses.py` | Candidate identity plus integration, evidence vector, and audit trail. | Needs an activation/export contract before writing public output. |
| `ProductWriter` | Productization scripts, quant matrix versioning, workbook/CSV writers | Writes public matrix/workbook/CSV state. | Requires explicit writer scope, expected diff, tests, and manifest/status-index coverage. |

## Evidence Providers And Evidence States

### Common Evidence Semantics

Owner:

- `xic_extractor/evidence_semantics.py`
- `xic_extractor/peak_detection/evidence_facts.py`
- `tests/test_evidence_semantics.py`
- `tests/test_evidence_spine_consistency.py`

Purpose: convert targeted candidates, discovery candidates, and aligned cells
into comparable decision semantics.

Core evidence:

- `CommonEvidence`: MS1 peak presence, area, height, boundary status, RT delta,
  trace quality, scan support, neutral-loss tag, observed/configured loss,
  loss ppm, seed event count, MS2 presence, MS2 NL match, MS2 trace strength,
  confidence, evidence score, review flag, and reason.
- `EvidenceDecisionSemantics`: decision class plus support, concern, conflict,
  not-counted, review, and exclusion labels.
- Canonical support labels include `ms1_peak`, `positive_area`,
  `ms2_present`, `nl_match`, `multi_seed`, and `scan_support`.
- Canonical concern labels include `missing_ms1_peak`, `non_positive_area`,
  `nl_fail`, `trace_quality_review`, and `backfill_provenance`.

Authority state: semantic evidence only. It can explain or classify a decision,
but it is not a writer scope.

### MS1 Trace, Morphology, Area, And Boundary Evidence

Owners:

- `xic_extractor/peak_detection/hypotheses.py`
- `xic_extractor/peak_detection/evidence_facts.py`
- `xic_extractor/peak_detection/selection_decision.py`
- `xic_extractor/alignment/cell_quality.py`
- `docs/lc-msms-evidence-rules.md`

Purpose: answer whether there is an assessable MS1 peak in the expected window,
whether the selected apex/boundary/area is complete, and whether the local trace
supports the same peak rather than neighboring noise.

Evidence states:

- `ms1_peak_present`, `positive_area`, `area_source`, `height`, selected apex,
  start/end RT, duration, boundary quality, baseline quality, prominence,
  scan support, edge quality, continuity, CWT/boundary context.
- `CellQualityStatus`: `detected_quantifiable`, `rescue_quantifiable`,
  `review_rescue`, `duplicate_loser`, `ambiguous_owner`, `blank`, `invalid`.
- MS1 morphology default uses `gaussian15_positive_asls_residual` as the active
  selected-area owner where relevant.

Authority state: MS1 signal can support a value and can block/review a cell,
but product writing still needs lane-specific authority. A local peak alone is
not enough to write a new matrix cell.

### CID-NL, MS2, HCD, And Product-Ion Evidence

Owners:

- `xic_extractor/neutral_loss.py`
- `xic_extractor/ms2_trace_evidence.py`
- `xic_extractor/discovery/*`
- `xic_extractor/instrument_qc/hcd_evidence.py`
- `xic_extractor/instrument_qc/calibration_product_evidence.py`
- `scripts/build_cid_nl_default_product_activation.py`
- `tools/diagnostics/cid_nl_feature_inclusion_gate.py`

Purpose: answer whether MS2/product-ion/neutral-loss evidence supports an
identity, discovery candidate, or calibration context.

Evidence states:

- `CandidateMS2Evidence`, `NLResult`, `MS2ProductEvidence`, `MS2TraceEvidence`.
- Discovery evidence includes seed events, product intensity, MS1 peak/area,
  scan support, RT alignment, family/superfamily context, score, and tier.
- HCD evidence includes product-ion match, trigger scan count, ppm/intensity
  gates, parse status, and status such as `hcd_supported`, `hcd_partial`,
  `no_product_match`, `no_ms2_trigger`, and `hcd_group_unmapped`.

Authority state: CID-NL/MS2/HCD evidence is identity or diagnostic evidence.
It must not directly become `ProductWriter` authority. Public output changes
must still pass a bounded activation contract and quant-matrix expected-diff
replay.

### RT, iRT, Drift, And Paired-ISTD Evidence

Owners:

- `xic_extractor/rt_prior_library.py`
- `xic_extractor/target_pair_rt_calibration.py`
- `xic_extractor/peak_detection/evidence_facts.py`
- `xic_extractor/alignment/shared_peak_identity_explanation/*`
- `docs/lc-msms-evidence-rules.md`

Purpose: answer whether a hypothesis is RT-compatible with a prior, paired
internal standard, drift model, or expected local neighborhood.

Evidence states:

- RT prior close/near/shifted, paired ISTD RT close, drift-corrected delta,
  drift prior source, injection-order context, RT mode status, and matrix RT
  drift policy status.

Authority state: RT is contextual evidence. RT mismatch can block or require
review, but RT agreement alone should not create matrix write authority.

### Targeted Product Projection And Review Actions

Owners:

- `xic_extractor/peak_detection/targeted_product_projection.py`
- `xic_extractor/extraction/result_assembly.py`
- `xic_extractor/output/schema.py`
- `xic_extractor/output/csv_writers.py`
- `xic_extractor/output/detection.py`
- `xic_extractor/review_actions.py`
- `docs/product/decision-policy.md`
- `tests/test_targeted_product_projection.py`
- `tests/test_review_actions.py`

Purpose: separate targeted product authority from legacy display fields.

Evidence states:

- `TargetedProductProjection`: `product_state`, `counted_detection`,
  `review_state`, `projection_reason`, support/review/conflict/not-counted
  reasons, exclusion reasons, legacy evidence, and `legacy_authority_status`.
- `ProductState`: `detected_clean`, `detected_flagged`, `not_counted`,
  `excluded`, `ambiguous`.
- Review actions: `reject_current`, `select_candidate`, and
  `set_manual_boundary` are product-mutating and require expected diff.
  `accept_current` is audit-only; unresolved/deferred states do not write.

Authority state: in targeted output, product state and counted detection are
projection authority. Legacy `Confidence`, `Reason`, `NL`, and score are
evidence or display fields, not counting authority.

### Discovery Evidence

Owners:

- `xic_extractor/discovery/models.py`
- `xic_extractor/discovery/evidence_config.py`
- `xic_extractor/discovery/evidence_score.py`
- `scripts/build_cid_nl_default_activation_successor_authority_contract.py`
- `scripts/build_cid_nl_default_product_activation.py`
- `tools/diagnostics/cid_nl_feature_inclusion_gate.py`

Purpose: discover non-targeted / untargeted candidate identities and decide whether a
successor row is better explained than a source/current row.

Evidence states:

- MS1 peak present/absent, area, scan support, trace quality, seed events,
  product intensity, strict neutral-loss match, configured/observed loss,
  RT aligned/near/shifted, family singleton/representative/member context,
  evidence score, tier, successor identity, source identity, and expected-diff
  contract status.

Authority state: Discovery produces candidate identities and activation
evidence. It is not itself the writer for public matrix output. Any public default
activation must be bounded by the status index, authority manifest, expected
diff, and provenance.

### Alignment, Owner, Edge, And Ambiguity Evidence

Owners:

- `xic_extractor/alignment/ownership_models.py`
- `xic_extractor/alignment/ownership.py`
- `xic_extractor/alignment/edge_scoring.py`
- `xic_extractor/alignment/matrix.py`
- `xic_extractor/alignment/matrix_identity.py`
- `xic_extractor/alignment/cell_quality.py`
- `xic_extractor/alignment/production_decisions.py`

Purpose: decide which sample-local MS1 owner or aligned identity a cell belongs
to, whether an edge can join owners across samples, and whether ambiguity or
duplicates should block writing.

Evidence states:

- `IdentityEvent`: candidate/sample/raw/tag/precursor/product/observed-loss
  seed evidence.
- `SampleLocalMS1Owner`: owner apex, boundaries, area, height, primary event,
  supporting events, conflict flag, assignment reason, region audit, selected
  integration.
- `OwnerEdgeEvidence`: edge decision, failure reason, RT deltas, drift source,
  injection gap, owner quality, seed support, duplicate context, and score.
- Hard blockers include same sample, NL tag mismatch, precursor/product/loss
  tolerance mismatch, non-detected owner, ambiguous owner, identity conflict,
  and backfill bridge.
- `MatrixIdentityRowDecision`: `production_family`,
  `provisional_discovery`, `audit_family`, with confidence high/medium/review/none.
- `ProductionStatus`: `detected`, `accepted_rescue`, `review_rescue`,
  `rejected_rescue`, `blank`.

Authority state: owner and identity evidence can permit, block, or review a
matrix cell, but a new writer scope still needs activation/expected diff. Owner
ambiguity is a blocker until resolved.

### Backfill Evidence Projection And Product-Authority Sidecars

Owners:

- `xic_extractor/alignment/promotion_policy.py`
- `xic_extractor/alignment/backfill_evidence_projection.py`
- `xic_extractor/alignment/backfill_ms1_product_authority.py`
- `xic_extractor/alignment/backfill_candidate_ms2_product_authority.py`
- `xic_extractor/alignment/shared_peak_identity_explanation/machine_evidence_support.py`

Purpose: decide whether a currently blank/rescued cell has enough evidence to
be accepted, reviewed, held, or written inside a bounded Backfill lane.

Evidence states:

- MS1 pattern: `backfill_ms1_pattern_status`,
  `backfill_ms1_pattern_evidence_level`, product-authority source/scope/hash,
  and own-max shape support.
- QC reference: `backfill_qc_reference_status`,
  `backfill_qc_reference_evidence_level`.
- RT drift: `backfill_matrix_rt_drift_status`,
  `backfill_drift_evidence_level`, drift compatibility, drift-corrected delta.
- Candidate MS2: `backfill_candidate_ms2_pattern_status`,
  `backfill_candidate_ms2_evidence_level`, strict NL, trigger, trace strength,
  DDA-missing-NL, required-tag status, and product-authority provenance.
- MS1 product authority requires allowlist status, source evidence, overlay
  JSON provenance/hash, same family/sample/vector, own-max metric, supportive
  quality vector, and threshold compliance.
- The retired 666-cell and 84-cell Backfill expansion experiments remain only as
  negative scope tombstones in the authority manifest. Their packets and
  checkers are available from Git history, not as current evidence-chain gates.
- Candidate MS2 product authority requires supportive/partial support,
  `sample_candidate_aligned` or `sample_boundary_aligned` level, source hash,
  schema version, source match, and similarity threshold compliance.

Authority state: these sidecars are acceptance/provenance gates. They are not
raw MS1/MS2 providers. A row without product-authorized scope/source remains
review or diagnostic evidence.

### Quant Matrix Expected-Diff And Provenance

Owners:

- `xic_extractor/alignment/quant_matrix_version.py`
- `xic_extractor/alignment/quant_matrix_promotion.py`
- `scripts/build_quant_matrix_default_product_activation.py`
- `scripts/build_cid_nl_default_product_activation.py`

Purpose: make matrix-writing changes explicit, replayable, and auditable.

Evidence states:

- Expected diff: `peak_hypothesis_id`, `sample_stem`, baseline value,
  activated value, expected matrix effect, expected reason.
- Cell provenance: source family ids, matrix value, cell status, value source,
  write authority, acceptance decision/basis, truth status, quant value,
  matrix area source, source artifact relpath/hash/row hash/manifest hash.
- Promotion readiness: separates contract correctness from scientific
  confidence and requires large-cohort/oracle/downstream evidence before broad
  scientific claims.

Authority state: this is the last gate before default matrix writing. It refuses
unused expected diffs and existing-value overwrites. Passing a focused expected
diff proves contract correctness for a bounded scope, not global scientific
confidence.

### Overlay And Human-Guide Evidence

Owners:

- `tools/diagnostics/family_ms1_overlay_*`
- `tools/diagnostics/cid_nl_default_activation_gallery_review.py`
- `tools/diagnostics/cid_nl_differential_overlay_review.py`
- `xic_inspector`
- `docs/superpowers/validation/evidence_chain_status_guide.html`

Purpose: produce scientific overlay evidence and make completed evidence
reviewable without silently changing product authority.

Evidence states:

- Trace recovery status, overlay trace JSON/PNG/HTML, recovered overlays, and
  compact reviewer conclusions with an enduring consumer.

Authority state: review artifacts may support an active acceptance gate, but
dataset-run queues, templates, labels, and rendered packets belong under
ignored `output/`. A retained compact verdict remains non-authoritative unless
a separate activation contract consumes it.

## Product-Lane Authority Boundaries

The authoritative machine-readable registry state is
`docs/superpowers/validation/productization_status_index_v1.tsv` plus
`docs/superpowers/schemas/productization_authority_manifest.v1.json`. This table
describes stable lane roles, not current counts. Counts, hashes, pass/fail
numbers, and active scope sizes belong in the machine-readable artifacts.
The exact Backfill writer membership remains in the retained production-
acceptance manifest and activation values; `scripts/check_productization_authority.py`
recomputes the manifest identity and binds every authorized cell across both.

| Lane / surface | Evidence chain | Authority boundary |
| --- | --- | --- |
| Backfill current | Backfill policy, same-peak support, sample-local evidence, value provenance, and expected diff. | Writer authority is limited to the registered scope in the authority manifest and status index. |
| CID-NL Discovery | Successor self evidence, CID-NL tag evidence, MS1/RT/family context, identity reconstruction, expected diff, and quant matrix replay. | CID-NL/MS2 evidence is not direct writer authority; it must be consumed by a bounded activation contract. |
| Broad Backfill candidate universe | Candidate, review, overlay, recovery, economics, and locality evidence. | No write authority unless a new activation/export contract and manifest scope are added. |
| Targeted review actions | Expected-diff gate over product state, counted detection, review state, candidate sidecar, or manual-boundary state. | Mutating actions require approved expected diff and fail closed on stale/missing baseline. |

## Known Gaps

1. There is still no machine-readable evidence-chain map that joins every
   active writer scope to its required evidence columns, artifacts, and
   authority outcome. This file is the human source of truth until that checker
   exists.
2. Similar vocabulary appears in multiple layers: `own_max`, `ambiguous`,
   `rescue`, `accepted_rescue`, `backfill`, and `authority` have layer-specific
   meanings. Reports must name the layer, not only the word.
3. The HTML evidence guide is a teaching artifact. It should be updated from
   this file, but it must not become the authority manifest or status source.
4. HCD, standards, calibration, Delta Mass, and product-ion vocabulary exist as
   providers or hooks, but not every provider is a complete first-class
   product lane.

## Required Maintenance Rules

When adding or changing evidence:

1. Update this file if the change adds a durable evidence provider, state,
   promotion boundary, or authority boundary.
2. Update `docs/product/domain-glossary.md` only when stable vocabulary, lane
   boundaries, or authority concepts change.
3. Update `docs/lc-msms-evidence-rules.md` when the scientific/domain
   interpretation changes.
4. Update `productization_status_index_v1.tsv`,
   `productization_authority_manifest.v1.json`, and the promotion contract only
   when tier, active lane, writer scope, public
   surface, default matrix, workbook, counted detection, selected peak, or
   selected area changes.
5. Add or update focused tests/checkers when a machine-readable contract is
   introduced.

## Integrity Status Vocabulary

If a machine-readable evidence-chain checker exists, it should use stable
row/cell identifiers and report these generic states:

- `wired`: required evidence exists, hashes/provenance match, and authority
  scope is registered.
- `not_wired`: required evidence is missing or cannot be joined.
- `not_applicable`: evidence provider does not apply to that lane.
- `conflict`: evidence exists but contradicts the authority claim.
- `held`: evidence is incomplete and the cell/row must not write.

When a lane needs ownership, own-max, ambiguity, or shift-aware evidence, the
checker should join those artifacts by stable row/cell keys. If the artifacts
are absent or unjoinable, the correct state is `not_wired` or `held`, not a
silent pass and not broad writer authority.

# CID-NL Independent Reference-Universe Protocol

Doc placement: repo_support_doc
Doc kind: spec
Doc lifecycle: active
Repo owner: docs/product/discovery.md
Doc exit rule: Absorb the durable denominator and independence rules into the
Discovery validation owner after Issue #125 closes.

Date: 2026-07-25
Issue: [#125](https://github.com/Chao-hu-Lab/XIC_Extractor/issues/125)
Validation status: `diagnostic_only`

## Decision

Recovery must be measured against a bounded reference universe that is built
without the tested tracer's proposals, scores, rankings, thresholds, or calls.
The tested tracer may later be compared with that sealed universe; it cannot
define who enters the denominator.

The reference universe has two separately reported positive sources:

1. expected-positive controls whose physical presence and method applicability
   are fixed by experimental design;
2. sample-local positives supported by an independent bounded enumeration and
   blinded review.

External libraries and frozen identity profiles may support identity
interpretation. They do not prove that a feature is present in a particular
sample.

## Frozen Scope

The first formal Untargeted claim is NL116-only. The bounded dataset manifest
must freeze all 85 `AcquisitionSource` identities, stable sample keys, method
configuration, eligible scan/m/z/RT space, and these material strata:

- `TumorBC2286_DNAandRNA` and `TumorBC2304_DNAandRNA` are
  `dna_and_rna_mixed_biological`;
- all other biological samples are `dna_only_biological`;
- pooled QC injections are `pooled_qc`, without inferring their composition
  from filenames.

The six fixed-spike NL116 internal standards form a complete 510-cell control
census (`85 × 6`). The fixed-spike NL132 internal standard remains recorded as
physically present but out of the NL116 method scope. A paired analyte never
inherits expected presence from its internal standard.

## Eligible Reference Sources

| Source | May establish sample-local positive? | Required boundary |
| --- | --- | --- |
| Fixed-spike in-scope internal standard | Yes, as an expected-positive control | Experimental design, source/config hash, method scope |
| Authentic standard in the evaluated sample | Yes | Sample provenance and method applicability |
| Frozen identity profile | No, not alone | Profile may support identity after local evidence exists |
| Attributable library MS2 | No, not alone | Library provenance, adduct/polarity/NL/material scope, local evidence |
| Independently generated benchmark | Yes, within its declared claim | Generator independent of tested-tracer decision logic |
| Blinded systematic review | Yes | Independent bounded frame, retained evidence, sealed review record |
| Matched negative | Negative/challenge only | Matching method frozen before machine reveal |

Legacy, Targeted, or earlier machine outputs are comparison surfaces after the
reference seal. They are not membership sources.

## Construction Sequence

1. **Freeze the bounded frame.** Seal dataset identities, method scope,
   material strata, evidence types, inclusion/exclusion rules, independent
   grouping keys, and source manifests.
2. **Populate the control census.** Emit one row for every method-applicable
   fixed-spike control cell. Record method-out-of-scope controls explicitly
   rather than silently dropping them.
3. **Enumerate non-control candidates independently.** Use a versioned bounded
   enumerator or a documented systematic human search protocol that does not
   consume tested-tracer or legacy decisions. Reuse of a low-level RAW adapter
   is allowed only when membership and decision logic remain independent and
   the shared dependency is declared. A dependency manifest records adapter,
   evidence-transform, candidate-generation, and decision-stage IDs, versions,
   config hashes, shared-stage status, and overlap disposition.
4. **Deduplicate before reveal.** Collapse source overlaps by the frozen
   `reference_family_id × AcquisitionSource × method_scope` key. Conflicts
   become review cases; they are not resolved by tracer ranking.
5. **Exhaust or probability-sample.** A complete census uses inclusion
   probability and analysis weight `1`. Any sampled frame records its sampling
   algorithm, seed commitment, stratum, inclusion probability, and weight.
   Challenge-only rows remain descriptive and do not estimate representative
   recovery.
6. **Review blind.** Create review presentations without source-role or
   machine/legacy mappings. Seal sample-local identity/observation labels,
   policy labels, valid-case decisions, and any pre-reveal adjudication.
7. **Seal the reference universe.** Hash the rows, source bytes, construction
   protocol, dependency manifest, frame completeness/size by stratum, review
   records, counts, exclusions, undercoverage disposition, and independence
   audit.
8. **Reveal and compare once.** Join the tested-tracer proposal manifest only
   after both seals exist. A post-reveal defect makes the affected gate
   `inconclusive`; rows and denominators are not selectively repaired.

## Labels And Denominators

Only these labels enter recovery:

- `expected_positive_control`;
- `independently_supported_positive`.

They must be in method scope and have valid sealed lineage. Report the two
source classes separately before any declared combined estimand.

`supported_negative` may support challenge analysis and proposal precision.
`unassessable`, `method_out_of_scope`, and `excluded` enter neither positive nor
negative scientific denominators. Their counts and reasons remain visible.

Proposal-conditioned precision is:

> correctly supported `select_value` proposals / all sealed `select_value`
> proposals.

Reference recovery is:

> independently supported positives recovered under the declared policy /
> all eligible independently supported positives.

These are different estimands. Proposal precision is not a dataset
false-positive rate, and recovery cannot be estimated from tracer proposals.

For probability samples, the preregistered weighted estimator and its
uncertainty method are used. Sample-cells from one hypothesis are not presented
as independent compounds; results report hypothesis/family, sample, batch,
study, and instrument grouping.

## Independence Audit

The seal fails unless all are true:

- tested-tracer outputs were not used for membership, ordering, adjudication,
  or case validity;
- legacy/Targeted outputs were not used for membership;
- library membership was not treated as sample presence;
- shared adapters or evidence transforms are declared;
- any shared candidate/decision logic is identified.

If the independent enumerator shares the tested tracer's candidate or decision
logic, the resulting set may be used for regression or proposal precision but
not as an independent recovery denominator. A genuinely independent frame or
expected-positive control census is then still required.

`frame_completeness` is one of `complete`,
`incomplete_known_undercoverage`, or `unknown`. Only a complete sealed frame
may use the `full_bounded_reference_recovery` label. Incomplete or unknown
frames must use `known_reference_recovery` or
`expected_control_recovery_only`, record the undercoverage disposition, and
cannot support a full-recovery GO claim.

## Fail-Closed Cases

- Missing source bytes, hashes, stable source identity, or review binding:
  `inconclusive`.
- Reference construction influenced by revealed tracer/legacy output:
  `inconclusive` and redraw a new sealed universe.
- NL132 or RNA evidence used to create an NL116 DNA-only expected positive:
  invalid row and, if found after reveal, `inconclusive`.
- Fixed-spike ISTD presence transferred to its paired analyte:
  invalid row.
- Unknown or unassessable evidence converted to a negative:
  invalid row.

## Machine-Readable Owner

The row and seal shape is
[`cid_nl_reference_universe_manifest_schema.v1.json`](../schemas/cid_nl_reference_universe_manifest_schema.v1.json).
The review and adjudication events are owned by
[`cid_nl_oracle_review_adjudication_schema.v1.json`](../schemas/cid_nl_oracle_review_adjudication_schema.v1.json).

This protocol grants no Matrix, Backfill, counted-detection, selected-area,
workbook, GUI, activation, export, or compound-identification authority.

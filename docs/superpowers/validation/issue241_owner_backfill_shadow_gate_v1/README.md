# Issue #241 Owner Backfill shadow-gate evidence

Validation evidence status: `production_ready` for the bounded Owner Backfill
cutover. This packet grants no new Matrix or ProductWriter authority.

Issue: [#241](https://github.com/Chao-hu-Lab/XIC_Extractor/issues/241).
Parent programme: [#238](https://github.com/Chao-hu-Lab/XIC_Extractor/issues/238).

## Frozen comparison

- Activation-base implementation: `548238dee445d546190854bf5a9d165751d04350`.
- Shared-fact candidate: `4d93e6be58a2f4a485c5ec112e0da19802aff7a6`.
- Preset and scope: `dna_dr`, `production-equivalent`,
  `validation-minimal`, `audit-evidence-mode=none`.
- Acquisition: current-schema discovery-owner closure, exact Owner Backfill
  windows, one RAW worker, and no cache, super-window, or checkpoint path.
- Fact-cost scope: peak facts produced from the already acquired Owner Backfill
  trace. A fact receipt RAW-open count of zero therefore means no second RAW
  acquisition; it does not claim that the alignment run opened no RAW data.

The activation-base path and candidate used separate fresh output directories
against the same owner-bound discovery index and RAW identities. Legacy
Standard Peak was not used as the production parity baseline.

## Fresh RAW lanes

| Lane | Owner-bound input | Sample universe | Owner Backfill baseline | Owner Backfill candidate | Disposition |
| --- | --- | ---: | ---: | ---: | --- |
| 2RAW | discovery revision `485139c304e80090bac2469105f299faa83619c9`; index SHA-256 `16223a06b9cee2d3108875c3a4c7056cf7a668b35479992040b2318e5f1a0a6f` | 2 | 8.096062 s | 8.106515 s | PASS |
| 8RAW | discovery revision `ecdb120a93104771f2590266de140017e9c4eba0`; index SHA-256 `233a0af0e8f6c93c2d5d5fc50536f85075f0b72aad9333724462cc7c9841e01a` | 8 | 309.967479 s | 310.964821 s | PASS |

The 2RAW samples were `TumorBC2263_DNA` and
`Breast_Cancer_Tissue_pooled_QC3`. The paired 8RAW lane added
`TumorBC2312_DNA`, `NormalBC2263_DNA`, `NormalBC2312_DNA`,
`Breast_Cancer_Tissue_pooled_QC5`, `BenignfatBC1055_DNA`, and
`BenignfatBC1151_DNA`.

All durations above are finite observations rendered from the execution timing
records. They are single busy-machine observations, not a speedup claim. The
candidate delta was +0.13% in 2RAW and +0.32% in the 8RAW Owner Backfill stage.

## Owner-derived call and identity receipt

| Observation | 2RAW baseline | 2RAW candidate | 8RAW baseline | 8RAW candidate |
| --- | ---: | ---: | ---: | ---: |
| Logical trace evaluations | 688 | 688 | 29,458 | 29,458 |
| Gaussian smoothing | 684 | 684 | 27,516 | 27,516 |
| Gaussian integration | 684 | 684 | 27,516 | 27,516 |
| Fact-scope RAW opens | 0 | 0 | 0 | 0 |
| Artifact round trips | 0 | 0 | 0 | 0 |
| Sealed candidate observations | unavailable on base | 688 | unavailable on base | 29,458 |
| Identity-unavailable observations | unavailable on base | 0 | unavailable on base | 0 |
| Finite positive available-fact durations | 684 | 684 | 27,516 | 27,516 |

The 2RAW candidate bound 688 group hypotheses across two acquisition sources.
The 8RAW candidate bound 29,458 observations to 4,365 group hypotheses across
eight acquisition sources. Every observation retained its ordered
`group_hypothesis_id x sample x acquisition` identity. The 684 and 27,516
available facts retained mandatory Gaussian-smoothed boundary, shape, and
Gaussian area evidence. All receipts reported
`evidence_only_no_write_authority`.

Owner acquisition cardinality was also exact:

| Lane | Logical requests | Canonical requests | Equivalent logical requests | XIC extractions | RAW chromatogram calls | Trace points |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2RAW | 688 | 687 | 1 | 687 | 481 | 122,371 |
| 8RAW | 29,458 | 29,437 | 21 | 29,437 | 18,563 | 5,560,652 |

Every value in this table was identical between the activation base and the
candidate. The candidate fact computation consumed the already acquired trace;
no second acquisition or persisted-artifact rehydration path was observed.

## Exact parity

The following activation-base and candidate artifacts were byte-identical in
the paired 8RAW lane:

| Artifact | SHA-256 |
| --- | --- |
| `alignment_backfill_cell_evidence.tsv` | `a0149c61bcf956018a69ed79c73674a2157723eecd7d2bb1c83a2d3a7c6c4bfc` |
| `alignment_matrix_identity.tsv` | `e7e93d0fe6acfa120012f1c0375940983d69c7c284a4df67289232efb87761a8` |
| `alignment_matrix.tsv` | `979974f14289e66e0deded2c8ffaddd1ad68ceaa4a52837ea960973013dc5489` |
| `alignment_owner_backfill_seed_audit.tsv` | `f55e873e5d9325650950d70b987d2a64f324d9a4cd528ba40f79004f6a7c5b9f` |
| `alignment_review.tsv` | `eb82ab06f98fa8d01d94ca6ed53f410a421ab91c63c8fcc8b498e764aaaeb389` |
| `skipped_evidence_ledger.tsv` | `84154068d364e6d1e5215c09d748eba4779fdf3c597996e0c5a98a6a4b15fca1` |
| formal `activation_application_summary.tsv` | `3d67e378f9c6395eaa62ac9797007ec93fd1be0697d6f88b8e74de4fbfa15e8c` |
| formal `activation_hypothesis_identity.tsv` | `6f61644bc7d7be54b12e1f81a6c739abdbded03ed298f1a3f155fc8de5456f08` |
| formal `activation_value_delta.tsv` | `841a4e46958415632b056cf6d54271ee21b074ff93f3045cb942ca04bfe6ad04` |

The 2RAW lane was also byte-identical for cell evidence, Matrix identity and
values, seed audit, review, and skipped-evidence ledger. Its Matrix SHA-256 was
`74ae5dfa97d4b7b0f0de2cf6285b1f0442f40177aa2a1294f3c415745db0afd1`.

The 8RAW formal product directory contained the same six-file inventory. Its
five value/identity TSV files were byte-identical. The manifest differed only
by its baseline-versus-candidate output locator and became identical after
normalizing that locator.

Across the full 4,498-file diagnostic output tree, 1,027 raw hashes differed.
After normalizing raw and JSON-escaped run locators, 30 files remained: the two
timing files, the fact receipt, and 27 downstream provenance projections. The
27 projections differed only because identical path-normalized trace-data JSON
had different raw SHA-256 values; field-level comparison found no authority,
decision, identity, boundary, area, Matrix, value, or failure difference.

## Negative controls and stop rules

- A stale previously accepted 8RAW discovery artifact was rejected before RAW
  open because it lacked the current five-column candidate schema. It was not
  accepted as evidence.
- A 2RAW compatibility launch without discovery-owner closure reported 688
  identity-unavailable observations and could not promote. The accepted lanes
  required owner closure and reported zero identity-unavailable observations.
- Focused tests retain disagreement, non-finite duration, partial identity, and
  multiple-hypothesis HOLD/no-write oracles. Any future disagreement in those
  classes stops the cutover claim.
- No 85RAW lane was launched.

## Review P1 closure: bounded process payload

PR #255 review identified that the first cutover retained each complete
normalized RT/intensity trace in `peak_fact_observations` and transported those
observations through process-worker results even though the production pipeline
did not consume them. Finding class I, `transaction cost amplification`, was
therefore reopened as a plan gap: the original gate bounded calls but not
retained or serialized payload.

Issue #241 was refrozen before the fix. Mandatory Gaussian-smoothed boundary,
shape, window, and Gaussian-area evidence still executes before the decision;
only the post-decision projection changed. Owner Backfill now retains bounded
scalar fact receipts with sealed identity, while process-worker results retain
only the aggregated owner-derived cost receipt. Full normalized RT/intensity
arrays and raw scan-index sequences do not cross that boundary.

The negative no-RAW oracle built equivalent three-point and 50,000-point full
facts, then proved that receipt pickle size stayed bounded, `raw_scan_indices`
was empty, no normalized trace field survived, and both worker-result dataclasses
excluded `peak_fact_observations`.

A fresh 8RAW process lane at commit
`96cf6065333482989a096f122b9402983e2a5602` used the same owner-bound discovery
index SHA-256
`233a0af0e8f6c93c2d5d5fc50536f85075f0b72aad9333724462cc7c9841e01a`,
exact RAW windows, `raw_workers=2`, and eight samples. It completed with eight
successful worker receipts and the following owner-derived result:

| Observation | Fresh 8RAW process result |
| --- | ---: |
| Sealed candidate observations | 29,458 |
| Identity-unavailable observations | 0 |
| Logical trace evaluations | 29,458 |
| Gaussian smoothing | 27,516 |
| Gaussian integration | 27,516 |
| Fact-scope RAW opens | 0 |
| Artifact round trips | 0 |
| Worker result contains peak-fact observation field | no |
| Total serialized worker-result bytes | 23,954,111 |
| Largest serialized worker result | 3,073,705 |
| Owner Backfill elapsed observation | 170.31 s |

The six primary output/failure TSVs and all three formal activation value,
identity, and application TSVs were byte-identical to the previously accepted
8RAW candidate. This fresh process run changes no Matrix, writer, failure, or
scientific authority. It closes the payload instance without making a memory or
wall-time speedup percentage claim.

## Review P2 closure: bounded integration-only projection

PR #255 then identified a sibling instance of the same finding class. The
compatibility-only `integration_with_trace_evidence` consumer requested an
enriched `IntegrationResult`, but the shared seam also sorted and copied the
complete trace into normalized Python tuples and built window evidence that the
caller immediately discarded. Finding class I, `transaction cost
amplification`, was reopened again because the first corrective plan enumerated
the Owner Backfill worker-result boundary but not both direct shared-seam
consumers.

Issue #241 was refrozen before the second fix. The shared evidence owner still
validates one trace and computes at most one AsLS baseline, one Gaussian15
morphology trace, and one integration. The integration-only path now returns
after that bounded work. Only the full-fact path projects normalized shape and
window evidence, which remains mandatory for Owner Backfill. No flag, second
smoothing pass, caller-specific evidence owner, Matrix authority, or public
output was added.

The no-RAW regression oracle first failed with both discarded projections
observed, then passed after the split. It proves that the integration-only path
performs one Gaussian15 smoothing while making zero normalized-shape or window
projection calls. The full-fact oracle still proves one shared Gaussian15 trace
feeds both shape and morphology area.

Fresh current-checkout validation at commit
`9c2976b6b4a5488cc913cc838501297576ead9ca` used exact windows,
`raw_workers=2`, and the same owner-bound inputs. The first 2RAW execution was
rejected because its receipt was supplied an incorrect full code-revision
string; its pipeline output was not used for gate acceptance. A fresh second
2RAW attempt bound the correct revision and passed. The accepted 2RAW and 8RAW
results were:

| Observation | 2RAW | 8RAW |
| --- | ---: | ---: |
| Worker receipts | 2 | 8 |
| Sealed trace evaluations | 688 | 29,458 |
| Gaussian smoothing | 684 | 27,516 |
| Gaussian integration | 684 | 27,516 |
| Fact-scope RAW opens | 0 | 0 |
| Artifact round trips | 0 | 0 |
| Identity-unavailable observations | 0 | 0 |
| Owner-build elapsed observation | 7.83 s | 22.43 s |

The six primary output/failure TSVs were byte-identical in both lanes. In the
8RAW lane, all five formal Matrix/value/identity TSVs were also byte-identical,
including the three activation application, hypothesis-identity, and value
delta projections. The accepted input hashes remained
`16223a06b9cee2d3108875c3a4c7056cf7a668b35479992040b2318e5f1a0a6f`
for 2RAW and
`233a0af0e8f6c93c2d5d5fc50536f85075f0b72aad9333724462cc7c9841e01a`
for 8RAW.

The comparable 8RAW owner-build observation decreased from 23.26 s to 22.43 s
(about 3.6%), while the Owner Backfill stage increased from 170.31 s to
183.09 s. These are single busy-machine observations and support no wall-time
speedup or regression claim. The promotion basis is exact output parity,
unchanged owner-derived call counts, and the negative projection-call oracle.

## Terminal disposition

Shadow promotion: `PASS`.

Production cutover: `GO` for commits
`4d93e6be58a2f4a485c5ec112e0da19802aff7a6` and
`96cf6065333482989a096f122b9402983e2a5602`, with the bounded sibling-consumer
correction at `9c2976b6b4a5488cc913cc838501297576ead9ca`. The final code path has one
Owner Backfill peak-fact source; no dormant shadow path or second production
calculation remains, no full trace crosses the worker result boundary, and the
integration-only sibling does not build discarded shape/window projections.
The result is positive on correctness, ownership, sealed identity, bounded
payload, and non-increasing semantic call cost. It does not claim a material
wall-time speedup or redesign hypothesis selection, ModeTrack, Matrix,
ProductWriter, or public output semantics.

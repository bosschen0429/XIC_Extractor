# Issue #180 M9 Gate C 85RAW acceptance

Status: `diagnostic_only`

Verdict: `APPROVE_GATE_C`

Producing code: `3518ed612a8dae1c65d0ecbb8284fc8233b52397`

This packet retains the compact, exact-byte evidence chain for the bounded,
opt-in M8 -> M2 -> checked QuantMatrixVersion projection. It grants no default
activation, M1 change, chemical-identity claim, or new writer authority.

## Outcome

- The method lane was fixed at precursor `349.215`, CID neutral loss
  `116.0474 Da`, and 85 breast-tissue injections. Sample metadata came from the
  supplied method document through the installed Documents DOCX table helper;
  all 85 rows joined exactly to the RAW universe.
- C1 completed successfully in 1,723.23 seconds. It retained 11 ModeTracks and
  classified 64 exact-owner candidates as 26 proposed and 38 rejected.
- C1 used 83 RAW opens and 79 XIC batches, both within the frozen maximum of 85.
  Runtime finalization was `succeeded`.
- Manual review passed all 26 proposed cells. The observation replay matched
  26/26 `peak_observation_content_id` values and 26/26 raw areas. Three family
  overlays were visually reviewed. This establishes physical peak and lineage
  placement only, not a new chemical identity.
- C2 consumed the exact checked manifest and wrote 26/26 expected Backfill
  cells, with 0 unused expected diffs and no unauthorized Matrix value.
  Rollback reproduced the baseline exactly.
- A fresh independent acceptance review returned `APPROVE_GATE_C` with no
  blocker. Residual authority remains `diagnostic_only`.

The producing v1 source retained the observation content ID but not the full
physical observation payload. Manual review therefore used one bounded replay
through the existing M7 adapter. The implementation now emits
`m9_mode_track_source_v2` with the existing canonical PeakObservation payload,
so a future run can review the same evidence without reopening RAW. This later
serialization-only correction does not change this packet's frozen hashes.

## Durable artifacts

The copied JSON, TSV, and PNG files are byte-identical to the accepted task
output. Manual-review locators beginning with
`output/issue180_m9_gate_c_85raw_3518ed61/` map to this directory; the three
reviewed hypothesis PNGs retain their original names under
`manual_review_overlays_v2/`.

| Artifact | SHA-256 |
| --- | --- |
| `proposal/proposal_receipt.json` | `FF36182247C3935246C706F75C23DD2C8F0E9F0692B953851E6E2BE879F4A674` |
| `proposal/proposal.json` | `31EDD1BD1E1078F9E8AE3D6B2090CC29963D1F76775061B0C4809BF9333FC707` |
| `proposal/mode_track_source.json` | `D957DF92C96028F614AE5D6BFA6BF594040C41B363C8D2BA6CB0CD3C5ED16B5D` |
| `manual_review_v1.json` | `A14AB1F2AAA0B1E33AA2B4ED619406C2B75964D19F3C9174905069B32478B15A` |
| `manual_review_observation_replay_v1.json` | `0E4E387DF99239151F807CB3C2D19B2DCDAAE3FD1DB5D6B6FD48305841923C5E` |
| `checked_manifest_v1.tsv` | `8228B70EB16B3BD60C3F6064F8FFD14D709EFC64930E6D2DB196E4568898E50B` |
| `c2_final_v1/result_qmv.json` | `98186C73EF72877B6F4569085A4281F138A6115DCF51E6730347153779A97638` |
| `c2_final_v1/final_receipt.json` | `8E751925102C35CDBA3420C727722788020C3340CE82896B806CE7ABAE63D110` |
| FAM000004 hypothesis PNG | `2A8499E068098089B73F46125EAAFE1CBF7A2CC2652163B4249FD8F1661B2BEE` |
| FAM000007 hypothesis PNG | `D0D574D465940DB8B7280C7847B3085B785FC9C65838EAC258D17F8B24371DD8` |
| FAM000009 hypothesis PNG | `15299537BC71500704FF5FCCECC0D90DAED0E2080328D3C0CE4454F138DF8331` |

## Verification

The accepted C1 artifacts were independently reloaded and checked against the
manual review, manifest, QMV result, expected diff, terminal Matrix oracle, and
baseline rollback hash. After the v2 evidence-retention correction, the focused
no-RAW suite reported `148 passed, 2 skipped`; Ruff and mypy passed for the
changed source and tests. The 85RAW run was not repeated because the correction
only serializes the immutable PeakObservation already present during C1.

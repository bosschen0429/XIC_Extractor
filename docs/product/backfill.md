# Backfill

Backfill（回填）revisits a missing cell after Discovery and Alignment have
established an owner-backed Target or cross-sample group. A revisit can produce
a local observation; it does not create a group, decide membership, count a
detection or grant [writer authority](decision-policy.md#writer-authority).
The code and its tests own how each step works; this page keeps what no single
module states: the boundaries across modules, the reasons behind them, and
their limits.

## Where Each Decision Lives

| Decision | Owner | Tests |
| --- | --- | --- |
| Missing cell, group support gate, mapping (Untargeted) | `alignment.backfill.conservative` | [`test_alignment_conservative_backfill.py`](../../tests/test_alignment_conservative_backfill.py) |
| Eligible questions, canonical subject, query RT and position basis | `alignment.backfill.cid_nl_planning` | |
| Paired-offset and band localization, apex tolerance | `BandRtLocator.backfill_locations` | [`test_band_rt_location.py`](../../tests/test_band_rt_location.py) |
| Method evidence reuse, search and competing attribution | `alignment.backfill.cid_nl_evidence` | [`test_cid_nl_multi_group_recovery.py`](../../tests/test_cid_nl_multi_group_recovery.py) |
| Competing claims and physical view equivalence | `peak_detection.physical_peak_equivalence` | [`test_physical_peak_claim_completion.py`](../../tests/test_physical_peak_claim_completion.py) |
| Cell value and no-write | `ProductionCellDecision` | [`test_alignment_cell_decision.py`](../../tests/test_alignment_cell_decision.py) |
| Targeted reference group and recovery | `alignment.backfill.istd_anchored`, `extraction.targeted_istd_backfill` | |

## Boundaries Across Modules

- **No new evidence.** A Backfill candidate never adds a direct SUPPORT vote,
  trains RT anchors, establishes a row, or counts toward its own support gate.
- **No moved members.** Initial members stay in their groups; Backfill refits
  no band, moves no member and creates no group. Only CrossSample can admit a
  missing-sample member, and only with unique SUPPORT.
- **No wider search.** Backfill evaluates what was acquired within the bounded
  query; it never widens the acquired window or reopens RAW to search
  elsewhere, and a wider signal context for integration admits no candidate
  outside the query.
- **A value is not membership.** A `backfilled` cell can hold a value without
  its sample becoming a detected member.
- **Targeted.** An accepted HOLD analyte stays not counted; an accepted ISTD
  receipt is identity confirmation. Recovery uses only evidence the run
  already acquired, and ISTDs and recovered samples never become analyte
  SUPPORT votes.
- **Switched off** means not searched: no completed-empty receipt, zero or
  SUPPORT is minted.

## Why It Works This Way

- **Query RT, not group RT.** Each sample is searched at an RT forecast from
  its same-cohort neighbors in injection order, so steep drift is followed
  rather than lagged. The group center stays a reference and display
  coordinate, not an identity key.
- **Unreliable localization does not block.** When held-out residuals are too
  large, Backfill still evaluates qualified candidates inside the acquired
  window, flagged as low confidence, rather than guessing a peak or widening
  the search; a reliably localized claim wins over an unlocated rival.
- **Missing Discovery events prove nothing.** The Discovery scan range can be
  narrower than a peak, so the absence of a Discovery event is not proof of
  missing local CID-NL.
- **One claim per peak.** Two queries can resolve the same physical peak,
  and only one claim wins. Same apex plus overlapping windows is not enough to block a recovery, because same-apex
  windows can hold different ions.
- **REJECT survives.** A cell whose initial assessment rejected missing MS1
  keeps that `REJECT` until the normal relation owner grants SUPPORT.

## Limits

- The extrapolation guard and the apex tolerance are operational bounds, not
  calibrated confidence intervals or identity decisions.
- The background screen (local prominence above three robust noise SDs) is
  conservative, not a measured blank false-positive rate or an absence claim.
- The physical-view tolerance is a comparison tolerance, not an error
  estimate; nearby RT, overlapping bounds, similar area or high correlation
  prove neither identity nor separation.
- No search-coordinate basis establishes zero perturbation or permission to
  write a value.
- `alignment_backfill_cell_evidence.tsv` can be empty when no eligible cell was
  accepted; a population check needs a fixture known to exercise this path.

# Backfill

Backfill（回填）revisits a missing cell after Discovery and Alignment have
established an owner-backed Target or cross-sample group.
A revisit can produce a local observation. Backfill does not create a group,
decide membership, or itself count a detection. Product policy consumes the
accepted receipt as described below. Backfill does not grant Matrix writer
authority on its own. The current Untargeted CID-NL path uses the bounded
conservative decision in `xic_extractor.alignment.conservative_backfill`.

## Contract

### Targeted route

The Targeted production workflow freezes a reference group from its selected
direct CID-NL-supported observations, then calls the same conservative support
gate and candidate mapper. Sample roles and batch metadata come from the
existing sample-metadata owner; ISTDs do not add analyte SUPPORT votes.

For a paired Target, confirmed same-batch peers establish the measured
analyte-to-ISTD RT offset. `PeakTrajectory.backfill_locations` transfers that
offset to the current sample's credible paired ISTD, with the existing bounded
residual tolerance. Missing paired evidence, excessive offset scatter, or
competing confirmed groups does not authorize a Targeted recovery.

For an ISTD, the same frozen direct-support group and `PeakTrajectory` locate
the missing sample without requiring a paired ISTD. The same support gate and
conservative mapper apply; recovered samples never become direct SUPPORT votes.

The mapper consumes the complete retained candidate assessment and applies the
shared background-qualified physical Backfill question within the localized
window. It does not discard non-qualifying MS2 events to manufacture an empty
evidence set; set-level CID-NL attribution remains competing evidence. An
accepted observation may replace the initial observation-only capture, but
neither reranks direct SUPPORT peaks nor changes the original HOLD assessment.
The original physical integration is reused, not re-estimated by the writer.

The finalized scientific result retains the mapping receipt. Excel reports
`BACKFILL_ACCEPTED` or `BACKFILL_HOLD` through its existing diagnostics/review
surface. An accepted HOLD analyte retains `Counted Detection=False`. For an
ISTD, product projection treats the validated accepted Backfill receipt as
identity confirmation and sets `Counted Detection=True` when quantitation is
available and no exclusion or unresolved identity conflict blocks publication.
Missing required NL alone initially leaves an ISTD unconfirmed; expected
presence and MS1 coherence do not substitute for this receipt. Without an
accepted Backfill, existing initial Targeted quantitation is unchanged and is
not represented as a Backfill result. This retained-evidence route does not
expand the acquired trace or silently search another RAW window.

### Untargeted route

1. Normal Untargeted HTML runs, Alignment CLI runs, and preset runs enter the
   retained CID-NL workflow through `applications.untargeted.application.run_untargeted_application`.
2. `alignment.cid_nl_backfill_planning` owns eligible sample/group questions,
   subject and trajectory localization, and reuse/search/skip decisions. Sparse
   runtime execution obtains any missing initial assessment before resolving a
   question; `untargeted_execution.partition.workers` only adapts those decisions
   into bounded per-sample acquisition work.
3. Production composition in `untargeted_execution.partition_composition` maps the
   resulting evidence through the conservative Backfill decision.
4. The Untargeted route does not execute the retained `ObservationRevisit`
   transaction.

### Group support gate

The active decision uses one fixed group and the direct SUPPORT closure of
that group.

| Quantity | Rule |
| --- | --- |
| `N` | Counts non-excluded study, QC, and pooled-QC samples. Blank, calibrator, system-suitability, solvent, and unknown roles do not vote. |
| `K` | `K = max(2, N//20)`. |
| `S` | Counts distinct eligible samples with direct SUPPORT for that exact group. Each sample counts once. |

- If `S < K`, the conservative revisit does not start for that group.
- The workset contains every eligible sample without direct SUPPORT for the
  group, including samples with a HOLD or REJECT assessment. The frozen
  group and the local RT/mass request bound the workset.
- A Backfill candidate does not add a direct SUPPORT vote.

### Query RT versus group RT

The frozen group center remains the group's identity/evidence coordinate.
The request center is sample-specific: take up to twelve same-cohort
injection neighbors, convert their measured RTs to common coordinates when
supported, fit a robust local linear trend over injection order so steep drift
is followed rather than lagged, then apply the target sample's frozen batch warp. Both XIC bounds
and preferred-peak selection use this query RT. Insufficient calibration and
missing-context fallbacks have explicit bases; neither means measured zero
shift. See the [RT mechanism table](alignment.md#rt-mechanisms-and-their-consumers).

Forecast acceptance is narrower than the acquired context: same-cohort direct
reference samples are held out one at a time using that same trajectory
estimator. With at least two residuals, the apex tolerance is the larger of
12 seconds and their 90th percentile, capped by the acquisition half-window.
A residual p90 above that bound, or insufficient residuals, makes localization
unreliable and leaves the cell empty rather than accepting any nearby peak.
An observed trajectory position uses a 12-second bound (or the smaller
acquisition half-window). The context fallback never expands this apex bound.
A reliable query with no apex inside that bound can still nominate a physical
segment whose own boundaries contain the query center. Mere overlap with the
search window is insufficient. Multiple containing segments remain ambiguous;
one containing segment must pass the existing complete-context, acquired-support,
continuity, and background-contrast checks. This candidate recovery neither
changes the RT tolerance nor bypasses downstream ownership and duplicate-peak
decisions. Queries with an in-window apex keep their existing selection behavior.
A configured acquisition window narrower than 12 seconds remains valid when
the measured residual p90 fits inside it; the 12-second floor cannot itself
make localization unreliable. Residuals are prepared once per row/cohort, not
once per missing sample.
The retained physical evaluation distinguishes `sample_localization_unreliable`
from `no_peak_in_sample_local_window`; its nearest candidate RT remains
inspectable even when no numeric observation is accepted.

Completed-empty evidence binds both the original group center and actual query
RT. It validates the acquired query, not containment of the group center in a
shifted window. Supported Backfill admission uses the same frozen coordinates
and owner-to-band trend rule as final grouping; it does not refit the warp.

### Cell outcome

The physical Backfill question uses
`sample_local_background_qualified_peak_in_context`. It retains the ordinary
acquired-support and continuity requirements, and requires local prominence
above three robust noise standard deviations (1.4826 times the unscaled
short-scale MAD). Independently qualified censored hit clusters keep their
existing support rule. This is a conservative background screen, not a measured
blank false-positive rate or chemical-absence assertion. Initial and Detected
capture policies and the integral of an accepted physical segment are unchanged.
An isolated pair of non-adjacent raw hits remains insufficient original XIC
continuity; smoothing it into a peak does not supply missing acquired support.

1. An eligible missing Untargeted cell can yield
   `ms1_only_backfill_observation` when the cell has exactly one
   group-bounded MS1 peak and no same-sample CID-NL evidence.
2. `ProductionCellDecision` can write the area of that observation as
   `backfilled` / `accepted_backfill`.
3. A backfilled sample does not become a detected member of the group.
4. The cell gets no Backfill value in each of these cases:
   - no peak;
   - multiple peaks;
   - competing evidence;
   - insufficient group support;
   - an ineligible sample;
   - a member of a Matrix row already holds the peak;
   - the Backfill of another group also found the peak.
5. A member of an archive row writes no value and does not hold the peak.
6. If the windows of two rows resolve the same peak, the cell stays unresolved
   in both rows. The peak does not supply one value twice.
7. Each window traces the peak again with its own bounds. Therefore one peak
   means one apex scan in one sample within the preferred m/z tolerance, as in
   Discovery. One peak does not mean one peak-observation content ID.
8. When Backfill independently assesses a cell with an initial missing-MS1
   `REJECT`, the Backfill assessment retains the initial `REJECT`. The Backfill
   assessment must earn SUPPORT through the normal relation owner before the
   cell can hold a numeric value.

### Final cell evidence

Final cell composition consumes the exact CrossSample coverage handoff,
predecessor observations, and completed Backfill cell evidence. It does not
accept a separate repair receipt or repaired-observation population.
Conservative Backfill decisions, candidate mapping, and coverage terminals
retain their own provenance and authority boundaries.

Canonical final-cell and cell-decision identity encodings retain their existing
empty repair fields at the serialization boundary. These fields do not represent
an additional runtime repair path or change published identities.

## Surfaces

- `alignment_backfill_cell_evidence.tsv` is a compact validation output for
  accepted Backfill cells. Schema `alignment_backfill_cell_query_evidence` adds
  `backfill_query_rt_min` and `backfill_query_rt_max` from the executed assessment;
  `family_center_rt` remains a distinct group-level display value. Unavailable
  query bounds are empty, never copied from that display value. Readers require
  the query-bound columns; old compact TSV exports must be regenerated.
- Validation-minimal output selection emits that TSV. The ordinary production
  level writes the Matrix.
- The TSV can be empty when the run has no eligible accepted Backfill cell. A
  population check must use a fixture known to exercise this path.

## Boundaries

- `ProductionCellDecision` owns the selected numeric output and the
  write/no-write decision.
- CrossSample owns membership.
- The [Quant Matrix](quant-matrix.md), the
  [productization status index](../superpowers/validation/productization_status_index.tsv),
  and the [authority manifest](../superpowers/productization/status/productization_authority_manifest.json)
  own publication scope.
- Diagnostic traces, a well-formed coverage epoch, and candidate records do not
  grant any of the authorities listed above.

# Backfill

Backfill（回填）revisits a missing cell after Discovery and Alignment have
established an owner-backed Target or cross-sample group.
A revisit can produce a local observation. Backfill does not create a group,
decide membership, or itself count a detection. Product policy consumes the
accepted receipt as described below. Backfill does not grant Matrix writer
authority on its own. The current Untargeted CID-NL path uses the bounded
conservative decision in `xic_extractor.alignment.backfill.conservative`.

## Contract

### Missing cell

The two workflows define a missing cell differently:

- Untargeted: every eligible sample without direct SUPPORT for a non-rejected
  group that passes the [group support gate](#group-support-gate), including
  samples with a HOLD or REJECT assessment for that group.
- Targeted: a HOLD sample of a Target that is an internal standard or is paired
  with one, revisited from its direct CID-NL peers and the paired ISTD's
  observation in that sample.

Backfill cannot start without that prior Target or group context.

### Targeted route

`alignment.backfill.istd_anchored` freezes a reference group from a Target's
selected direct CID-NL-supported observations, then calls the same conservative
support gate and candidate mapper. `extraction.targeted_istd_backfill` supplies
it those peers and the paired ISTD's observations from the retained acquisition
results; the Targeted production workflow only composes the two. Sample roles and batch metadata come from the
existing sample-metadata owner; ISTDs do not add analyte SUPPORT votes.

For a paired Target, confirmed same-batch peers establish the measured
analyte-to-ISTD RT offset. `BandRtLocator.backfill_locations` transfers that
offset to the current sample's credible paired ISTD, with the existing bounded
residual tolerance. Missing paired evidence, excessive offset scatter, or
competing confirmed groups does not authorize a Targeted recovery.

For an ISTD, the same frozen direct-support group and the band locator locate
the missing sample without requiring a paired ISTD. The same support gate and
conservative mapper apply; recovered samples never become direct SUPPORT votes.

Targeted's selection owner supplies the identity-admitted candidate scope.
Method assessment binds that scope while retaining the full acquired NL evidence;
an unrelated, rejected alternate's positive NL cannot turn an unconfirmed chosen
ISTD into sample-level SUPPORT and skip its HOLD recovery. Admission to this
assessment does not itself authorize Backfill or a paired anchor: localization,
physical qualification and the accepted recovery decision still apply.

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
2. `alignment.backfill.cid_nl_planning` owns eligible sample/group questions,
   subject and trajectory localization; `alignment.backfill.cid_nl_evidence`
   owns method-specific reuse/search/skip and competing-attribution decisions. Sparse
   runtime execution obtains any missing initial assessment before resolving a
   question; `untargeted_execution.partition.workers` only adapts those decisions
   into bounded per-sample acquisition work.
3. Production composition in `untargeted_execution.partition_composition` maps the
   resulting evidence through the conservative Backfill decision.
4. The Untargeted route does not execute the retained `ObservationRevisit`
   transaction.

Backfill is enabled by default. The Workbench switch, Alignment CLI
`--no-backfill`, and `AlignmentConfig(backfill_enabled=False)` skip revisit
planning and acquisition, not just publication. Completion retains existing
sample-local evidence and exact no-input coverage. An otherwise empty local
lane records `backfill_disabled` and becomes `not_searched`; it cannot mint a
completed-empty receipt, zero value, or SUPPORT. A HOLD lane with local
candidates stays `unresolved`, because no group-support decision exists; with
Backfill enabled, a group below that threshold reports the same lane as
`not_searched`. Neither writes a Matrix value. Run metadata records
`backfill_execution=not_run`. Existing direct observations remain available.

Cross-subject groups retain each SUPPORT cell's exact assessment and hypothesis
binding. The supporting subject nearest the formal group center is the canonical
Backfill subject, with subject ID breaking ties. Missing/non-support cells use
that subject; another subject supplies support only through an exact owner binding.
Eligibility counts distinct supporting samples across the compatible workset,
not separately per method subject. Attempts bind group ID, sample and subject;
RT bounds describe the query, not the destination identity. Execution supplies
the grouping configuration and known-ISTD context; composition cannot replace it.

### Group support gate

The active decision uses one fixed group and the direct SUPPORT closure of
that group.

| Quantity | Rule |
| --- | --- |
| `N` | Counts non-excluded `sample` and `qc` injections. `std`, `blank` and `other` roles do not vote. |
| `K` | `K = max(2, N//20)`. |
| `S` | Counts distinct eligible samples with direct SUPPORT for that exact group. Each sample counts once. |

- If `S < K`, the conservative revisit does not start for that group.
- The workset contains every eligible sample without direct SUPPORT for the
  group, including samples with a HOLD or REJECT assessment. The frozen
  group and the local RT/mass request bound the workset.
- A Backfill candidate does not add a direct SUPPORT vote.

The same sample eligibility and direct-SUPPORT quorum gate the publication of
an MS1-only group member found during initial acquisition. The peak can still
be compared and assigned before this check. A member that fails the value
gate remains observed but has an `unresolved` cell with no numeric value.
An exact local partial or set-level CID-NL relation also stays unresolved;
a relation to a different peak does not veto an MS1-only value. Initial
intersection acquisition checks unresolved measured peaks for local CID-NL
within their MS1 bounds. The Discovery scan range can be narrower than those
peaks, so a missing Discovery event is not proof of missing local CID-NL.
A window without qualifying local scans does not supply method support.
Supplemental scans can confirm that member, but cannot help establish the
row, train its RT anchors, or vote toward its own S/K requirement. They do
not change the separate Backfill execution switch.

### Query RT versus group RT

The frozen group center remains its reference/display coordinate, not an identity key.
The request center is sample-specific: take up to twelve same-cohort
injection neighbors, convert their measured RTs to common coordinates when
all references are supported, fit a robust local linear trend over injection
order so steep drift is followed rather than lagged, then invert the same frozen
batch calibration for the target sample. A group with drift coefficients fits
that trend with its modeled drift removed and adds the target's drift back,
using the coefficients final grouping chose. Neighbors with unknown drift are
left out of that fit; a target with unknown drift keeps the plain coordinates. If that inverse is unsupported, refit
the whole forecast in observed coordinates. Both XIC bounds
and preferred-peak selection use this query RT. Insufficient calibration and
missing-context fallbacks have explicit bases; neither means measured zero
shift. See [RT calibration](rt-calibration.md).

Forecast acceptance is narrower than the acquired context: same-cohort direct
reference samples are held out one at a time using that same trajectory
estimator. With at least two residuals, the apex tolerance is the larger of
12 seconds and their 90th percentile, capped by the acquisition half-window.
A residual p90 above that bound, or insufficient residuals, makes localization
unreliable. Untargeted still evaluates qualified physical candidates already
inside the acquired bounded window; the forecast does not select among multiple
segments. A unique qualified candidate can enter the fixed-band comparison with a
`backfill_low_localization_confidence` risk. A reliably localized candidate
wins over an unlocated rival when both claim the same physical peak. This does not expand acquisition,
change its integral, or relax method-specific quantitation eligibility.
An observed trajectory position uses a 12-second bound (or the smaller
acquisition half-window). The context fallback never expands this apex bound.
A reliable query with no apex inside that bound can still nominate a physical
segment whose own boundaries contain the query center. Mere overlap with the
search window is insufficient. Multiple containing segments remain ambiguous;
one containing segment must pass the existing complete-context, acquired-support,
continuity, and background-contrast checks. MS1-only observations
remain eligible for the existing cell policy; rejected Discovery rows supply neither
new roots nor borrowed NL evidence. This candidate recovery neither
changes the RT tolerance nor bypasses downstream ownership and duplicate-peak
decisions. Queries with an in-window apex keep their existing selection behavior.
A configured acquisition window narrower than 12 seconds remains valid when
the measured residual p90 fits inside it; the 12-second floor cannot itself
make localization unreliable. Residuals are prepared once per row/cohort, not
once per missing sample.
The retained physical question records unreliable localization separately from
its physical answer. No peak, ambiguous segments, and insufficient acquired
support remain explicit unavailable answers; low confidence alone is not a
physical failure.

Completed-empty evidence binds both the original group center and actual query
RT. It validates the acquired query, not containment of the group center in a
shifted window. Supported Backfill admission reads the fixed band
and its acquired query; it does not refit the band or move any member.

Missing-position estimates use the trajectory's same-cohort observations. With
calibrated target and neighbor support the basis is `calibrated_trajectory_fit`;
otherwise it is `trajectory_fit` in measured coordinates. Observed positions
retain their measured value and use `trajectory_observed`.
For extrapolation, the fit's residual scale amplified by prediction leverage
is compared with the configured Backfill half-window. If that risk proxy exceeds
the window, the query uses the fitted RT at the nearest supported injection and
its basis ends in `trajectory_fit_at_support_boundary`; target-sample coordinate conversion
still applies. Interpolation and lower-risk extrapolation retain the local line.
This guard is not a calibrated confidence interval or an identity decision.
Missing injection order or same-cohort observations leaves only the median
reference position of the trajectory (`trajectory_reference`). None of these
search-coordinate descriptions establishes zero perturbation or permission to
write a value.

For an unobserved sample, a forecast extended by MS1-only positions must agree
with the same-cohort direct-reference trend when that trend has at least three
distinct injection positions and bounded held-out residuals. Disagreement beyond
the larger empirical RT tolerance leaves localization unreliable: it does not
select either predicted peak or widen the search. Observed correspondence and
independently measured paired-ISTD localization retain their existing rules.

### Search context

A matched MS1-only context peak supplies a trajectory position, not CID-NL
support, chemical identity or a selected integral. An existing HOLD is reusable
only if it contains that location; otherwise the physical owner evaluates the
requested location. A trajectory without matched context still contains its
direct owners.

Physical Backfill keeps candidate search bounds separate from MS1 signal
context. The existing query half-window bounds eligible peak apices and the
method-evidence search. The acquisition phase also prefetches one additional
half-window per flank, in the same sample-local batch. Only an
`incomplete_peak_context` answer permits evaluation of that wider trace;
complete search-eligible observations retain their original boundaries and
integral. This bounded fallback does not expand candidate eligibility, reopen RAW, or grant
membership. Multiple eligible peaks still follow the physical owner's
ambiguity rule. The persisted `bounded_physical_peak_question` binds the
search interval independently of its measured context; legacy questions
retain their original interpretation.

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
   group-bounded MS1 peak and no qualifying CID-NL relation. The peak can be
   newly measured or a measured HOLD peak without a unique qualifying relation;
   that sample's NL events stay recorded but do not support the value.
2. `ProductionCellDecision` can write the area of that observation as a
   `backfilled` cell.
3. A backfilled sample does not become a detected member of the group.
4. The cell gets no Backfill value in each of these cases:
   - no peak;
   - multiple peaks;
   - competing evidence;
   - insufficient group support;
   - an ineligible sample;
   - a member of a Matrix row already holds the peak;
   - another compatible candidate already claimed the peak, or the candidate
     exceeds the fixed band's mass span.
5. A member of an archive row writes no value and does not hold the peak.
6. If two queries resolve the same peak, prefer reliable localization, then
   the smaller distance to the acquired query, then measured peak order.
   Record the accepted and alternative members without moving original
   members. Shape contradictions are advisory; precursor span, method
   compatibility and physical uniqueness still constrain admission.
7. Direct observations and Backfill observations use the shared
   [physical-view evidence rule](#physical-peak-view-equivalence).
   Different bounds or context-dependent apices do not establish
   different peaks. A Matrix row's Direct member keeps its existing value;
   another row cannot Backfill the same measured peak. Competing Backfills have
   one selected feasible assignment, whether they carry SUPPORT or MS1-only
   HOLD. Accepted choices use `AcceptedMembershipRisk` with `phase=backfill`,
   including `backfill_competition` for contested claims. Final row/sample,
   review TSV and compact explanation/Inspector retain these same facts.
   Inspector's loaded-item search accepts the phase and exact risk flag text,
   without filtering out the selected numeric value.
   Claim resolution runs once before new members or numeric cells are created. A SUPPORT proposal that
   cannot join its requested feature does not veto an otherwise valid recovery.
   Each view uses its actual extraction m/z interval, not a second preferred-ppm
   occupancy rule. Same apex plus overlapping windows is not sufficient to
   block a distinct ion's recovery. Unresolved comparison evidence neither
   merges views nor invents a competing claim. No RT epsilon joins apices.
8. When Backfill independently assesses a cell with an initial missing-MS1
   `REJECT`, the Backfill assessment retains the initial `REJECT`. The Backfill
   assessment must earn SUPPORT through the normal relation owner before the
   cell can hold a numeric value.

### Membership admission

Initial members stay in their original groups through bounded Backfill.
A group-bound Backfill with unique SUPPORT may add a missing-sample member
when the candidate is compatible with the fixed band and its whole precursor
mass span. The grouping owner records
the admitted member and exact predecessor/successor lineage; it does not refit,
move original members, split known ISTD bindings, or create new groups.
Competing claims on the same physical content keep at most one winner, ordered
by localization confidence and distance to the bounded query. The accepted
cell records a competition flag. Final cells retain the original cell for provenance and the
completed Backfill assessment/observation for the final decision and integral.
Cell evidence uses `cell_observation` and binds unresolved SUPPORT and
Backfill provenance explicitly. Acquisition
cache facts do not depend on these cell schemas.

### Final cell evidence

Final cell composition consumes the exact CrossSample coverage handoff,
predecessor observations, and completed Backfill cell evidence. It does not
accept a separate repair receipt or repaired-observation population.
Conservative Backfill decisions, candidate mapping, and coverage terminals
retain their own provenance and authority boundaries.

Canonical final-cell and cell-decision identity encodings retain their existing
empty repair fields at the serialization boundary. These fields do not represent
an additional runtime repair path or change published identities.


## Physical Peak View Equivalence

`peak_detection.physical_peak_equivalence` compares views for Alignment claim
resolution and reports `same`, `distinct` or `unresolved`; none is a chemical
identification. Comparison never changes a selected integral or grants
membership.

- Views must share an acquisition source and overlapping extraction m/z
  intervals.
- Same-apex views are equivalent when their samples are identical in the common
  selected interval. Small flank differences are allowed when both intervals
  keep exactly the same raw peak maximum, the overlap has at least five matching
  acquisition times, and the integral of the absolute signal difference is at
  most 5% of the integral of the pointwise maximum in that overlap. This is a
  comparison tolerance, not an error estimate; neither trace is rescaled.
- Different apices need identical measured overlap around both apices and the
  physical segment owner assigning both queries to one lobe. Resolved separate
  lobes stay distinct. Nearby RT, overlapping bounds, similar area or high
  correlation alone prove neither identity nor separation.
- Alignment applies occupancy on top: existing Direct values remain; competing
  eligible Backfills do not create duplicate members or cells. `unresolved`
  does not establish a competing claim, since same-apex windows can contain
  different ions. Archive rows do not occupy Matrix peaks.

## Surfaces

- `alignment_backfill_cell_evidence.tsv` is a compact validation output of
  the Backfill searches: one row per cell with a peak whose group-bounded
  search retained one query window. Cells never searched that way are absent,
  whether `detected` or `backfilled`, and other cells of the same row are not
  copied in. It adds `backfill_query_rt_min` and `backfill_query_rt_max` from
  the executed assessment; Review's `group_center_rt` remains a distinct
  group-level display value. Readers require the query-bound columns; old
  compact TSV exports must be regenerated.
- Validation-minimal output selection emits that TSV. The ordinary production
  level writes the Matrix.
- The TSV can be empty when the run has no eligible accepted Backfill cell. A
  population check must use a fixture known to exercise this path.

## Boundaries

- `ProductionCellDecision` owns the selected numeric output and the
  write/no-write decision.
- CrossSample owns membership.
- The [Quant Matrix](quant-matrix.md) and [Writer Authority](decision-policy.md#writer-authority) own
  publication scope.
- Diagnostic traces, a well-formed coverage epoch, and candidate records do not
  grant any of the authorities listed above.

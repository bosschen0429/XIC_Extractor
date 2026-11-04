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
| `N` | Counts non-excluded study, QC, and pooled-QC samples. Blank, calibrator, system-suitability, solvent, and unknown roles do not vote. |
| `K` | `K = max(2, N//20)`. |
| `S` | Counts distinct eligible samples with direct SUPPORT for that exact group. Each sample counts once. |

- If `S < K`, the conservative revisit does not start for that group.
- The workset contains every eligible sample without direct SUPPORT for the
  group, including samples with a HOLD or REJECT assessment. The frozen
  group and the local RT/mass request bound the workset.
- A Backfill candidate does not add a direct SUPPORT vote.

### Query RT versus group RT

The frozen group center remains its reference/display coordinate, not an identity key.
The request center is sample-specific: take up to twelve same-cohort
injection neighbors, convert their measured RTs to common coordinates when
all references are supported, fit a robust local linear trend over injection
order so steep drift is followed rather than lagged, then invert the same frozen
batch calibration for the target sample. If that inverse is unsupported, refit
the whole forecast in observed coordinates. Both XIC bounds
and preferred-peak selection use this query RT. Insufficient calibration and
missing-context fallbacks have explicit bases; neither means measured zero
shift. See [Alignment RT coordinates](alignment.md#prepare-rt-coordinates).

Forecast acceptance is narrower than the acquired context: same-cohort direct
reference samples are held out one at a time using that same trajectory
estimator. With at least two residuals, the apex tolerance is the larger of
12 seconds and their 90th percentile, capped by the acquisition half-window.
A residual p90 above that bound, or insufficient residuals, makes localization
unreliable. Untargeted still evaluates qualified physical candidates already
inside the acquired bounded window; the forecast does not select among multiple
segments. A unique qualified candidate can enter the fixed-group comparison
with one-quarter of its otherwise applicable RT weight and a
`backfill_low_localization_confidence` risk. This does not expand acquisition,
change its integral, or relax method-specific quantitation eligibility.
An observed trajectory position uses a 12-second bound (or the smaller
acquisition half-window). The context fallback never expands this apex bound.
A reliable query with no apex inside that bound can still nominate a physical
segment whose own boundaries contain the query center. Mere overlap with the
search window is insufficient. Multiple containing segments remain ambiguous;
one containing segment must pass the existing complete-context, acquired-support,
continuity, and background-contrast checks. An evaluation requiring overlap
review does not mint a Backfill observation, even when its envelope is measurable
and an admitted group authorized the query. Clean MS1-only observations remain
eligible for the existing cell policy; rejected Discovery rows supply neither
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
shifted window. Supported Backfill admission uses the same frozen coordinates
and owner-to-band trend rule as final grouping; it does not refit the warp.

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
   - another feasible Backfill assignment wins, or blank has lower cost.
5. A member of an archive row writes no value and does not hold the peak.
6. If the windows of two rows resolve the same peak, compare fixed-group
   assignments and blank using the shared membership cost. Retain at most 64
   candidate configurations at each bounded extension, choose the lowest cost
   (mean peer fit resolves cost ties), then retry complete compatible group
   bundles and local add/remove/reassignment choices until none improves it.
   Partial RT gaps cannot permanently discard a feasible complete extension.
   Retain a feasible alternative and its cost gap. Original members never move.
   Conflicts between fixed original members do not create an infinite subtraction
   baseline; all pairs involving new candidates still obey hard constraints.
   An accepted extension of such a group carries `fixed_original_conflict`.
   The peak supplies at most one value; equivalent choices remain visible rather than making every row blank.
   Newly added cross-sample shape contradictions contribute soft cost and risk,
   while mass, identity, measurement and physical uniqueness remain constraints.
7. Direct observations and Backfill observations use the shared
   [physical-view evidence rule](../lc-msms-evidence-rules.md#physical-peak-view-equivalence).
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
A group-bound Backfill with unique SUPPORT may add a missing sample member
when it passes the existing feature evidence rule. The grouping owner records
the admitted member and exact predecessor/successor lineage; it does not refit,
move original members, split known ISTD bindings, or create new groups.
Competing claims on the same physical content remain unresolved and supply no
numeric value. Final cells retain the original cell for provenance and the
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

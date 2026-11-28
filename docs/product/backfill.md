# Backfill

Backfill（回填）revisits a missing cell after Discovery and Alignment have
established an owner-backed Target or cross-sample group.
A revisit can produce a local observation. Backfill does not create a group,
decide membership, or itself count a detection. Product policy consumes the
accepted receipt as described below. Backfill does not grant Matrix writer
authority on its own.

## At A Glance

| Question | Rule | Detail |
| --- | --- | --- |
| Which cells does Backfill revisit? | Untargeted: every eligible sample without direct SUPPORT for a group. Targeted: a HOLD sample of an ISTD or an ISTD-paired Target. | [Missing cell](#missing-cell) |
| When does a group qualify? | When enough distinct samples support it directly: `S >= K`. | [Group support gate](#group-support-gate) |
| Where does Backfill look? | At a sample-specific query RT forecast from same-cohort neighbors, inside the acquired bounded window. | [Query RT versus group RT](#query-rt-versus-group-rt) |
| What earns a value? | Exactly one group-bounded, background-qualified MS1 peak with no competing evidence. The cell is written as `backfilled`. | [Cell outcome](#cell-outcome) |
| What if two queries claim one peak? | One claim wins; the other cell gets no value. | [Competing claims](#competing-claims) |
| Does a backfilled sample join the group? | Only with unique SUPPORT, admitted by CrossSample. A backfilled value alone is not membership. | [Membership admission](#membership-admission) |
| What does Backfill never do? | Add a direct SUPPORT vote, move an original member, refit a band or widen the acquired window. | [Boundaries](#boundaries) |

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

Targeted Backfill recovers a HOLD sample from evidence the run already
acquired. It does not expand the acquired trace or search another RAW window.

- **Reference group.** A Target's selected direct CID-NL-supported observations
  form a frozen reference group. The route applies the same conservative
  support gate and candidate mapper as the Untargeted route. ISTDs do not add
  analyte SUPPORT votes, and recovered samples never become direct SUPPORT
  votes. Sample roles and batch metadata come from
  [sample metadata](sample-metadata-qc.md).
- **Paired Target.** Confirmed same-batch peers establish the measured
  analyte-to-ISTD RT offset. The band locator transfers that offset to the
  current sample's credible paired ISTD, within the bounded residual tolerance.
  Missing paired evidence, excessive offset scatter, or competing confirmed
  groups does not authorize a Targeted recovery.
- **ISTD.** The same frozen direct-support group and the band locator locate
  the missing sample without requiring a paired ISTD.
- **Candidate scope.** Targeted selection supplies the identity-admitted
  candidate scope. Method assessment binds that scope while retaining the full
  acquired NL evidence. An unrelated, rejected alternate's positive NL cannot
  turn an unconfirmed chosen ISTD into sample-level SUPPORT and skip its HOLD
  recovery. Admission to this assessment does not itself authorize Backfill or
  a paired anchor: localization, physical qualification and the accepted
  recovery decision still apply.
- **Mapping.** The mapper consumes the complete retained candidate assessment
  and applies the [physical Backfill question](#cell-outcome) within the
  localized window. Non-qualifying MS2 events stay in the evidence set;
  set-level CID-NL attribution remains competing evidence. An accepted
  observation can replace the initial observation-only capture. It neither
  reranks direct SUPPORT peaks nor changes the original HOLD assessment. The
  writer reuses the original physical integration and does not re-estimate it.

The finalized scientific result retains the mapping receipt. The workbook
reports `BACKFILL_ACCEPTED` or `BACKFILL_HOLD` on its diagnostics/review
surface.

| Case | Result |
| --- | --- |
| Accepted HOLD analyte | Retains `Counted Detection=False`. |
| Accepted ISTD | Product projection treats the validated accepted Backfill receipt as identity confirmation. It sets `Counted Detection=True` when quantitation is available and no exclusion or unresolved identity conflict blocks publication. |
| ISTD missing only its required NL | Starts unconfirmed. Expected presence and MS1 coherence do not substitute for the receipt. |
| No accepted Backfill | The initial Targeted quantitation stands and is not represented as a Backfill result. |

### Untargeted route

Backfill is enabled by default. Normal Untargeted HTML runs, Alignment CLI
runs and preset runs all enter the same CID-NL workflow.
[Where the code lives](#where-the-code-lives) names the modules.

**Turning Backfill off.** The Workbench switch, Alignment CLI `--no-backfill`,
and `AlignmentConfig(backfill_enabled=False)` skip revisit planning and
acquisition, not just publication.

- Direct observations remain available. Completion keeps sample-local evidence
  and exact no-input coverage.
- An otherwise empty local lane records `backfill_disabled` and becomes
  `not_searched`. It cannot mint a completed-empty receipt, zero value, or
  SUPPORT.
- A HOLD lane with local candidates stays `unresolved`, because no
  group-support decision exists. With Backfill enabled, a group below the
  support threshold reports the same lane as `not_searched`. Neither writes a
  Matrix value.
- Run metadata records `backfill_execution=not_run`.

**Groups that span method subjects.**

- Each SUPPORT cell keeps its exact assessment and hypothesis binding.
- The supporting subject nearest the formal group center is the canonical
  Backfill subject, with subject ID breaking ties. Missing and non-support
  cells use that subject; another subject supplies support only through an
  exact owner binding.
- Eligibility counts distinct supporting samples across the compatible
  workset, not separately per method subject.
- Attempts bind group ID, sample and subject. RT bounds describe the query, not
  the destination identity.
- Execution supplies the grouping configuration and known-ISTD context;
  composition cannot replace it.

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

**The same gate decides whether an MS1-only member publishes a value.** This
applies to an MS1-only group member found during initial acquisition, and uses
the same sample eligibility and direct-SUPPORT quorum.

- The peak can still be compared and assigned before this check.
- A member that fails the gate remains observed but has an `unresolved` cell
  with no numeric value.
- An exact local partial or set-level CID-NL relation also stays unresolved. A
  relation to a different peak does not veto an MS1-only value.
- Initial intersection acquisition checks unresolved measured peaks for local
  CID-NL within their MS1 bounds. The Discovery scan range can be narrower
  than those peaks, so a missing Discovery event is not proof of missing local
  CID-NL. A window without qualifying local scans does not supply method
  support.
- Supplemental scans can confirm that member. They cannot help establish the
  row, train its RT anchors, or vote toward its own S/K requirement, and they
  do not change the separate Backfill execution switch.

### Query RT versus group RT

Backfill searches at a query RT computed for each sample. The frozen group
center remains the group's reference and display coordinate; it is not an
identity key. Both the XIC bounds and preferred-peak selection use the query
RT. See [RT calibration](rt-calibration.md) for the coordinate conversion.

#### Query center

The request center is sample-specific:

1. Take up to twelve same-cohort injection neighbors.
2. Convert their measured RTs to common coordinates when all references are
   supported.
3. Fit a robust local linear trend over injection order, so steep drift is
   followed rather than lagged.
4. Invert the same frozen batch calibration for the target sample.

- A group with drift coefficients fits that trend with its modeled drift
  removed and adds the target's drift back, using the coefficients final
  grouping chose. Neighbors with unknown drift are left out of that fit; a
  target with unknown drift keeps the plain coordinates.
- If the inverse is unsupported, the whole forecast is refit in observed
  coordinates.
- Insufficient calibration and missing-context fallbacks have explicit bases;
  neither means measured zero shift.

#### Position basis

Missing-position estimates use the trajectory's same-cohort observations.

| Basis | When |
| --- | --- |
| `trajectory_observed` | The sample has an observed position, which keeps its measured value. |
| `calibrated_trajectory_fit` | The target and its neighbors have calibrated support. |
| `trajectory_fit` | The fit runs in measured coordinates. |
| ends in `trajectory_fit_at_support_boundary` | Extrapolation risk exceeds the Backfill half-window; see below. |
| `trajectory_reference` | Injection order or same-cohort observations are missing. The query uses the median reference position of the trajectory. |

For extrapolation, the fit's residual scale amplified by prediction leverage
is compared with the configured Backfill half-window. If that risk proxy
exceeds the window, the query uses the fitted RT at the nearest supported
injection; target-sample coordinate conversion still applies. Interpolation
and lower-risk extrapolation keep the local line. This guard is not a
calibrated confidence interval or an identity decision.

None of these search-coordinate descriptions establishes zero perturbation or
permission to write a value.

#### Apex tolerance

Forecast acceptance is narrower than the acquired context.

- Same-cohort direct reference samples are held out one at a time, using the
  same trajectory estimator. Residuals are prepared once per row and cohort,
  not once per missing sample.
- With at least two residuals, the apex tolerance is the larger of 12 seconds
  and their 90th percentile, capped by the acquisition half-window.
- An observed trajectory position uses a 12-second bound, or the smaller
  acquisition half-window. The context fallback never expands this apex bound.
- A configured acquisition window narrower than 12 seconds remains valid when
  the measured residual p90 fits inside it. The 12-second floor cannot itself
  make localization unreliable.

#### Unreliable localization

A residual p90 above the apex tolerance bound, or insufficient residuals, makes
localization unreliable.

- Untargeted still evaluates qualified physical candidates already inside the
  acquired bounded window. The forecast does not select among multiple
  segments.
- A unique qualified candidate can enter the fixed-band comparison with a
  `backfill_low_localization_confidence` risk.
- A reliably localized candidate wins over an unlocated rival when both claim
  the same physical peak.
- Unreliable localization does not expand acquisition, change the integral, or
  relax method-specific quantitation eligibility.
- The physical question records unreliable localization separately from its
  physical answer. No peak, ambiguous segments, and insufficient acquired
  support remain explicit unavailable answers; low confidence alone is not a
  physical failure.

For an unobserved sample, a forecast extended by MS1-only positions must agree
with the same-cohort direct-reference trend when that trend has at least three
distinct injection positions and bounded held-out residuals. Disagreement
beyond the larger empirical RT tolerance leaves localization unreliable: it
does not select either predicted peak or widen the search. Observed
correspondence and independently measured paired-ISTD localization follow
their own rules.

#### A segment that contains the query

A reliable query with no apex inside the apex bound can still nominate a
physical segment whose own boundaries contain the query center.

- Mere overlap with the search window is insufficient.
- Multiple containing segments remain ambiguous.
- One containing segment must pass the complete-context, acquired-support,
  continuity, and background-contrast checks.
- MS1-only observations remain eligible for the cell policy. Rejected
  Discovery rows supply neither new roots nor borrowed NL evidence.
- This recovery neither changes the RT tolerance nor bypasses downstream
  ownership and duplicate-peak decisions.

#### What the query does not move

Completed-empty evidence binds both the original group center and the actual
query RT. It validates the acquired query, not containment of the group center
in a shifted window. Supported Backfill admission reads the fixed band and its
acquired query; it does not refit the band or move any member.

### Search context

A matched MS1-only context peak supplies a trajectory position, not CID-NL
support, chemical identity or a selected integral. A HOLD is reusable only if
it contains that location; otherwise the physical owner evaluates the
requested location. A trajectory without matched context still contains its
direct owners.

Physical Backfill keeps candidate search bounds separate from MS1 signal
context:

- The query half-window bounds eligible peak apices and the method-evidence
  search.
- The acquisition phase also prefetches one additional half-window per flank,
  in the same sample-local batch.
- Only an `incomplete_peak_context` answer permits evaluation of that wider
  trace. Complete search-eligible observations keep their original boundaries
  and integral.
- This bounded fallback does not expand candidate eligibility, reopen RAW, or
  grant membership. Multiple eligible peaks still follow the physical owner's
  ambiguity rule.
- The persisted `bounded_physical_peak_question` binds the search interval
  independently of its measured context.

### Cell outcome

**The physical question.** Backfill asks
`sample_local_background_qualified_peak_in_context`.

- It applies the ordinary acquired-support and continuity requirements.
- It requires local prominence above three robust noise standard deviations
  (1.4826 times the unscaled short-scale MAD).
- Independently qualified censored hit clusters follow their own support rule.
- An isolated pair of non-adjacent raw hits is insufficient original XIC
  continuity. Smoothing it into a peak does not supply missing acquired
  support.
- This is a conservative background screen, not a measured blank
  false-positive rate or chemical-absence assertion. It does not change the
  Initial and Detected capture policies or the integral of an accepted
  physical segment.

**When a cell gets a value.** An eligible missing Untargeted cell can yield
`ms1_only_backfill_observation` when the cell has exactly one group-bounded
MS1 peak and no qualifying CID-NL relation.

- The peak can be newly measured, or a measured HOLD peak without a unique
  qualifying relation. That sample's NL events stay recorded but do not
  support the value.
- `ProductionCellDecision` can write the area of that observation as a
  `backfilled` cell.
- A backfilled sample does not become a detected member of the group.

**When a cell gets no Backfill value.**

- no peak;
- multiple peaks;
- competing evidence;
- insufficient group support;
- an ineligible sample;
- a member of a Matrix row already holds the peak;
- another compatible candidate already claimed the peak, or the candidate
  exceeds the fixed band's mass span.

A member of an archive row writes no value and does not hold the peak.

When Backfill independently assesses a cell with an initial missing-MS1
`REJECT`, the Backfill assessment keeps the initial `REJECT`. The Backfill
assessment must earn SUPPORT through the normal relation owner before the cell
can hold a numeric value.

#### Competing claims

Direct observations and Backfill observations use the shared
[physical-view evidence rule](#physical-peak-view-equivalence) to decide
whether two claims are on one peak. Different bounds or context-dependent
apices do not establish different peaks.

- **Direct wins.** A Matrix row's Direct member keeps its value; another row
  cannot Backfill the same measured peak.
- **One Backfill wins.** Competing Backfills have one selected feasible
  assignment, whether they carry SUPPORT or MS1-only HOLD. If two queries
  resolve the same peak, prefer reliable localization, then the smaller
  distance to the acquired query, then measured peak order.
- **Original members stay.** The accepted and alternative members are recorded
  without moving original members. Claim resolution runs once, before new
  members or numeric cells are created.
- **What constrains admission.** Shape contradictions are advisory. Precursor
  span, method compatibility and physical uniqueness still constrain
  admission. A SUPPORT proposal that cannot join its requested feature does
  not veto an otherwise valid recovery.
- **What does not block.** Each view uses its actual extraction m/z interval,
  not a second preferred-ppm occupancy rule. Same apex plus overlapping
  windows is not sufficient to block a distinct ion's recovery. Unresolved
  comparison evidence neither merges views nor invents a competing claim. No
  RT epsilon joins apices.

Accepted choices use `AcceptedMembershipRisk` with `phase=backfill`, including
`backfill_competition` for contested claims. The final row/sample, the review
TSV and the compact explanation shown in Inspector carry these same facts.
Inspector's loaded-item search accepts the phase and exact risk flag text,
without filtering out the selected numeric value.

### Membership admission

Initial members stay in their original groups through bounded Backfill.

- A group-bound Backfill with unique SUPPORT can add a missing-sample member
  when the candidate is compatible with the fixed band and its whole precursor
  mass span.
- The grouping owner records the admitted member and exact
  predecessor/successor lineage. It does not refit, move original members,
  split known ISTD bindings, or create new groups.
- Competing claims on the same physical content keep at most one winner,
  ordered by localization confidence and distance to the bounded query. The
  accepted cell records a competition flag.
- Final cells keep the original cell for provenance, and the completed
  Backfill assessment and observation for the final decision and integral.
- Cell evidence uses `cell_observation` and binds unresolved SUPPORT and
  Backfill provenance explicitly. Acquisition cache facts do not depend on
  these cell schemas.

### Final cell evidence

Final cell composition consumes the exact CrossSample coverage handoff,
predecessor observations, and completed Backfill cell evidence. Conservative
Backfill decisions, candidate mapping, and coverage terminals each keep their
own provenance and authority boundaries.

## Physical Peak View Equivalence

Alignment claim resolution compares two views of a physical peak and reports
`same`, `distinct` or `unresolved`. None is a chemical identification.
Comparison never changes a selected integral or grants membership.

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
- Alignment applies occupancy on top: Direct values remain; competing
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
  group-level display value. Readers require the query-bound columns.
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

## Where The Code Lives

For readers tracing a rule into source. Module docstrings own the details.

| Rule | Module |
| --- | --- |
| Conservative eligibility and mapping, Untargeted CID-NL path | `xic_extractor.alignment.backfill.conservative` |
| Targeted reference group and recovery decision | `alignment.backfill.istd_anchored` |
| Targeted peers and paired-ISTD observations, read from retained acquisition results | `extraction.targeted_istd_backfill` |
| Paired-offset and band localization | `BandRtLocator.backfill_locations` |
| Untargeted entry point | `applications.untargeted.application.run_untargeted_application` |
| Eligible sample/group questions, subject and trajectory localization | `alignment.backfill.cid_nl_planning` |
| Method-specific reuse, search, skip and competing-attribution decisions | `alignment.backfill.cid_nl_evidence` |
| Adapting those decisions into bounded per-sample acquisition work | `untargeted_execution.partition.workers` |
| Mapping the resulting evidence through the conservative decision | `untargeted_execution.partition_composition` |
| Physical peak view comparison | `peak_detection.physical_peak_equivalence` |

Sparse runtime execution obtains any missing initial assessment before
resolving a question. The Targeted production workflow only composes the two
Targeted modules.

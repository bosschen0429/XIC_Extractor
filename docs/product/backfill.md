# Backfill

Backfill（回填）revisits a missing cell after Discovery and Alignment have
established an owner-backed Target or cross-sample group. A revisit can produce
a local observation; it does not create a group, decide membership, count a
detection or grant Matrix writer authority. Product policy consumes the
accepted receipt as described below.

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

- Untargeted: every eligible sample without direct SUPPORT for a non-rejected
  group that passes the [group support gate](#group-support-gate), including
  samples with a HOLD or REJECT assessment for that group.
- Targeted: a HOLD sample of a Target that is an internal standard or is paired
  with one, revisited from its direct CID-NL peers and the paired ISTD's
  observation in that sample.

Backfill cannot start without that prior Target or group context.

### Targeted route

Targeted Backfill recovers a HOLD sample from evidence the run already
acquired; it does not expand the trace or search another RAW window.

- **Reference group.** A Target's selected direct CID-NL-supported
  observations form a frozen reference group, under the same support gate and
  candidate mapper as Untargeted. ISTDs add no analyte SUPPORT votes, and
  recovered samples never become direct votes. Roles and batches come from
  [sample metadata](sample-metadata-qc.md).
- **Paired Target.** Confirmed same-batch peers give the measured
  analyte-to-ISTD RT offset, transferred to the sample's credible paired ISTD
  within the bounded residual tolerance. Missing paired evidence, excessive
  offset scatter or competing confirmed groups authorize no recovery.
- **ISTD.** The same frozen group and band locator locate the missing sample
  without a paired ISTD.
- **Candidate scope.** Targeted selection supplies the identity-admitted scope;
  method assessment binds it while keeping the full acquired NL evidence. An
  unrelated, rejected alternate's positive NL cannot turn an unconfirmed chosen
  ISTD into SUPPORT and skip its HOLD recovery. Admission to this assessment
  does not itself authorize Backfill or a paired anchor: localization,
  physical qualification and the accepted recovery decision still apply.
- **Mapping.** The mapper consumes the complete candidate assessment and asks
  the [physical question](#cell-outcome) within the localized window.
  Non-qualifying MS2 events stay in the evidence; set-level CID-NL attribution
  stays competing evidence. An accepted observation can replace the initial
  observation-only capture; it neither reranks direct SUPPORT nor changes the
  HOLD assessment, and the writer reuses the original integration.

The finalized result keeps the mapping receipt; the workbook reports
`BACKFILL_ACCEPTED` or `BACKFILL_HOLD` on its diagnostics/review surface.

| Case | Result |
| --- | --- |
| Accepted HOLD analyte | Retains `Counted Detection=False`. |
| Accepted ISTD | Product projection treats the validated accepted Backfill receipt as identity confirmation. It sets `Counted Detection=True` when quantitation is available and no exclusion or unresolved identity conflict blocks publication. |
| ISTD missing only its required NL | Starts unconfirmed. Expected presence and MS1 coherence do not substitute for the receipt. |
| No accepted Backfill | The initial Targeted quantitation stands and is not represented as a Backfill result. |

### Untargeted route

Backfill is on by default; normal HTML, Alignment CLI and preset runs enter the
same CID-NL workflow.

- **Off switch.** The Workbench switch, Alignment CLI `--no-backfill` and
  `AlignmentConfig(backfill_enabled=False)` skip revisit planning and
  acquisition, not just publication. Direct observations, sample-local evidence
  and exact no-input coverage stay. An otherwise empty lane records
  `backfill_disabled` and becomes `not_searched`, never a completed-empty
  receipt, zero or SUPPORT. A HOLD lane with local candidates stays
  `unresolved`, since no group-support decision exists; with Backfill on, a
  group below the threshold reports that lane `not_searched`. Neither writes a
  value. Run metadata records `backfill_execution=not_run`.
- **Groups spanning method subjects.** Each SUPPORT cell keeps its exact
  assessment and hypothesis binding. The supporting subject nearest the group
  center is the canonical Backfill subject (subject ID breaks ties); missing
  and non-support cells use it, and another subject supplies support only
  through an exact owner binding. Eligibility counts distinct supporting
  samples across the workset, not per subject. Attempts bind group ID, sample
  and subject; RT bounds describe the query, not the destination. Execution
  supplies the grouping configuration and known-ISTD context; composition
  cannot replace it.

### Group support gate

The decision uses one fixed group and its direct SUPPORT closure.

| Quantity | Rule |
| --- | --- |
| `N` | Counts non-excluded `sample` and `qc` injections. `std`, `blank` and `other` roles do not vote. |
| `K` | `K = max(2, N//20)`. |
| `S` | Counts distinct eligible samples with direct SUPPORT for that exact group. Each sample counts once. |

- If `S < K`, the revisit does not start for that group. The workset holds
  every eligible sample without direct SUPPORT, HOLD and REJECT included,
  bounded by the frozen group and local RT/mass request. A Backfill candidate
  adds no SUPPORT vote.
- **The same gate decides whether an MS1-only member publishes a value**, for
  an MS1-only group member found during initial acquisition. The peak can be
  compared and assigned first; a member failing the gate stays observed with an
  `unresolved` cell and no value. An exact local partial or set-level CID-NL
  relation also stays unresolved, while a relation to a different peak does not
  veto the value.
- Initial acquisition checks unresolved measured peaks for local CID-NL within
  their MS1 bounds. The Discovery scan range can be narrower than those peaks,
  so a missing Discovery event does not prove missing local CID-NL; a window
  without qualifying local scans supplies no support. Supplemental scans can
  confirm that member, but cannot establish the row, train its RT anchors, vote
  toward its own S/K or change the execution switch.

### Query RT versus group RT

Backfill searches at a query RT computed per sample. The frozen group center
stays the group's reference and display coordinate, not an identity key. Both
the XIC bounds and preferred-peak selection use the query RT;
[RT calibration](rt-calibration.md) gives the coordinate conversion.

#### Query center

1. Take up to twelve same-cohort injection neighbors.
2. Convert their measured RTs to common coordinates when all references are
   supported.
3. Fit a robust local linear trend over injection order, so steep drift is
   followed rather than lagged.
4. Invert the same frozen batch calibration for the target sample.

A group with drift coefficients fits with its modeled drift removed and adds
the target's drift back; neighbors with unknown drift are left out, and a
target with unknown drift keeps plain coordinates. An unsupported inverse
refits the whole forecast in observed coordinates. Insufficient calibration and
missing-context fallbacks have explicit bases; neither means zero shift.

#### Position basis

| Basis | When |
| --- | --- |
| `trajectory_observed` | The sample has an observed position, which keeps its measured value. |
| `calibrated_trajectory_fit` | The target and its neighbors have calibrated support. |
| `trajectory_fit` | The fit runs in measured coordinates. |
| ends in `trajectory_fit_at_support_boundary` | Extrapolation risk exceeds the Backfill half-window; see below. |
| `trajectory_reference` | Injection order or same-cohort observations are missing. The query uses the median reference position of the trajectory. |

For extrapolation, the fit's residual scale amplified by prediction leverage is
compared with the Backfill half-window; above it, the query uses the fitted RT
at the nearest supported injection, still converted to the target's
coordinates; interpolation and lower-risk extrapolation keep the local line.
This guard is not a confidence interval or identity decision, and
no basis establishes zero perturbation or permission to write.

#### Apex tolerance

- Same-cohort direct reference samples are held out one at a time with the same
  estimator; residuals are prepared once per row and cohort.
- With at least two residuals, the apex tolerance is the larger of 12 seconds
  and their 90th percentile, capped by the acquisition half-window. An observed
  trajectory position uses 12 seconds or the smaller half-window. The context
  fallback never expands it.
- A window narrower than 12 seconds stays valid when the residual p90 fits
  inside it; the floor cannot itself make localization unreliable.

#### Unreliable localization

A residual p90 above the apex tolerance, or too few residuals, makes
localization unreliable.

- Untargeted still evaluates qualified candidates already inside the acquired
  window; the forecast does not choose among segments. A unique qualified
  candidate can enter the fixed-band comparison with a
  `backfill_low_localization_confidence` risk, and a reliably localized
  candidate wins over an unlocated rival for the same peak.
- It does not expand acquisition, change the integral or relax quantitation
  eligibility. The physical question records it separately from its answer;
  no peak, ambiguous segments and insufficient support stay explicit
  unavailable answers.
- For an unobserved sample, a forecast extended by MS1-only positions must
  agree with the same-cohort direct-reference trend when that trend has at
  least three distinct injection positions and bounded held-out residuals.
  Disagreement beyond the larger empirical tolerance leaves localization
  unreliable without picking either peak or widening the search. Observed
  correspondence and paired-ISTD localization follow their own rules.

#### A segment that contains the query

A reliable query with no apex inside the apex bound can still nominate one
physical segment whose own boundaries contain the query center; mere window
overlap is not enough, and several containing segments stay ambiguous. That
segment must pass the complete-context, acquired-support, continuity and
background-contrast checks. MS1-only observations stay eligible; rejected
Discovery rows supply no roots or borrowed NL evidence. This changes neither
the RT tolerance nor downstream ownership and duplicate-peak decisions.

#### What the query does not move

Completed-empty evidence binds both the group center and the actual query RT;
it validates the acquired query, not containment of the center in a shifted
window. Admission reads the fixed band and its query; it refits no band and
moves no member.

### Search context

Physical Backfill keeps candidate search bounds separate from MS1 signal
context. A matched MS1-only context peak supplies a trajectory position, not CID-NL
support, identity or an integral. A HOLD is reusable only if it contains that
location; otherwise the physical owner evaluates the requested location. A
trajectory without matched context still contains its direct owners.

- The query half-window bounds eligible apices and the method-evidence search.
  Acquisition also prefetches one more half-window per flank in the same
  sample-local batch.
- Only an `incomplete_peak_context` answer permits evaluating that wider trace;
  complete observations keep their boundaries and integral. The fallback does
  not expand eligibility, reopen RAW or grant membership, and several eligible
  peaks still follow the ambiguity rule.
- The persisted `bounded_physical_peak_question` binds the search interval
  independently of its measured context.

### Cell outcome

**The physical question** is
`sample_local_background_qualified_peak_in_context`: the ordinary
acquired-support and continuity requirements plus local prominence above three
robust noise standard deviations (1.4826 times the unscaled short-scale MAD).
Independently qualified censored hit clusters follow their own rule. An
isolated pair of non-adjacent raw hits is insufficient continuity, and
smoothing it into a peak supplies no support. This is a conservative
background screen, not a blank false-positive rate or absence claim, and it
changes neither the Initial and Detected capture policies nor an accepted
segment's integral.

**When a cell gets a value.** An eligible missing Untargeted cell yields
`ms1_only_backfill_observation` when it has exactly one group-bounded MS1 peak
and no qualifying CID-NL relation. The peak can be newly measured, or a
measured HOLD peak without a unique qualifying relation; that sample's NL
events stay recorded but do not support the value. `ProductionCellDecision`
can write its area as a `backfilled` cell; the sample does not become a
detected member.

**When a cell gets no value:** no peak; multiple peaks; competing evidence;
insufficient group support; an ineligible sample; a Matrix-row member already
holds the peak; or another compatible candidate claimed it, or the candidate
exceeds the fixed band's mass span. An archive-row member writes no value and
holds no peak. A cell whose initial missing-MS1 assessment is `REJECT` keeps
that `REJECT` and must earn SUPPORT through the normal relation owner before it
can hold a value.

#### Competing claims

Claims on one peak are decided by the
[physical-view rule](#physical-peak-view-equivalence); different bounds or
context-dependent apices do not make different peaks.

- **Direct wins.** A Matrix row's Direct member keeps its value; another row
  cannot Backfill that peak.
- **One Backfill wins.** Competing Backfills, SUPPORT or MS1-only HOLD, have
  one selected feasible assignment: reliable localization first, then the
  smaller distance to the acquired query, then measured peak order. Resolution
  runs once, before members or cells are created, and records the accepted and
  alternative members without moving original ones.
- **What constrains admission.** Precursor span, method compatibility and
  physical uniqueness; shape contradictions are advisory. A SUPPORT proposal
  that cannot join its feature does not veto an otherwise valid recovery.
- **What does not block.** Each view uses its actual extraction m/z interval,
  not a second preferred-ppm rule. Same apex plus overlapping windows does not
  block a distinct ion's recovery; unresolved comparison neither merges views
  nor invents a claim; no RT epsilon joins apices.

Accepted choices carry `AcceptedMembershipRisk` with `phase=backfill`
(`backfill_competition` for contested claims), in the final row/sample, the
review TSV and Inspector's explanation; Inspector's search accepts the phase
and flag text without filtering out the selected value.

### Membership admission

Initial members stay in their groups through Backfill. A group-bound Backfill
with unique SUPPORT can add a missing-sample member when compatible with the
fixed band and its whole precursor span. The grouping owner records the member
and exact predecessor/successor lineage; it does not refit, move members,
split known ISTD bindings or create groups. Final cells keep the original cell
for provenance and the completed Backfill assessment and observation for the
decision and integral, through `cell_observation` with explicit unresolved
SUPPORT and Backfill provenance; acquisition cache facts do not depend on
these schemas. Final composition consumes the CrossSample coverage handoff,
predecessor observations and completed Backfill evidence, each keeping its own
provenance and authority.

## Physical Peak View Equivalence

Claim resolution compares two views of a physical peak as `same`, `distinct`
or `unresolved`, never as identification, and never changes an integral or
grants membership.

- Views must share an acquisition source and overlapping extraction m/z
  intervals.
- Same-apex views are equivalent when their samples are identical in the
  common interval. Small flank differences are allowed when both keep exactly
  the same raw maximum, the overlap has at least five matching acquisition
  times, and the integrated absolute difference is at most 5% of the
  integrated pointwise maximum there. This is a comparison tolerance, not an
  error estimate; neither trace is rescaled.
- Different apices need identical measured overlap around both apices and the
  segment owner assigning both queries to one lobe. Resolved separate lobes
  stay distinct. Nearby RT, overlapping bounds, similar area or high
  correlation prove neither identity nor separation.
- Occupancy applies on top: Direct values stay, competing Backfills create no
  duplicate members or cells, `unresolved` is not a competing claim (same-apex
  windows can hold different ions), and archive rows occupy no Matrix peak.

## Surfaces

`alignment_backfill_cell_evidence.tsv` is a compact validation output: one row
per cell whose group-bounded search kept one query window. Cells never searched
that way are absent, whether `detected` or `backfilled`, and other cells of the
row are not copied in. It adds `backfill_query_rt_min` and
`backfill_query_rt_max` from the executed assessment, distinct from Review's
group-level `group_center_rt`; readers require them. Validation-minimal output
emits it; the production level writes the Matrix. It can be empty when no
eligible cell was accepted, so a population check needs a fixture known to
exercise this path.

## Boundaries

`ProductionCellDecision` owns the value and write/no-write decision;
CrossSample owns membership; the [Quant Matrix](quant-matrix.md) and
[Writer Authority](decision-policy.md#writer-authority) own publication scope.
Diagnostic traces, a well-formed coverage epoch and candidate records grant
none of these.

## Where The Code Lives

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

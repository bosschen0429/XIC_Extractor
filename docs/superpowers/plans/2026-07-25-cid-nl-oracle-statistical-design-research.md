# CID-NL Oracle No-RAW Statistical Design Research

Doc placement: repo_support_doc
Doc kind: note
Doc lifecycle: active
Repo owner: docs/product/discovery.md
Doc exit rule: Absorb retained statistical rules into the Issue #125 formal
execution packet, then archive this research note.

Date: 2026-07-25
Scope: external primary-source research for the CID-NL oracle acceptance
contract
Validation status: `diagnostic_only`
Decision authority: none; this note supplies statistical design inputs and does
not choose product thresholds, promotion policy, or writer authority.

## Executive Contract Implications

1. Proposal-conditioned precision and independent-reference recovery are
   different estimands with different sampling frames. They must not be turned
   into one synthetic 2×2 table by concatenating separately sampled proposal and
   reference-positive cases.
2. A raw proportion and a Wilson or Clopper-Pearson interval are valid only when
   the denominator consists of independent, equal-probability Bernoulli units
   for one defined population parameter. Unequal sampling weights, repeated
   sample-cells within a hypothesis/family, or adaptive selection require
   design-aware or cluster-aware inference.
3. Precision is prevalence-sensitive. A challenge set enriched with positives
   or hard negatives does not estimate deployment precision unless the frozen
   target-population mixture is restored by known design weights.
4. Recovery can be called sensitivity/recall only relative to a reference
   universe that independently and sufficiently enumerates the target
   positives. If positives can exist outside that frame, full-universe recovery
   is not identified.
5. Hidden duplicates estimate intra-rater repeatability, not correctness and not
   inter-rater reproducibility. The duplicate presentations cannot add weight to
   primary scientific denominators.
6. Abstention requires two coordinates: coverage and error among automated
   decisions. Class-conditional coverage and unconditional positive recovery
   are also needed to reveal selective abstention on hard positives.
7. Sample size cannot be chosen from a universal case count. It follows from a
   prespecified estimand, threshold or desired interval width, confidence level,
   expected rate, target prevalence/stratum mix, sampling design, and number of
   independent clusters.

These are statistical implications, not endorsements or rejections of any
existing XIC threshold.

## Notation And Estimands

For eligible primary unit \(i\):

- \(Y_i=1\): independently supported target-positive status under the declared
  reference definition;
- \(A_i=1\): the sealed tracer emits the positive proposal/action being
  evaluated;
- \(C_i=1\): the proposal/action is correct under the frozen oracle;
- \(G_i=1\): the automation makes a decision rather than abstaining/holding;
- \(Q_i=1\): the human oracle result is `unassessable`;
- \(\pi_i\): known probability that unit \(i\) is included in the review
  sample;
- \(w_i=1/\pi_i\): design weight;
- \(h(i)\): preregistered stratum;
- \(k(i)\): independent cluster, such as hypothesis/family, batch, or another
  declared dependency owner.

The target population, unit, time window, method scope, workflow policy, and
stratum mixture are part of every estimand. A metric without these bindings is
only a number, not an acceptance claim.

### Proposal-conditioned precision

For a census of all sealed positive proposals:

\[
\widehat{\mathrm{Precision}}_P
=
\frac{\sum_{i\in P} A_i C_i}
     {\sum_{i\in P} A_i}.
\]

For a probability sample from the proposal frame:

\[
\widehat{\mathrm{Precision}}_{P,w}
=
\frac{\sum_{i\in s_P} w_i A_i C_i}
     {\sum_{i\in s_P} w_i A_i}.
\]

This is the machine-learning use of “precision,” equivalent to positive
predictive value \(TP/(TP+FP)\), not the metrology use of precision as repeated
measurement dispersion. FDA gives both the count definition and the
prevalence-based PPV formula in its official imaging-study guidance
([FDA, 2004, pp. 31–32](https://www.fda.gov/media/71237/download)).

### Independent-reference recovery

For a census of the independently supported positive reference frame:

\[
\widehat{\mathrm{Recovery}}_R
=
\frac{\sum_{i\in R} Y_i A_i C_i}
     {\sum_{i\in R} Y_i}.
\]

For a probability sample from that frame:

\[
\widehat{\mathrm{Recovery}}_{R,w}
=
\frac{\sum_{i\in s_R} w_i Y_i A_i C_i}
     {\sum_{i\in s_R} w_i Y_i}.
\]

FDA defines sensitivity as \(TP/(TP+FN)\), but restricts accuracy terminology
to comparisons against an adequate reference standard; against a
non-reference method, the authority recommends agreement terminology instead
([FDA, 2007](https://www.fda.gov/regulatory-information/search-fda-guidance-documents/statistical-guidance-reporting-results-studies-evaluating-diagnostic-tests-guidance-industry-and-fda)).
Therefore, “independent-reference recovery” is the safer XIC label unless the
reference source and its claim boundary justify sensitivity/recall.

### Abstention and selective risk

For a defined eligible population:

\[
\widehat{\mathrm{Coverage}}
=
\frac{\sum_i w_iG_i}{\sum_i w_i},
\qquad
\widehat{\mathrm{SelectiveRisk}}
=
\frac{\sum_i w_iG_i\,\mathbf 1(\text{error}_i)}
     {\sum_i w_iG_i}.
\]

El-Yaniv and Wiener define coverage as \(E[g(X)]\) and risk as average loss over
accepted samples, explicitly making risk and coverage a trade-off
([JMLR, 2010](https://jmlr.csail.mit.edu/papers/volume11/el-yaniv10a/el-yaniv10a.pdf));
Chow's original reject-option paper likewise characterizes the error/reject
trade-off ([Chow, 1970](https://doi.org/10.1109/TIT.1970.1054406)).

For positive-reference cases, also retain:

\[
\widehat C_+
=
\frac{\sum_i w_iY_iG_i}{\sum_i w_iY_i},
\]

\[
\widehat{\mathrm{Recovery}}_{\text{decided},+}
=
\frac{\sum_i w_iY_iG_iA_iC_i}{\sum_i w_iY_iG_i},
\]

\[
\widehat{\mathrm{Recovery}}_{\text{automation},+}
=
\frac{\sum_i w_iY_iG_iA_iC_i}{\sum_i w_iY_i}
=
\widehat C_+\,
  \widehat{\mathrm{Recovery}}_{\text{decided},+}.
\]

The last identity makes visible a system that appears accurate only because it
holds difficult positives. FDA warns that discarding equivocal/nonbinary
outcomes and calculating only on the remainder can bias performance estimates
([FDA, 2007](https://www.fda.gov/regulatory-information/search-fda-guidance-documents/statistical-guidance-reporting-results-studies-evaluating-diagnostic-tests-guidance-industry-and-fda)).
Human `unassessable` is a separate oracle-sufficiency outcome \(Q_i\), not a
machine abstention \(G_i=0\).

## 1. Prevalence-Aware Precision And Recovery

### Observed authority

FDA gives the positive predictive value relationship

\[
\mathrm{PPV}(p)
=
\frac{p\,\mathrm{Se}}
       {p\,\mathrm{Se}+(1-p)(1-\mathrm{Sp})},
\]

where \(p\) is target-condition prevalence. Thus PPV/precision can change when
the population prevalence changes even if sensitivity and specificity are held
fixed
([FDA, 2004](https://www.fda.gov/media/71237/download)).
FDA also states that agreement changes with prevalence and that overall
agreement alone is insufficient; it recommends separately reported positive
and negative agreement with counts and confidence intervals
([FDA, 2007](https://www.fda.gov/regulatory-information/search-fda-guidance-documents/statistical-guidance-reporting-results-studies-evaluating-diagnostic-tests-guidance-industry-and-fda)).

Horvitz and Thompson established estimation from finite populations with
unequal inclusion probabilities
([Horvitz & Thompson, 1952](https://doi.org/10.1080/01621459.1952.10483446)).
Their design-based result is the authority for retaining \(\pi_i\) and using
inverse-probability weighted totals when challenge classes or strata are sampled
at different rates.

### Statistical inference

- Raw precision from a deliberately enriched challenge set estimates the
  challenge-set mixture, not the frozen representative population.
- Recovery conditional on \(Y=1\) is not algebraically driven by overall
  prevalence when sensitivity is constant. In this project, however, recovery
  can differ by hypothesis family, material, batch, signal regime, or method
  scope; therefore the aggregate recovery still depends on the positive-case
  stratum mixture.
- A proposal census describes one sealed tracer attempt over one bounded
  dataset. It does not by itself estimate future-batch precision.
- If the proposal and reference frames are sampled separately, their sampling
  fractions are design choices, not prevalence estimates. Concatenating them
  creates a case-control-like artificial prevalence.

### Contract-design options, not policy

The contract could require all of the following without choosing acceptance
thresholds:

1. report each stratum's numerator, denominator, point estimate, and interval;
2. report a frozen design-weighted aggregate using target stratum weights;
3. label an unweighted challenge-set result as challenge-conditioned;
4. label a proposal-census result as bounded-attempt precision;
5. prohibit a deployment-precision claim when target mixture or inclusion
   probabilities are unknown;
6. keep reference-source classes separate when they support different
   scientific claims, or state the rule by which they are combined.

## 2. Binomial Confidence Intervals

### Applicability gate

NIST's binomial treatment begins with \(n\) independent binary trials and
\(\hat p=x/n\)
([NIST TN 2119, 2020](https://doi.org/10.6028/NIST.TN.2119)).
That model is appropriate only when:

- the unit and success event are fixed before review;
- all \(n\) units are independent for the estimand;
- units are sampled with equal probability under the declared population
  model; a complete finite frame is instead a census of that frame;
- one common Bernoulli probability is a reasonable target;
- adaptive supplemental cases, calibration cases, and hidden duplicate
  presentations are excluded from the primary denominator.

### Wilson score interval

For \(x\) successes in \(n\) independent trials,
\(\hat p=x/n\), \(z=z_{1-\alpha/2}\):

\[
\mathrm{center}
=
\frac{\hat p+z^2/(2n)}{1+z^2/n},
\]

\[
\mathrm{halfwidth}
=
\frac{z}{1+z^2/n}
\sqrt{\frac{\hat p(1-\hat p)}{n}+\frac{z^2}{4n^2}},
\]

\[
CI_W=[\mathrm{center}-\mathrm{halfwidth},
      \mathrm{center}+\mathrm{halfwidth}].
\]

Wilson introduced the score construction
([Wilson, 1927](https://doi.org/10.1080/01621459.1927.10502953));
NIST publishes the interval and one-sided-bound forms and prefers Wilson over
the ordinary Wald interval in many small or boundary-rate settings
([NIST Engineering Statistics Handbook](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm);
[NIST TN 2119](https://doi.org/10.6028/NIST.TN.2119)).

For a one-sided lower gate, replace \(z_{1-\alpha/2}\) with \(z_{1-\alpha}\)
and retain the lower endpoint only. The gate must state whether its confidence
level is one-sided or two-sided; “95% CI” is not enough.

### Clopper-Pearson exact interval

Let \(B^{-1}(q;a,b)\) be the \(q\) quantile of a Beta distribution. The
equal-tailed interval is:

\[
L =
\begin{cases}
0, & x=0,\\
B^{-1}(\alpha/2;x,n-x+1), & x>0,
\end{cases}
\]

\[
U =
\begin{cases}
1, & x=n,\\
B^{-1}(1-\alpha/2;x+1,n-x), & x<n.
\end{cases}
\]

The construction comes from inverting binomial tail tests
([Clopper & Pearson, 1934](https://doi.org/10.1093/biomet/26.4.404));
NIST gives the same binomial-tail and Beta-quantile forms
([NIST TN 2119](https://doi.org/10.6028/NIST.TN.2119)).

### Recommended statistical role

- Two-sided Wilson intervals are suitable as the default descriptive interval
  for unweighted independent proportions.
- A preregistered one-sided score or exact lower bound is suitable when a gate
  asks whether a success probability exceeds a floor.
- Exact Clopper-Pearson bounds are defensible for small denominators or zero
  observed failures, but are conservative; the method should not be switched
  after seeing results.
- Always report \(x/n\) beside the interval. FDA explicitly recommends counts
  and percentages together with two-sided 95% confidence intervals for
  diagnostic accuracy/agreement reporting
  ([FDA, 2007](https://www.fda.gov/regulatory-information/search-fda-guidance-documents/statistical-guidance-reporting-results-studies-evaluating-diagnostic-tests-guidance-industry-and-fda)).

### When simple binomial intervals are invalid

Sample-cells sharing a hypothesis/family, batch, specimen, or acquisition may
be correlated. Liang and Zeger's generalized estimating equations were
developed for repeated/clustered outcomes and provide variance estimation
without treating within-cluster measurements as independent
([Liang & Zeger, 1986](https://doi.org/10.1093/biomet/73.1.13)).
For stratified or unequal-probability samples, Rao and Wu developed resampling
inference that preserves complex survey structure
([Rao & Wu, 1988](https://doi.org/10.1080/01621459.1988.10478591)).

Therefore:

- do not substitute row count for the number of independent hypotheses;
- use the actual sampling design and cluster IDs in variance estimation;
- if using resampling, resample independent clusters within frozen strata, not
  individual sample-cells;
- do not use an ordinary bootstrap over rows for unequal-probability samples;
- if there are too few independent clusters for reliable asymptotic or
  resampling inference, report the estimate as descriptive and mark precision
  insufficient.

If every unit in the bounded finite frame is reviewed, the result is a census of
that frame. A binomial sampling interval is not uncertainty about that same
finite census; reviewer error, reference error, and future-population
generalization remain separate uncertainties.

## 3. Sample-Size Planning

### Width-based planning

For a two-sided interval with desired approximate half-width \(d\), planning
value \(p^\*\), and confidence \(1-\alpha\):

\[
n_{\text{ind}}
\approx
\left\lceil
\frac{z_{1-\alpha/2}^2p^\*(1-p^\*)}{d^2}
\right\rceil.
\]

NIST derives this planning approximation and says \(p^\*\) is the best prior
estimate of the rate
([NIST TN 2119](https://doi.org/10.6028/NIST.TN.2119)).
Use \(p^\*=0.5\) only as a worst-case width choice when no calibration estimate
exists. Final planning near 0 or 1 should enumerate the chosen Wilson or exact
interval rather than rely only on the normal approximation.

### Prevalence-aware total workload

If \(n_+\) independent reference positives are required to estimate recovery,
and the probability that a randomly sampled eligible unit is reference-positive
is \(p_R\):

\[
N_{\text{eligible},R}\approx\frac{n_+}{p_R}.
\]

If \(n_P\) reviewed positive proposals are required to estimate
proposal-conditioned precision, and positive proposals occur at rate \(p_P\)
within an all-eligible sampling frame:

\[
N_{\text{eligible},P}\approx\frac{n_P}{p_P}.
\]

Sampling directly from sealed proposal and reference-positive frames avoids
paying these prevalence multipliers, but it no longer estimates target
prevalence. Buderer gives the analogous prevalence-aware sample-size method for
sensitivity and specificity, with required total sample size driven by desired
precision, expected performance, and condition prevalence
([Buderer, 1996](https://doi.org/10.1111/j.1553-2712.1996.tb03538.x)).

### Lower-bound acceptance planning

If a gate requires a lower confidence bound \(L(x,n)\) to exceed policy floor
\(p_0\), the operational planning algorithm is:

1. freeze interval method and one-/two-sided \(\alpha\);
2. choose an evidence-supported design value \(p_1>p_0\);
3. for each candidate \(n\), calculate the pass set
   \(\{x:L(x,n)>p_0\}\);
4. choose the smallest \(n\) such that
   \(P_{X\sim Bin(n,p_1)}[L(X,n)>p_0]\ge 1-\beta\);
5. inflate for clustering, unequal weights, invalid cases, and expected
   `unassessable` without changing the frozen primary denominator.

This separates confidence (\(\alpha\)) from probability of passing when the
design value is true (power \(1-\beta\)). A point-estimate calculation alone
does not do that.

### Zero-failure / zero-disagreement planning

With \(m\) independent trials and zero observed events, the one-sided exact
\((1-\alpha)\) upper bound for event rate \(d\) is:

\[
d_U=1-\alpha^{1/m}.
\]

To make that upper bound no greater than a proposed tolerance \(d_0\):

\[
m
\ge
\left\lceil\frac{\log(\alpha)}{\log(1-d_0)}\right\rceil.
\]

At one-sided 95% confidence, illustrative—not policy—minimums are:

| Proposed maximum event rate \(d_0\) | Zero-event independent trials needed |
| ---: | ---: |
| 10% | 29 |
| 5% | 59 |
| 2% | 149 |
| 1% | 299 |

The familiar approximate 95% upper bound \(3/m\) was presented for zero
numerators by Hanley and Lippman-Hand
([JAMA, 1983](https://doi.org/10.1001/jama.1983.03330370053031)).
The exact formula above should be retained in the contract calculator.

### Design-effect and feasibility limits

The planning \(n_{\text{ind}}\) is a count of independent units. Correlated
sample-cells require inflation or, preferably, direct power simulation under
the intended cluster design. Unequal weights can further reduce effective
information. At minimum, calibration must estimate or bound:

- cluster-size distribution;
- within-cluster correlation or between-cluster heterogeneity;
- stratum-specific performance;
- design-weight dispersion;
- invalid and `unassessable` rates.

No number of additional rows repairs reference bias or unknown inclusion
probabilities. FDA explicitly notes that increasing sample size does not reduce
systematic bias
([FDA, 2007](https://www.fda.gov/regulatory-information/search-fda-guidance-documents/statistical-guidance-reporting-results-studies-evaluating-diagnostic-tests-guidance-industry-and-fda)).

## 4. Human Reviewer Intra-Rater Repeatability And Hidden Duplicates

### Observed authority

NIST/VIM defines repeatability as agreement of successive measurements under
the same procedure, observer, instrument, location, conditions, and short time;
reproducibility changes one or more of those conditions
([NIST TN 1297](https://www.nist.gov/pml/nist-technical-note-1297/nist-tn-1297-appendix-d1-terminology)).
Thus one reviewer re-reading hidden duplicates supports an intra-rater
repeatability claim only.

Cohen introduced chance-corrected agreement for nominal categories
([Cohen, 1960](https://doi.org/10.1177/001316446002000104)).
For \(m\) distinct hidden-duplicate case pairs and transition counts \(n_{ab}\)
from first-pass category \(a\) to repeat category \(b\):

\[
P_o=\frac{\sum_a n_{aa}}m,\qquad
P_e=\sum_a\frac{n_{a\cdot}}m\frac{n_{\cdot a}}m,\qquad
\kappa=\frac{P_o-P_e}{1-P_e}.
\]

FDA cautions that agreement, including kappa, is not correctness and changes
with prevalence
([FDA, 2007](https://www.fda.gov/regulatory-information/search-fda-guidance-documents/statistical-guidance-reporting-results-studies-evaluating-diagnostic-tests-guidance-industry-and-fda)).
Donner and Eliasziw show that kappa confidence intervals and sample size for
binary agreement depend on the agreement model, not merely on a desired count
([Donner & Eliasziw, 1992](https://doi.org/10.1002/sim.4780111109)).

### Minimum repeatability outputs

The packet should preserve, before adjudication:

- number of distinct duplicated `case_id`s \(m\);
- complete first-pass × repeat-pass transition table \(n_{ab}\);
- exact presentation order and elapsed time;
- raw agreement \(P_o\) and disagreement \(1-P_o\), with a binomial interval
  only when duplicate case IDs are independently sampled;
- category-specific repeat agreement or transition rates;
- kappa with interval when estimable;
- duplicate sampling stratum and inclusion probability;
- number and reason of duplicate-triggered adjudications;
- separate results for identity, observation state, projection, apex/boundary,
  and any other independently judged dimension.

No universal kappa adjective or cutoff is supplied by the authorities above.
The XIC owner would need to select a tolerance based on the consequence of label
instability and observed calibration marginals.

### Hidden-duplicate design implications

- The primary presentation and repeat presentation share one scientific unit.
  Counting both in precision or recovery is pseudoreplication.
- Repeatability must use the two sealed pre-adjudication judgments. Replacing
  either with a consensus/adjudicated value erases the disagreement being
  measured.
- Hidden duplicates selected only from ambiguous cases estimate repeatability
  for ambiguous cases. A representative overall estimate needs probability
  sampling across the declared primary-case strata, with design weights if
  allocation is disproportionate.
- A very small duplicate fraction may detect gross inconsistency but cannot
  establish a tight upper bound. The zero-disagreement table above shows the
  scale: zero disagreements among ten pairs still leaves a one-sided 95% exact
  upper bound of about 26%.
- A second reviewer, if later added, creates an inter-rater estimand with a
  separate pairing and consensus protocol; it must not be pooled silently with
  intra-rater duplicates.

## 5. Abstention, Coverage, And `unassessable`

### Observed authority

Selective-classification methodology treats abstention as an explicit selection
function and evaluates risk jointly with coverage
([El-Yaniv & Wiener, 2010](https://jmlr.csail.mit.edu/papers/volume11/el-yaniv10a/el-yaniv10a.pdf)).
FDA says ignoring equivocal outcomes can bias performance estimates and
recommends explicit handling
([FDA, 2007](https://www.fda.gov/regulatory-information/search-fda-guidance-documents/statistical-guidance-reporting-results-studies-evaluating-diagnostic-tests-guidance-industry-and-fda)).

### Contract-design options, not policy

For every population/stratum, the packet could report:

1. eligible primary units;
2. machine decisions, machine holds, human assessable cases, and human
   `unassessable` cases as separate counts;
3. overall and class-conditional automated coverage;
4. error, precision, and recovery among automated decisions;
5. unconditional automated recovery with positive holds left in the
   denominator;
6. final human-assisted outcome as a separate workflow estimand;
7. intervals calculated from the same frozen denominator definitions.

A coverage floor alone is insufficient: automation could satisfy it while
concentrating holds in one scientifically important stratum. A conditional
error bound alone is also insufficient: it can be improved mechanically by
abstaining more. The pair, plus class/stratum views, exposes the trade-off
without dictating its acceptable location.

Human `unassessable` should remain in packet-sufficiency accounting. It may be
excluded from a scientific numerator/denominator only by a preregistered
estimand rule, with its fraction and reasons reported. Post-result removal is
not missing-data handling.

## 6. Incomplete Ground Truth And Reference-Universe Risk

### Observed authority

FDA defines a reference standard as the best available method for establishing
presence/absence independently of the test under evaluation. It warns about
reference error, incorporation of the evaluated test into the reference, and
verification/work-up bias; if no adequate reference can be constructed,
correctness cannot be estimated directly and agreement terminology is required
([FDA, 2007](https://www.fda.gov/regulatory-information/search-fda-guidance-documents/statistical-guidance-reporting-results-studies-evaluating-diagnostic-tests-guidance-industry-and-fda)).

Begg and Greenes showed that restricting analysis to cases receiving definitive
verification can seriously bias accuracy estimates, and adjustment requires
assumptions about the verification-selection mechanism
([Begg & Greenes, 1983](https://pubmed.ncbi.nlm.nih.gov/6871349/)).
Hui and Walter showed that two imperfect tests can be estimated without a gold
standard only under a stronger design: both tests in two populations with
different prevalences, stable test error rates, and conditional independence
([Hui & Walter, 1980](https://doi.org/10.2307/2530508)).

### What is identifiable from common packet shapes

| Available evidence | Identifiable claim | Not identifiable without more evidence/assumptions |
| --- | --- | --- |
| All sealed positive proposals reviewed | Bounded-attempt proposal precision relative to the frozen oracle | Dataset false-positive rate; missed positives; future-batch precision |
| Probability sample of proposals with known \(\pi_i\) | Design-weighted proposal precision for that proposal frame | Precision for an unspecified deployment mixture |
| Exhaustive independent positive reference frame | Recovery over that declared positive frame | Positives omitted by the reference construction |
| Probability sample from a complete positive frame | Design-weighted recovery over that frame | Recovery when frame undercoverage is unknown |
| Tracer-conditioned review only | Agreement/correctness among reviewed tracer cases | Full recovery; nonproposal false negatives |
| One imperfect reviewer and one tracer in one population | Their observed 2×2 association/agreement | Both parties' true sensitivity/specificity and latent prevalence |

The last row is underidentified by simple parameter counting: one 2×2 table has
three independent observed probabilities, while latent prevalence plus
sensitivity and specificity for two imperfect methods gives five unknowns.
Hui-Walter becomes just-identified only after adding a second population and
its assumptions. This is a model-based option, not ground truth recovered for
free.

### Worst-case bounds when the finite universe is enumerated but partly verified

Suppose every eligible unit is enumerated and proposal status is known, but
reference status is verified only for a subset. Let:

- \(a\): verified positives correctly recovered;
- \(b\): verified positives not recovered;
- \(u_1\): unverified proposed/recovered units;
- \(u_0\): unverified nonproposed units.

Without assumptions about unverified labels, true recovery lies no tighter than:

\[
\mathrm{Recovery}_{\min}
=
\frac{a}{a+b+u_0},
\qquad
\mathrm{Recovery}_{\max}
=
\frac{a+u_1}{a+b+u_1}.
\]

The lower bound treats all unverified nonproposals as positives and all
unverified proposals as negatives; the upper bound does the reverse. If the
eligible universe itself is not enumerated, \(u_0\) is unknown and even this
finite bound is unavailable; the lower bound can approach zero. These are
logical partial-identification bounds, not confidence intervals.

### Reference-universe failure modes

1. **Frame undercoverage:** an independent list of known positives can be
   perfectly reviewed and still omit unknown positives. Recovery is then
   “known-reference recovery,” not complete scientific recall.
2. **Incorporation bias:** if tracer output, scores, or discrepancies influence
   reference construction, the reference is no longer independent. FDA
   explicitly warns against evaluated-test incorporation and discrepant
   resolution
   ([FDA, 2007](https://www.fda.gov/regulatory-information/search-fda-guidance-documents/statistical-guidance-reporting-results-studies-evaluating-diagnostic-tests-guidance-industry-and-fda)).
3. **Verification bias:** if only high-score, proposed, or suspicious cases
   receive full review, complete-case accuracy is biased unless selection
   probabilities/mechanisms are modeled
   ([Begg & Greenes, 1983](https://pubmed.ncbi.nlm.nih.gov/6871349/)).
4. **Reference misclassification:** confidence intervals quantify sampling
   variability conditional on labels; they do not correct a wrong oracle.
5. **Artificial prevalence:** separately enriched proposal and
   reference-positive samples do not identify prevalence and must not be used
   for prevalence-dependent PPV without external target-mixture data.
6. **No negative frame:** a positive-only reference universe cannot identify
   specificity or dataset false-positive rate \(FP/(FP+TN)\).
7. **Dependent imperfect methods:** latent-class estimates depend on assumptions
   such as conditional independence and stable error rates; violations can
   change the answer while fitting observed agreement.

## 7. Multiplicity And Gate Semantics

FDA distinguishes co-primary endpoints, where every endpoint must pass, from
multiple opportunities for success. Requiring all co-primary endpoints to pass
does not create the same Type I multiplicity problem, although it reduces power;
allowing success on any one of several endpoints does require multiplicity
control
([FDA Multiple Endpoints Guidance, 2022](https://www.fda.gov/media/162416/download)).

Applied as a statistical inference:

- a GO rule requiring every prespecified gate to pass is an
  intersection-union structure; each component still needs its declared
  confidence method and adequate power;
- a NARROW rule that may choose any successful stratum after results are seen
  creates multiple paths to a positive claim unless the candidate strata and
  gatekeeping rule are preregistered;
- descriptive failure taxonomy does not need multiplicity adjustment if it is
  not promoted into an additional confirmatory claim.

This note does not select alpha allocation or gate order.

## 8. Minimum Data Required By A Statistical Contract

### Estimand and universe

- `estimand_id`, claim wording, evaluation unit, target population, time/batch
  scope, policy, method scope, and success event;
- complete frame identity and size by stratum, or an explicit statement that
  the frame is incomplete;
- proposal-frame and reference-frame membership kept as separate flags;
- reference source/type and the claim strength it supports;
- frozen inclusion/exclusion and handling of invalid, missing, held, and
  `unassessable` units.

### Sampling design

- census versus probability sample versus nonprobability/challenge sample;
- stratum ID and frame size \(N_h\);
- first-order inclusion probability \(\pi_i>0\) and design weight \(w_i\);
- selection algorithm, seed, sampling wave, and adaptive-supplemental flag;
- independent cluster IDs and all known dependency links;
- primary presentation flag and `duplicate_of_case_id`.

Without a nonzero known inclusion probability, design-weighted population
estimation is unavailable; Horvitz-Thompson estimation depends on those
probabilities
([Horvitz & Thompson, 1952](https://doi.org/10.1080/01621459.1952.10483446)).

### Frozen outcomes

- sealed tracer proposal/action and machine hold;
- first-pass blinded human labels before reveal;
- `assessable`/`unassessable` and reason;
- reference label and source;
- correctness components rather than one global label;
- hidden-duplicate repeat label before adjudication;
- append-only correction/adjudication event without overwriting original
  labels.

### Planning inputs

- policy-selected metric floor or desired interval width;
- one-/two-sided confidence level and interval method;
- design success value \(p_1\), alpha, and desired power \(1-\beta\);
- calibration estimates/ranges for proposal rate, reference-positive
  prevalence, stratum performance, and `unassessable` rate;
- cluster-size distribution/correlation and design-weight dispersion;
- maximum review budget and invalid-case contingency;
- number and definition of confirmatory gates and any multiplicity/gatekeeping
  rule.

## 9. Preregisterable Statistical Decision Skeleton

The following is an implementation-neutral skeleton, not a completed product
policy:

```text
for each required estimand:
    verify frame, unit, source, sampling design, weights, and cluster IDs
    if required inputs are missing:
        result = inconclusive
    else:
        compute frozen point estimate
        compute design-appropriate uncertainty
        compare the preregistered bound (not only point estimate) with threshold

verify hidden-duplicate repeatability separately
verify coverage and selective risk jointly
verify human unassessable rate separately
apply preregistered all-gates / gatekeeping logic
never use adaptive supplemental cases to revise frozen representative estimates
```

The bound method must dispatch by design:

| Design | Suitable uncertainty route |
| --- | --- |
| Independent equal-probability Bernoulli units | Wilson or exact binomial |
| Complete bounded finite census | Descriptive census result; no sampling CI for that same frame |
| Stratified/unequal-probability probability sample | Design-based ratio variance or design-preserving resampling |
| Clustered sample-cells | Cluster-robust model or cluster-level resampling |
| Nonprobability challenge set | Descriptive challenge-conditioned result |
| Incomplete/unknown reference frame | Bounded claim, partial-identification bounds, or `inconclusive` |

## 10. Remaining Policy Choices For The XIC Owner

The sources do not determine:

- acceptable precision, recovery, abstention, `unassessable`, or repeatability
  thresholds;
- whether gates use one-sided 95%, two-sided 95%, or another confidence level;
- the target deployment mixture and stratum weights;
- which reference-source classes may be combined;
- acceptable uncertainty width and power;
- the hierarchy of hypothesis/family, batch, sample-cell, and policy claims;
- whether GO is strictly all-gates and which NARROW paths are confirmatory;
- the cost trade-off between false acceptance, missed recovery, abstention, and
  human review.

Those are product/scientific decisions. The statistical contract should require
them to be explicit and frozen before formal holdout labels or machine mappings
are opened.

## Primary And Authoritative Source Index

- FDA. *Statistical Guidance on Reporting Results from Studies Evaluating
  Diagnostic Tests* (2007): reference standards, verification/incorporation
  bias, agreement terminology, equivocal outcomes, counts and confidence
  intervals.
  [Official guidance](https://www.fda.gov/regulatory-information/search-fda-guidance-documents/statistical-guidance-reporting-results-studies-evaluating-diagnostic-tests-guidance-industry-and-fda)
- FDA. *Developing Medical Imaging Drug and Biological Products, Part 3*
  (2004): \(TP/(TP+FP)\) and prevalence-based PPV formula.
  [Official PDF](https://www.fda.gov/media/71237/download)
- NIST. Lu, *Estimating Instrument Performance: with Confidence Intervals and
  Confidence Bounds*, TN 2119 (2020): binomial assumptions, Wilson/exact bounds,
  and sample-size planning.
  [Official publication](https://doi.org/10.6028/NIST.TN.2119)
- Wilson (1927): score interval.
  [Original paper](https://doi.org/10.1080/01621459.1927.10502953)
- Clopper & Pearson (1934): exact binomial interval.
  [Original paper](https://doi.org/10.1093/biomet/26.4.404)
- Buderer (1996): prevalence-aware sample size for sensitivity/specificity.
  [Original methodology paper](https://doi.org/10.1111/j.1553-2712.1996.tb03538.x)
- Horvitz & Thompson (1952): unequal-probability finite-population sampling.
  [Original methodology paper](https://doi.org/10.1080/01621459.1952.10483446)
- Rao & Wu (1988): design-preserving resampling for complex surveys.
  [Original methodology paper](https://doi.org/10.1080/01621459.1988.10478591)
- Liang & Zeger (1986): correlated/repeated outcome inference.
  [Original methodology paper](https://doi.org/10.1093/biomet/73.1.13)
- NIST TN 1297 / VIM terminology: repeatability versus reproducibility.
  [Official terminology](https://www.nist.gov/pml/nist-technical-note-1297/nist-tn-1297-appendix-d1-terminology)
- Cohen (1960): nominal-category kappa.
  [Original methodology paper](https://doi.org/10.1177/001316446002000104)
- Donner & Eliasziw (1992): kappa CI and sample-size inference.
  [Original methodology paper](https://doi.org/10.1002/sim.4780111109)
- Hanley & Lippman-Hand (1983): zero-event “rule of three.”
  [Original methodology paper](https://doi.org/10.1001/jama.1983.03330370053031)
- Chow (1970): optimal error/reject trade-off.
  [Original methodology paper](https://doi.org/10.1109/TIT.1970.1054406)
- El-Yaniv & Wiener (2010): selective-classification coverage and risk.
  [Original methodology paper](https://jmlr.csail.mit.edu/papers/volume11/el-yaniv10a/el-yaniv10a.pdf)
- Begg & Greenes (1983): verification-selection bias.
  [Original methodology paper](https://pubmed.ncbi.nlm.nih.gov/6871349/)
- Hui & Walter (1980): identifiable latent-class design under explicit
  assumptions.
  [Original methodology paper](https://doi.org/10.2307/2530508)
- FDA. *Multiple Endpoints in Clinical Trials* (2022): all-required versus
  any-success gate multiplicity.
  [Official guidance](https://www.fda.gov/media/162416/download)

---
status: accepted
---

# Share one scientific lifecycle across Targeted and Untargeted

Targeted and Untargeted use one scientific capture, hypothesis, cross-sample,
trajectory-refinement, observation-repair, and cell-decision lifecycle.
Targeted is a constrained specialization: its exact Target list limits which
candidates may become Target subjects, while Untargeted projects the configured
Discovery universe. They retain separate SearchScope variants, row identity,
projection policy, public schemas, and writer authority because those are
application contracts rather than separate fishing methods.

## Considered options

- Keep Targeted and Untargeted as independent pipelines. Rejected because it
  preserves duplicated scientific owners and lets their behavior drift.
- Make Targeted reuse the full Untargeted output model. Rejected because a
  shared scientific lifecycle does not make their row, schema, review, or
  delivery contracts identical.
- Share the lifecycle behind explicit application scopes and projections.
  Accepted because broad candidate discovery is useful to both applications,
  while only owner-issued Target definitions may enter Target projection.

## Consequences

- `TargetSearchScope` and `UntargetedSearchScope` select candidate universes and
  evidence-provider constraints; they do not own observations, groups, values,
  artifacts, or writer authority.
- Both applications must reach the same capture and `PeakHypothesis`
  implementation. A generic `core/`, second hypothesis model, or hidden
  application-specific fallback is not an acceptable migration seam.
- Structural migration preserves current scientific output. Target local-fill,
  RT/QC policy, and other behavior promotions require separate expected-diff
  authority.

Current contracts: `docs/architecture-contract.md` and
`docs/product/domain-glossary.md` (Product Shape). Active delivery map: GitHub #301;
initial execution frontier: #316.

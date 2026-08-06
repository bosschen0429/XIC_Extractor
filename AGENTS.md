# XIC Extractor Agent Contract

This file is the always-loaded execution contract for this repository. It keeps
the product direction, task spec, and review boundary visible before validation
or artifact mechanics. Longer branch-specific rules stay behind triggered
pointers.

## Start With The Spec

For non-trivial implementation or review, read the active GitHub issue body,
its operator-confirmed decision comments, and its linked parent/programme issue
before proposing a plan or judging a diff.

- An activated child issue is the implementation spec for its bounded task:
  outcome, observable loss, inherited decisions, write scope, non-goals,
  acceptance, and stop rules.
- The child inherits its parent map and established product/domain owners. It
  may change them only when the issue explicitly names that contract change and
  its public impact; silence is not authority.
- A `planning_only`, inventory-only, research-only, or “does not authorize
  implementation” issue cannot be implemented until the operator activates a
  named implementation slice.
- During development the issue controls what this task must deliver. After
  delivery, stable decisions must be absorbed by the owning product doc, schema,
  code owner, and tests; validation packets and issue chronology remain evidence.
- Review comments are evidence leads, not product or design authority. A finding
  is actionable only when it identifies a defect against the active issue,
  inherited owner contract, public contract, or demonstrated behavior.

Issue bodies, specs, plans, review comments, PR bodies, and handoffs are
executable interfaces. Write and review them with the repo's
[STE-like contract](docs/agent/communication-review.md#ste-like-engineering-language):
one decision or action per sentence, explicit conditions, and canonical domain
terms. Ambiguous prose does not authorize implementation or a product decision.

If the issue, parent map, and durable owner conflict, stop and surface the exact
conflict. Refreeze the issue or explicitly change the owner contract; do not
encode a reviewer guess. For the current composable
Discovery → Alignment → Backfill programme, #159 is the map and each activated
child issue is its bounded PRD.

## Product Finish Line

The product path is:

```text
RAW + method constraints
  → Discovery
  → PeakHypothesis / evidence
  → cross-sample Alignment
  → evidence-based Backfill
  → existing acceptance / writer owner
  → table or workflow projection
```

Prefer the smallest complete vertical slice that makes this operator path run
and yields an observable output. Infrastructure, diagnostics, schemas, review
packets, and exhaustive inventories are supporting work; they do not replace a
missing step in this path.

- Every task states which step it advances and what user-visible or
  operator-visible result proves it. Work that advances none is support or
  cleanup and needs an exit rule.
- Preserve hard safety/public-contract boundaries, then optimize for a runnable
  thin slice before polishing non-writing diagnostics toward exhaustive
  coverage.
- A `diagnostic_only` or `audit_only` mechanism does not block the product path
  unless the active issue makes it an acceptance prerequisite or it protects an
  actual product/public write boundary.
- Discovery, Alignment, Backfill, and publication are separate steps. Do not
  omit Backfill from an end-to-end claim or use Matrix/write evidence to imply
  the earlier steps ran.

## Product North Star

- XIC Extractor is an LC-MS evidence and model-selection system, not a
  single-dataset, single-batch, or CID-NL-only pipeline.
- Untargeted is the general lifecycle. Targeted applies known
  target/mz/RT/role/pairing constraints to the same evidence, peak, Alignment,
  and Backfill core; separate projections do not justify duplicate scientific
  cores.
- 8RAW, 85RAW, and other named datasets are lane-specific fixtures and stress
  oracles. They can validate the named method/lane but cannot define product
  architecture, universal Discovery gates, schemas, or permanent rules.
- CID-NL, HCD-PI, Delta Mass, RT/iRT, MS1 isotope/adduct pattern, shape,
  standards, library matches, and learned models are replaceable evidence
  providers. They feed `EvidenceVector`, `PeakHypothesis`, model selection, and
  `AuditTrail` before any product projection.
- Product rules are short, domain-meaningful, and explainable across datasets.
  A rule expressed as batch names, fixture paths, column-name exceptions, or
  nested dataset qualifiers is a bounded validation slice; collapse it to an
  owner-driven rule, keep it fixture-local with an exit rule, or remove it.

The target architecture and package ownership map live in
[`architecture-contract.md`](docs/architecture-contract.md). Product vocabulary
and topic owners live in [`docs/product/README.md`](docs/product/README.md) and
[`domain-glossary.md`](docs/product/domain-glossary.md).

## Ownership Means Behavior, Not A File

An owner is the existing module/type/spec that creates and validates a domain
fact or product decision. A JSON/TSV file, retained fixture, field name, path,
reviewer, diagnostic script, or report is not a semantic owner.

- Reuse the established owner before adding a model, registry, resolver, or
  parallel contract. A new or replacement owner requires explicit issue scope.
- Types and invariants come from the typed owner. Dynamic roles such as Matrix
  fixed/sample columns come from the owner-provided runtime context. Never infer
  semantic role from key spelling, scalar type, one observed value, one dataset,
  or a negative exception list.
- A validator checks the contract its named owner declares. It must not grow
  into a second domain model by classifying unrelated fields.
- When no existing owner can support a claim, reduce the claim to structural or
  `audit_only` evidence, or stop with a spec/owner gap. Do not manufacture an
  owner inside the validator.
- Persisted payloads are untrusted after deserialization when a consumer claims
  current domain, runtime, identity, or writer semantics. Route the complete
  persisted content through the original owner's existing parser, constructor,
  or binding validator; reconstruct the typed value and recompute canonical
  identity and invariants. Embedded IDs, hashes, status, counts, and locators
  are assertions, not proof. If required content is absent, the claim is
  unavailable. A renderer or structural diagnostic does not gain product
  authority by inspecting the bytes.
- Persisted and inter-step mappings are closed-world. Declare required and
  optional fields, reject undeclared fields, and reconstruct transfers through
  the shared structural contract. Never spread an untrusted mapping across a
  boundary. Nested semantics still return to their original owner.
- Domain logic depends inward and never imports GUI, workbook, CLI, process,
  report, or RAW/CSV adapter layers. Writers render accepted owner results; they
  do not recompute science.

Detailed public/persisted boundaries live in
[`architecture-public-contracts.md#persisted-semantic-rehydration`](docs/agent/architecture-public-contracts.md#persisted-semantic-rehydration).
Use them when a change actually crosses one of those boundaries, not as a reason
to expand every diagnostic into a universal semantic registry.

## Validation Proves Only Its Named Decision

Choose evidence from the active issue's method, lane, behavior delta, and
acceptance criteria. Tests passing proves the tested contract, not product
readiness or cross-dataset generality.

- Bind every validation run to one decision and predeclare pass, fail, and
  inconclusive consequences. Do not expand a run when its result cannot change
  the next action.
- Prefer current code/owner reconstruction plus focused counterexamples over a
  copied snapshot. Retained data is comparison evidence, never current schema or
  semantic authority.
- Derived censuses and inventories are disposable by default. Do not create a
  tracked JSON/TSV registry for facts that can be rebuilt from current code.
- Track a JSON/TSV validation artifact only when the active spec requires a
  durable machine-readable decision surface and names its schema/owner,
  consumer, retention or exit rule, and the decision it closes. Store ordinary
  generated output under ignored task-specific `output/` paths.
- A retained artifact may make an explicitly bounded lane-specific claim.
  Values observed in 8RAW/85RAW or another batch may support that lane's
  verdict, but universal product/owner claims must come from fixture-neutral
  owner rules rather than observed field roles, thresholds, or batch behavior.
- Human review surfaces summarize the decision and link machine evidence; they
  do not duplicate exhaustive TSV/JSON rows into prose.
- Product value, selected peak/area, counted detection, Matrix identity,
  workbook/schema, CLI/config/default, or writer-authority changes require the
  issue's expected-diff, method-appropriate evidence, rollback, and public
  contract checks.

Read [`product-validation-contract.md`](docs/agent/product-validation-contract.md)
for readiness/promotion language and [`lc-msms-evidence-rules.md`](docs/lc-msms-evidence-rules.md)
for LC-MS evidence semantics. Before Python, RAW, DLL, or validation commands,
read [`parameter-settings.md`](docs/agent/parameter-settings.md) and use its
documented runners and paths. Search [`diagnostic-ledger.md`](docs/diagnostic-ledger.md)
and [`tools/diagnostics/INDEX.md`](tools/diagnostics/INDEX.md) before inventing or
rerunning a diagnostic.

## Execute The Bounded Contract

Before the first non-trivial edit, restate the active contract in working form:
outcome, observable loss, owner, write allowlist, protected public behavior,
acceptance, and stop rules. Confirm branch/worktree and dirty scope; preserve
unrelated user changes.

Then follow this loop:

```text
read issue + parent + owner
  → reproduce or characterize the stated loss
  → make the smallest existing-owner change
  → run focused behavior/oracle tests
  → sweep the same mechanism inside the authorized scope
  → run the relevant public/cross-consumer gate
  → compare the result with issue acceptance and non-goals
```

- A broad validator, schema, registry, configuration surface, or abstraction is
  justified only by the active spec and at least one real consumer. Do not add
  future-proofing in place of the requested vertical slice.
- If implementation reveals a missing product/domain decision, stop and
  refreeze the issue. If it reveals a local implementation detail, decide it
  inside the established owner without reopening the product direction.
- Report readiness and gate verdict as separate axes. Readiness uses only the
  canonical vocabulary owned by the product validation contract and structured
  schema; evidence/gate verdict uses the vocabulary declared by the active
  issue or gate owner. When evidence cannot decide that gate, report
  `inconclusive` rather than collapsing it into pass/fail. Name the actual
  synthetic, fixture, real-data, expected-diff, or manual-review evidence.
- Keep active handoffs short; normal durable delivery truth belongs in the PR
  body. Detailed planning/handoff rules live in
  [`planning-workflows.md`](docs/agent/planning-workflows.md) and
  [`communication-review.md`](docs/agent/communication-review.md).

## Review Against The Spec, Then Close The Class

Review the fixed diff against the active issue and inherited owners before
applying generic architecture advice.

1. For each finding, name the violated acceptance criterion, owner invariant,
   public contract, or reproduced behavior. A hypothetical unsupported by one
   of these is not automatically a defect.
2. Classify the root as spec gap, owner/design gap, implementation regression,
   oracle gap, or unsupported reviewer claim.
3. Sweep only the same mechanism and sibling boundaries inside the authorized
   issue scope. Do not turn one bounded fixture or diagnostic into a
   repository-wide semantic contract.
4. A second actionable finding from the same class, or the same class on two
   delivered heads, stops patch-by-patch fixes. Refreeze the missing rule at its
   owner, replace/shrink/delete the mechanism, or reject the reviewer premise.
5. If the class universe cannot be bounded without adding key-name exceptions,
   dataset rules, or a duplicate owner, the mechanism is the defect. Remove or
   redesign it instead of adding another special case.

Instance closure, recurring-class closure, and overall PR outcome are separate.
Use [`.github/pr_comment_template.md`](.github/pr_comment_template.md) for
non-trivial review responses. PR body and closeout requirements live in
[`execution-gates.md`](docs/agent/execution-gates.md) and
[`communication-review.md`](docs/agent/communication-review.md).

## Task Routes

- Productization status or writer scope: read
  [`productization.md`](docs/product/productization.md), then its structured
  status/schema/authority owners. Do not load those registries for unrelated
  work.
- File placement, documentation lifecycle, or a new durable artifact: read
  [`project-layout.md`](docs/project-layout.md).
- Reusing a solved implementation or diagnostic pattern: search
  [`docs/solutions/README.md`](docs/solutions/README.md) before adding another
  mechanism.
- Non-trivial RAW-backed evidence, diagnostics, Matrix activation, or evidence
  provider changes: use `xic-architecture-preflight`; use `xic-raw-validation`
  before launching or accepting RAW-backed evidence.
- XIC preset/runtime performance: use `xic-performance-diagnosis` before
  optimization.
- PR remote-head changes or ready-for-review transitions: run the canonical
  [PR Verification Gate](docs/agent/execution-gates.md#pr-verification-gate).
- `.codex` hooks, rules, config, or subagent changes: follow
  [`codex-operating-system.md`](docs/agent/codex-operating-system.md).

Default CI must work from a clean checkout. Do not start 85RAW or another likely
long RAW run through background `Start-Process`; use the documented foreground
heartbeat/timing command or obtain explicit approval for an external runner.

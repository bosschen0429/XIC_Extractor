# Planning And Workflows

This file owns planning discipline, reusable workflow routing, owner migration
policy, and the issue-to-PR contract shared by `.github/ISSUE_TEMPLATE/` and
`.github/pull_request_template.md`. Stable runners and validation command
shapes remain in `docs/agent/parameter-settings.md`.

## Planning And Review

- Before phase plans or expensive validation, name the decision the run can
  close, strongest existing oracle, missing independent evidence, expected
  runtime/artifacts, and fail-fast or inconclusive path.
- Use evidence tools proactively. Pick the structural-context, external-docs,
  or real-data validation path that best closes the decision; do not choose a
  weaker path only to save tokens or cost.
- For long tool chains, write the decision map first: question, evidence source,
  action if pass, action if fail. This prevents blind loops without discouraging
  tool use.
- Any `audit_only`, `shadow_only`, or `diagnostic_only` path needs an exit rule:
  promote, kill, externalize, or name the single missing evidence.
- Do not expand a specific validation run when its result cannot change the next
  action; use a different tool or stop with the blocker instead.
- Use a goal-shaped contract for long-running, multi-step, cross-turn, or
  repeatedly drifting work. Goal contracts must point to canonical local
  surfaces, active product docs/plans/schemas, and existing diagnostics or validation outputs.
- Non-trivial specs, plans, docs, workflow rules, and implementations need a
  critical-thinking review angle: strongest assumption, stale-artifact risk,
  cheaper existing oracle, or condition that would invalidate the path.

## Task Shaping And Premise Compilation

Every task follows the same delivery loop. Only non-trivial tasks need an
explicit artifact, and its depth scales with risk:

1. **Shape the goal** — name the intended outcome and user value, constraints,
   and the premise most likely to change the direction.
2. **Explore current reality** — inspect the current owner, implementation,
   real entry, tests, and strongest evidence; separate contract, observation,
   hypothesis, and direction-changing unknowns.
3. **Bound the task** — define in/out, preserved invariants, expected diff,
   acceptance evidence, and stop rule so implementation and review share the
   same finish line.
4. **Execute the smallest coherent slice** through the real product surface.
5. **Verify and review** representative output and failure semantics, then
   report observed facts, inference, and remaining unknowns separately.

This is an iterative loop, not a promise of one-pass delivery. New evidence may
reopen shaping or boundaries; when it does, update the canonical task shape and
invalidate displaced assumptions before execution resumes.

An established-owner fix with a fixed oracle may express this shape in the
request, a few sentences, and a focused test. Multi-step work keeps a short
task shape in its issue or plan. Escalate to a **Current Packet** only when
current evidence shows that an unknown can change ownership, public surface,
material cost, or acceptance; the work crosses a semantic/runtime boundary,
uses expensive real-data validation, transforms a legacy owner, follows a
load-bearing premise correction, has an unbounded production transaction, or
repeats a refreeze or rework class. A merely possible ambiguity is not current
evidence. `needs-info` and legacy work are escalation signals, not the scope of
the delivery loop.

### Current Packet Escalation

The canonical task surface carries the single authority pointer stub for the
**Current Packet**: packet ID, revision, base SHA, status, and exact location.
For issue-backed work this stub stays in the issue body; otherwise it stays in
the active plan, goal, or handoff. The packet may live there, in one canonical
comment, or in a linked tracked spec. Every replacement is self-contained and
lists all superseded packet IDs; partial supersession such as "replace one
section and inherit the rest" is invalid. Earlier versions remain history and
do not remain active acceptance text after a refreeze. The packet contains four
compiled blocks:

1. **Premise ledger** — list each decision-bearing statement, its authority,
   state, and what it invalidates. Valid states are `contract`, `observed`,
   `hypothesis`, and `rejected`. Current legacy behavior is evidence, not a
   frozen invariant, unless operator intent or a product/architecture contract
   grants it that status.
2. **Asset disposition** — classify every in-scope legacy capability, test,
   helper, and public surface as `keep`, `reuse_after_cleanup`,
   `compatibility_only`, `replace_or_retire`, or `unknown`. Name its successor
   owner and oracle; do not infer either wholesale preservation or wholesale
   rejection.
3. **Transaction compilation** — map each requirement through the real entry
   point, semantic owner, and every live consumer reachable from the in-scope
   production entries and exported surfaces, including writer/renderer/cache/
   reuse paths and downstream readers. Record the census command/query, base
   SHA, exclusions, call cost, counterexample, and acceptance evidence. Where
   dynamic dispatch, call count, or repeated work matters, execute the highest
   production entry with each named real consumer present. List every
   monkeypatch, no-op, or substitution; it cannot prove completeness for the
   path it replaces. A direct helper test does not prove that the CLI/main
   transaction has the same behavior. Bound the claim to in-repo reachability
   plus known external contracts rather than an unknowable external universe.
4. **Acceptance and stop** — record preserved invariants, expected product diff,
   non-goals, focused and representative verification, expected delivery cost,
   and the fail-fast condition. One consolidated review plus one root-class
   corrective is a convergence target, not a hard cap or permission to skip a
   final review.

If a premise correction changes ownership, scope, oracle, compatibility, or
material cost, stop mutation and trace every dependent scope row, test, budget,
and compatibility assumption. Publish a clean replacement Current Packet
before resuming; do not append another exception while leaving invalidated text
active.
An `unknown` may remain only when its resolution cannot change ownership, public
surface, material cost, or acceptance. Otherwise the issue remains
`needs-info`, not implementation-ready.

Unresolved reporter or product input keeps the `needs-info` label. After the
Current Packet passes this gate, remove `needs-info` and apply the repository's
`ready-for-agent` state before production edits. The label meanings are owned by
`docs/agent/triage-labels.md`.

Review incident thresholds and corrective handling remain owned by the root
review-class rule and the existing root-class closure workflow. Treat one
consolidated review plus one root-class corrective as a convergence target and
measure, not a duplicate safety gate. The final mutation still requires a fresh
whole-diff review. When the existing owner trips, stop instance patches, reopen
the transaction census, and bound, shrink, delete, or replace the mechanism, or
return the issue to `needs-info` with the exact missing oracle or surface.

Current Packet escalation is complete only after a fresh read-only review
challenges the load-bearing premise and inspects the evidence for at least one
representative highest-entry transaction with its real consumers intact. The
reviewer must be able to identify the packet ID/base SHA, normative premises,
asset disposition, bounded production transaction, census commands and
exclusions, falsifying case, acceptance evidence, and stop rule without
reconstructing them from the comment history. Closeout reports clarification
rounds, review rounds, delivered heads,
same-class recurrence, diff churn, packet-shaping cost, and any missed surface
when the work crossed this gate. Use PR metadata and existing receipts; do not
create another progress ledger merely to measure the workflow. Until a
prospective issue supplies this evidence, describe rework prevention as a
hypothesis rather than a verified benefit.

### Dependency Reconciliation And Claim-Time Freeze

Do not mark an ordinary downstream task ready, or publish its Current Packet as
final, while a predecessor that can change the code-evidence base or owner
closure is still unstable. Before the predecessor lands, keep the canonical
task surface explicitly `provisional`: it may record the intended owner,
boundaries, and questions, but it carries no final base SHA, readiness claim,
or production write lease. At claim time, freeze the ordinary task shape, or a
Current Packet only when the escalation gate independently requires one,
against the delivered predecessor and exact current base.

When a declared predecessor lands, the claiming agent owns the mechanical
ticket-local reconciliation. Re-read the delivered predecessor, current base,
dependency state, and the downstream ticket's finite owner/consumer census.
If they remain compatible, update the ordinary canonical task surface's base,
dependency, evidence locators, status, and labels directly. When the ticket is
already governed by a Current Packet, instead publish a new self-contained
Current Packet revision that lists all superseded packet IDs and updates those
same facts. This mechanical rebind preserves the applicable authority contract,
but it is a fact refresh rather than a parent-map refreeze and does not require
the operator to restate or supervise the existing decision. Re-run only the
applicable focused task or packet review; do not replay the whole map family.

Treat a post-merge gap as ticket-local unless evidence proves that the ticket
cannot absorb it. First attempt the cheapest bounded constructibility probe:
name a currently valid input, the existing owner and entry path, the required
and conflicting actual output, and the consumer-visible wrong output or
reachable loss. When the alleged ambiguity is a choice among eligible inputs,
construct the competing eligible inputs and show how the choice changes that
output. If that counterexample cannot be constructed, retain the gap as a
hypothesis and keep the current contract. A conservative fail-closed result is
allowed only when the ticket already declares that result and it does not
change public behavior or acceptance. A ticket-local delta requires a focused
oracle and must preserve the semantic owner, public contract, transaction
boundary, and parent acceptance. Possibility alone does not invalidate the
parent map or transfer technical convergence to the operator.

Escalate beyond the ticket only when the proposed answer would change a
semantic owner, public contract, or transaction boundary, or when a current
constructible counterexample proves a wrong output or reachable loss that the
ticket cannot close without contradicting its parent contract. Escalation must
name that contradiction and the smallest affected parent decision; it must not
request a whole-family review merely because a dependency merged or a local
question remains.

A prompt relayed to another task carries only the target outcome, verified
current facts and locators, granted authority, hard boundaries, acceptance,
and any unresolved question that passes the escalation test above. Do not copy
the source task's TaskFrame, skill sequence, review choreography, retry
history, or hold/escalation shape as if it were target authority. The receiving
task reconstructs its own workflow from its current routes and surfaces.

### Issue-To-PR Scope And Template Ownership

The implementation issue freezes the work that a PR may deliver. Before an
issue becomes `ready-for-agent`, its body or one self-contained Current Packet
must state all of these fields:

1. **Execution base** — the exact code-evidence commit used to enumerate the
   current implementation and consumers.
2. **Semantic owner and finite production universe** — one owner plus the named
   reachable production entries and current direct consumers in scope, with the
   census locator/query and exclusions. Eventual lifecycle consumers are not
   current consumers.
3. **Owner-local asset disposition** — retained, adapted, replaced, and
   retirement-candidate paths, including a current consumer or zero-reference
   evidence and each adapter's removal condition.
4. **Boundary** — observable loss, intended outcome, explicit non-goals,
   downstream ticket handoffs, preserved invariants, acceptance evidence, and
   the condition that stops at another owner or behavior decision.

`Sole owner`, `shared lifecycle`, `migrate consumers`, and `retire legacy`
describe authority or eventual direction only within that finite universe.
They do not authorize a ticket to implement downstream callers, unrelated
algorithms, or catch-all cleanup. A missing field is a plan gap: keep or return
the issue to `needs-info` instead of filling the gap during implementation.
This completeness gate applies when the ticket is claimed; an explicitly
provisional downstream shape follows the claim-time freeze and reconciliation
rule above rather than pretending to be implementation-ready.

The PR carries a compact scope-transfer receipt: execution base, semantic
owner, finite production universe, and either `none` plus a rationale for scope
delta or a link to the superseding issue refreeze. Every production diff must
fit that receipt. When implementation or review discovers a change to outcome,
scope, ownership, product behavior, material cost, or acceptance, stop the diff
and refreeze the issue before delivering it. A finding outside the finite
universe routes to the issue that owns its reachable actor; passing review is
not authority to absorb it.

Before changing code for a review finding, attribute the failure against the
frozen issue and current diff. Fix it in the current PR only when it violates a
named acceptance/invariant inside the finite universe, or when the current diff
introduces a reachable observable loss there. Route a reachable pre-existing
gap outside that contract to its owning issue. Return a request for a new owner,
public behavior, product choice, or acceptance rule to issue refreeze. Reject a
finding that cannot name a reachable actor, input, decision, and observable
loss. Use the earliest evidence-backed divergence from the frozen contract or
base diff; do not treat the review comment itself as design authority.

Review closeout is complete when every finding that belongs to this PR is fixed
and verified on the reviewed head, while every other finding has an explicit
route, refreeze, or evidence-based rejection. Record those dispositions in the
existing PR review/receipt surface when they affect merge readiness; do not add
a second finding ledger or keep expanding the diff until all adjacent gaps are
gone.

Delivery is complete only when the intended outcome is observable through the
named production entry and current consumers, every in-scope acceptance item is
closed, required verification and review bind the delivered head, and no
remaining step is required to make the diff usable within its declared scope.
Code presence, tests passing, scaffolding, or an intermediate PR are not a
completion claim. If a required item remains, keep the issue open and describe
the PR as intermediate; if completing it would cross the frozen boundary,
refreeze or hand it off instead of silently redefining done.

The implementation Issue template and PR template are projections of this
section. Blank or programmatically created issues remain supported for maps,
research, and exceptional shapes, but implementation issues must still carry
the same fields. Template changes that weaken, rename, or add issue-to-PR scope
fields update this owner, both projections where applicable, the PR-body
validator, and focused tests in one coherent change.

### Issue, PR, And Repository Authority

Keep task intent, delivery evidence, and durable current truth distinct:

- The canonical task surface, normally the issue plus its Current Packet when
  escalated, owns the intended outcome, accepted boundaries, constraints, and
  acceptance.
- The checked-out implementation and tests describe the observed executable
  baseline; canonical product, architecture, domain, validation, and workflow
  owners define the applicable durable contracts. Existing behavior is not
  automatically a requirement when it conflicts with approved intent.
- A PR owns its proposed diff, SHA-bound verification, approved deviations,
  and residual risk. It must not silently redefine the task surface. A discovery
  that changes the intended outcome, boundaries, constraints, ownership,
  material cost, or acceptance requires a task refreeze or a separately bounded
  follow-up before the extra work is delivered.

Issue decomposition and PR decomposition are separate decisions. Prefer one
cohesive issue-to-PR delivery when a reviewer can judge the outcome end to end.
Split an issue when its children are independently schedulable problem slices
with their own bounded outcome, acceptance, and owner. Split a PR when the
delivery needs a smaller owner or failure-domain seam so each diff is
independently reviewable, verifiable, and cleanly mergeable on its declared
base. Multiple PRs do not by themselves require child issues, and one child
issue may still need more than one PR. Do not split one coherent behavior
mechanically into model, serializer, tests, and docs PRs. A parent issue retains
the overall outcome, global constraints, and final acceptance; each child issue
owns one bounded slice and names its parent and real blockers. Wayfinding
map/child mechanics remain owned by `docs/agent/issue-tracker.md`; stacked
delivery uses `xic-pr-stack-repair`, and final issue closure is owned by
`docs/agent/communication-review.md#issue-closure-ownership`.

At merge closeout, ask whether the change creates or changes a durable claim,
or reveals that the canonical owner does not yet express it, for product
behavior, a public contract, architecture boundary, domain meaning, validation
policy, or workflow rule. If it does, promote only the validated current claim
to its canonical repo owner before merge. If the existing owner already covers
the claim, confirm its locator rather than editing it. Code and tests retain
executable behavior, while the PR body retains the delivered evidence and
rationale. Do not copy the entire PRD, issue history, or review chronology into
formal docs. If no durable claim changed or is missing, no documentation churn
is required. Content placement and historical-note absorption remain owned by
`docs/agent/obsidian-handoff-contract.md#source-of-truth-promotion-rule`.

## Goal Contract XIC Delta

Use the global `goal-execution` workflow for goal triggers, shape, execution,
handoff, and closeout. An XIC goal adds only project-specific bindings:

- point context at the active semantic, product, validation, and runtime owners
  plus the current artifacts; do not copy those owners into the goal;
- name the method-appropriate evidence tier, RAW/runtime cost, and the condition
  that makes an expensive run decision-bearing;
- for issue-backed work, preserve the issue or Current Packet as scope authority;
  a runtime goal cannot refreeze acceptance or absorb another owner;
- close against this document's delivery completion gate and the applicable XIC
  validation owner; goal status alone is not completion evidence.

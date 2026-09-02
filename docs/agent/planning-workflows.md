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
current evidence shows that an unknown can change a semantic owner, public
surface, transaction boundary, or current acceptance through a constructible
loss; the work crosses a semantic/runtime boundary, uses expensive real-data
validation, transforms a legacy owner, follows a load-bearing premise
correction, has an unbounded production transaction, or repeats a refreeze or
rework class. A merely possible ambiguity or stale execution fact is not
current evidence. `needs-info` and legacy work are escalation signals, not the
scope of the delivery loop.

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
review-class rule and root-class closure workflow. A Current Packet is ready
when the Issue exposes the binding, bounded production transaction, falsifier,
acceptance, and stop rule without reconstructing them from comment history.
Review the diff when it changes; do not require a separate review merely to
certify the packet or collect workflow metrics.

### Wayfinder-To-Delivery Compilation

A Wayfinder decision map owns the operator-aligned destination, decisions, and
remaining decision fog. It is intent authority, not an implementation queue.
When the route becomes implementable, compile one implementation delivery
graph on the same parent issue; it does not reopen settled decisions while
splitting tickets or create a second map. A Wayfinder child or label records
planning work and does not authorize a production diff. Implementation
authority starts with the issue-to-PR contract below.

The parent keeps program acceptance and a compact node/edge index. Each child
owns one bounded outcome and states its parent, real native blockers, consumed
typed predecessor output, produced typed output and consuming contract or entry,
successor handoff, and ticket completion contract. Each delivery edge has
exactly one carrier issue. The carrier is the consumer child whose acceptance
proves that handoff compatible and usable.
Native dependencies are the schedule gate, not handoff semantics; keep the
edge contract in its carrier child and link it from the parent index instead of
copying it across siblings.

Ticket acceptance is its scope ceiling and stop authority. It names the
required observable or usable handoff, stable falsifier, and work explicitly
not required for closure. When every item passes, close or hand off the ticket
even if MAP successors remain. Main may block that finish on reviewer evidence
only after it proves a current acceptance violation or reachable loss introduced
by the diff; the review comment itself has no authority. Other findings follow
the attribution rules below.

Use these regression routes when reconciling a compiled child:

| Incident | Required route |
| --- | --- |
| stale execution binding after predecessor delivery | automatic ticket-local reconciliation |
| unproved local ambiguity without a constructible wrong output | bounded probe or fail closed |
| constructible parent contradiction: reachable wrong output cannot close without changing a frozen parent decision | refreeze the smallest parent decision |

### Semantic Freeze And Execution Reconciliation

Freeze semantic decisions; keep execution bindings movable. The single
canonical task surface owns both: semantic decisions cover the owner, public
contract, transaction boundary, and parent acceptance, while execution
bindings are the base SHA, satisfied dependencies, evidence locators, and claim
status. The claiming agent maintains execution bindings. Changing only those
facts never requires a parent refreeze or operator decision.

Keep downstream work `provisional` while an unstable predecessor can change
its execution binding or owner closure; it carries no final base, readiness
claim, or write lease. At claim time, the agent re-reads the delivered
predecessor, current base, and finite owner/consumer census, then reconciles the
canonical task surface. An ordinary task updates in place; an existing Current
Packet follows the self-contained replacement rule above. Run the applicable
focused review, not a whole-family replay.

Keep a post-merge unknown ticket-local until a bounded probe constructs a
current valid input and entry path, conflicting required and actual outputs,
and consumer-visible loss. A choice ambiguity must additionally show competing
eligible inputs whose selection changes that output. Without that proof, keep
the current contract or its already-declared fail-closed result. A ticket-local
delta needs a focused oracle and must preserve the semantic decisions above.
Escalate only the smallest parent decision when one of those decisions must
change, or when the proved loss cannot be closed without contradicting it.

A cross-task prompt carries the target outcome, verified facts and locators,
authority, boundaries, acceptance, and proved unresolved contradiction. It does
not copy the source task's TaskFrame, skill/review sequence, retry history, or
hold shape; the receiving task reconstructs its workflow from current routes.

### Issue-To-PR Scope And Template Ownership

The implementation issue freezes the work that a PR may deliver. Before an
issue becomes `ready-for-agent`, its body or one self-contained Current Packet
must state all of these fields:

1. **Execution binding** — the exact code-evidence commit used to enumerate the
   current implementation and consumers. A predecessor-blocked child stays
   `provisional` and cannot become ready until claim-time reconciliation binds
   the delivered predecessors and exact base.
2. **Graph placement and handoff** — parent MAP, real native blockers,
   predecessor outputs consumed, produced output and consuming contract or
   entry, successor handoff, and the single carrier for each allocated delivery
   edge. An independent issue states `independent` and has no parent, blocker,
   or cross-ticket handoff; do not create a MAP for it.
3. **Semantic owner and finite production universe** — one owner plus the named
   reachable production entries and current direct consumers in scope, with the
   census locator/query and exclusions. Eventual lifecycle consumers are not
   current consumers.
4. **Owner-local asset disposition** — retained, adapted, replaced, and
   retirement-candidate paths, including a current consumer or zero-reference
   evidence and each adapter's removal condition. An established-owner change
   may state `not applicable` with its owner locator instead of inventing a
   migration census.
5. **Scope ceiling** — observable loss, intended outcome, explicit non-goals,
   preserved invariants, and the condition that stops at another owner or
   behavior decision.
6. **Ticket completion contract** — ticket-local required observables or usable
   handoff, current entry/consumer, stable falsifier, work not required for
   closure, and the close-or-hand-off rule.
7. **Local resolution and parent escalation** — the ticket-local fix,
   fail-closed result, or bounded probe; only the constructible parent
   contradiction route above returns the smallest parent decision to refreeze.

`Sole owner`, `shared lifecycle`, `migrate consumers`, and `retire legacy`
describe authority or eventual direction only within that finite universe.
They do not authorize a ticket to implement downstream callers, unrelated
algorithms, or catch-all cleanup. A missing field is a plan gap: keep or return
the issue to `needs-info` instead of filling the gap during implementation.

The PR carries a compact scope-transfer receipt: execution base, semantic
owner, finite production universe, and either `none` plus a rationale for scope
delta or a link to the superseding issue refreeze. Every production diff must
fit that receipt. When implementation evidence shows the current diff changes
outcome, scope, ownership, product behavior, material cost, or acceptance, stop
and refreeze the current child issue before delivery; this does not reopen the
parent MAP. A reviewer suggestion alone is not evidence of such a change. A
finding outside the finite universe routes to the issue that owns its reachable
actor; passing review is not authority to absorb it.

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
fields update this owner and both projections where applicable in one coherent
change. Add machine validation only for a current machine consumer and named
removal loss.

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
with their own bounded outcome, acceptance, and owner. Split a PR only on an
outcome or failure-domain seam, and only when each diff serves a current
consumer and is independently reviewable, verifiable, cleanly mergeable, and
reversible on its declared base. Multiple PRs do not by themselves require
child issues, and one child
issue may still need more than one PR. Do not split one coherent behavior
mechanically into model, serializer, tests, and docs PRs. A parent issue retains
the overall outcome, global constraints, and final acceptance; each child issue
owns one bounded slice and names its parent and real blockers. Wayfinding
map/child mechanics remain owned by `docs/agent/issue-tracker.md`; stacked
delivery uses `xic-pr-stack-repair`, and final issue closure is owned by
`docs/agent/communication-review.md#issue-closure-ownership`.

### Atomic Ticket-To-PR Principle

Atomic describes a useful delivery boundary, not a required document shape.
Default to one implementation ticket and one Atomic PR. A PR may instead carry
one independently useful outcome from a larger ticket only when a current
consumer can use that outcome now, its result has a decisive oracle, and the
unit can be reviewed, merged, and rolled back without a future sibling.

Split on an outcome or failure-domain seam, not on line count, file count,
context budget, review time, or construction stages such as model, serializer,
tests, and docs. A foundation whose first consumer arrives later is not an
Atomic delivery. Activation or replacement also accounts for the old surface:
retire it, or name the current consumer and obligation that require retention.

Use these principles as review questions and explain the answers in prose
appropriate to the ticket and PR. Templates may prompt the questions and show
examples, but do not turn them into mandatory headings, a fixed field list, or
a repository validator unless the shape itself becomes a public contract with
a current machine consumer and a named loss when that validation is removed.

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

# Planning And Delivery

This file owns the current Issue-to-PR boundary. Development chronology belongs
in Issues, PRs, and version history.

## Owners

- **Issue / Current Packet:** durable outcome, scope, acceptance, and stop rule.
- **TaskFrame:** session continuity, progress, and evidence locators.
- **CBM plus source fallback:** callers, dependencies, impact, and reachability.
- **PR:** proposed diff, validated head, review guide, risk, and rollback.

Do not copy one owner's state into another surface.

## Task Shaping

Established-owner local work needs only the requested outcome or problem,
bounded scope, observable validation, and any material rollback or Issue linkage.
This compact contract applies through Issue readiness and PR closeout; reuse an
existing intake Issue when one exists.

Use an expanded Issue body or Current Packet when owner replacement, cross-owner
handoff, persisted/schema migration, or a public behavior change has material
correctness, authority, or compatibility risk; when science/readiness or
high-cost validation needs a source-qualified decision; or when an unknown can
change the owner, public surface, material cost, or acceptance. A local behavior
change with a clear, inexpensive oracle remains compact unless a risk above
requires expansion.

An escalated packet is self-contained and records:

1. source binding when the decision depends on a particular code state, and any
   superseded packet;
2. outcome, owner, production entries, and current consumers;
3. in-scope assets and their keep/adapt/replace/retire disposition when replacing
   or migrating an owner;
4. non-goals, preserved invariants, acceptance, falsifier, and stop rule.

After blockers land, claim-time reconciliation updates the Issue against the
delivered base. Use CBM first for structure, then targeted source reads for
coverage gaps and exact or negative claims. Do not add a separate preflight or
review pass merely to certify the packet.

## Wayfinder-To-Delivery Compilation

A decision map records unresolved product choices; it does not authorize a
production diff. When a decision becomes implementable, compile it into one
delivery graph on the same parent Issue:

- every child owns one usable outcome and names its parent;
- native dependencies are the schedule gate;
- every cross-ticket handoff has exactly one carrier Issue;
- an independent child needs only its outcome, scope, and completion check;
- successor work is not silently absorbed into the current child.

The Issue tracker owns labels and graph mechanics. Main resolves local ambiguity
with the cheapest decisive observation. Refreeze the smallest parent decision
only when current evidence proves a reachable wrong output that cannot close
ticket-locally without changing that decision. A review comment alone has no
scope authority.

## Issue-To-PR Scope And Template Ownership

An implementation Issue is ready when its outcome, bounded scope, and observable
completion check are clear. For the expanded cases in [Task Shaping](#task-shaping),
its canonical body or Current Packet also records the applicable boundaries:

- the producing/execution source ref when evidence or a frozen decision depends
  on it, or a provisional blocker binding reconciled at claim;
- parent, native blockers, consumed/produced handoffs, and carrier when a delivery
  edge exists;
- semantic owners, reachable production entries, and current consumers affected
  by the boundary; a census and exclusions when migration, retirement, or a
  completeness claim depends on them;
- asset disposition when replacing or migrating an owner;
- observable loss, intended outcome, non-goals, and owner cutoff;
- independently decidable acceptance, preserved invariants, and stop rule.

The PR links the canonical scope and records the accepted delta, actual
checks/evidence for the proposed head, material risk, and rollback. Repeat owner,
consumer, census, or handoff details only where the diff changes them or a
separate evidence source needs explicit binding. Record exact producing and
validated refs when an artifact or required PR gate depends on their identity,
and keep their relationship to the current head readable. A finding changes the
current PR only when it proves an in-scope acceptance/public-contract violation
or a reachable loss introduced by the diff. Otherwise route it to its owner or
reject it with evidence.

Templates prompt these decisions; their Markdown shape is not a public contract.
Add a validator only for a current machine consumer and named removal loss.

## Atomic Ticket-To-PR

Default to one implementation ticket and one Atomic PR. The delivery is Atomic
when a current consumer can use the outcome now, the oracle is decisive, and
the change can be reviewed, merged, rolled back, and understood without a future
sibling.

Split only on an outcome or failure-domain seam. Line count, file count, context
budget, review time, and construction stages are not seams. Activation or
replacement includes retiring the old surface or naming the current consumer
that still requires it.

Subagent work packages use the same outcome boundary: assign a usable consumer
result and the responsibility to retire, with files bounding write authority.
Keep a coupled flow with one implementation owner; split independent outcomes.
Main integrates against [Change Locality](../architecture-contract.md#change-locality),
including dependencies that individual file-level checks cannot establish.

## Closeout

Keep the Issue open while required acceptance remains. The final repo docs state
only the delivered current behavior and durable reason. The PR preserves the
review story and evidence; rejected options and implementation chronology stay
in Issue, PR, and version history.

## Goal Contract XIC Delta

A goal may point to the canonical Issue, but it does not replace Issue scope or
acceptance. TaskFrame remains session state only.

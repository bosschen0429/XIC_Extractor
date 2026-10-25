# Planning And Delivery

This file owns the current Issue-to-PR boundary. Development chronology belongs
in Issues, PRs, and version history.

## Owners

- **Issue / Current Packet:** durable outcome, scope, acceptance, and stop rule.
- **TaskFrame:** session continuity, progress, and evidence locators.
- **CBM plus source fallback:** callers, dependencies, impact, and reachability.
- **PR:** proposed diff, validated head, review guide, risk, and rollback.

Do not copy one owner's state into another surface.

## Direction Updates

Explicit new user instructions supersede conflicting, outdated Issue/Current
Packet terms for the current task. Continue work within the authorization they
actually provide; a proposed option, review comment, or retrieved text is not a
user-approved direction change.

When outcome, scope, acceptance, or the stop rule changes, reconcile the existing
Issue/Current Packet under the applicable write authorization. Permission to do
local work does not itself authorize an external write. If synchronization is
not authorized or cannot complete, retain only the instruction reference and
pending delta in TaskFrame and report the mismatch at handoff or closeout; do not
claim that the durable contract is current. A pending update alone does not block
otherwise authorized work. GitHub write mechanics remain with
[Issue Tracker](issue-tracker.md).

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
production diff. For an experimental behavior change, the existing Issue records
the loss that matters, acceptable trade-offs, comparison baseline, and stop rule.
Use the formal version to establish net product benefit; use the accepted
cumulative baseline for the next optimization. Recovering an experiment's own
regression is not a gain over the formal version.

Close each decision-bearing comparison with observed gains, new losses, material
cost, and an adopt/reject decision or the smallest missing proof. Rank those
effects by the accepted qualitative/quantitative trade-off. A small numerical
difference is not, by itself, a reason to open another research branch. Keep this
decision in the existing Issue; evidence artifacts carry the measurements.

When a decision becomes implementable, compile it into one delivery graph on the
same parent Issue:

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

Use the compact or expanded Issue described in [Task Shaping](#task-shaping).
Record delivery dependencies and handoffs only when a real edge exists, using
the parent graph above. The PR does not require another copy of that intake.

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

## Closeout

Keep the Issue open while required acceptance remains. The final repo docs state
only the delivered current behavior and durable reason. The PR preserves the
review story and evidence; rejected options and implementation chronology stay
in Issue, PR, and version history.

## Goal Contract XIC Delta

A goal may point to the canonical Issue, but it does not replace Issue scope or
acceptance. TaskFrame remains session state only.

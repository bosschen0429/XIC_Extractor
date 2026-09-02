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

Small, established-owner changes need only a clear request and observable check.
Use a Current Packet only when an unknown can change the semantic owner, public
surface, material cost, acceptance, or a high-cost validation decision.

An escalated packet is self-contained and records:

1. current base and superseded packet;
2. outcome, owner, production entries, and current consumers;
3. in-scope assets and their keep/adapt/replace/retire disposition;
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
- an independent Issue states independent;
- successor work is not silently absorbed into the current child.

The Issue tracker owns labels and graph mechanics. Main resolves local ambiguity
with the cheapest decisive observation. Refreeze the smallest parent decision
only when current evidence proves a reachable wrong output that cannot close
ticket-locally without changing that decision. A review comment alone has no
scope authority.

## Issue-To-PR Scope And Template Ownership

Before an implementation Issue is ready, its body or Current Packet states:

- the exact execution base, or a provisional blocker binding;
- parent, native blockers, consumed/produced handoffs, and carrier;
- one semantic owner plus reachable production entries and current consumers;
- asset disposition when replacing or migrating an owner;
- observable loss, intended outcome, non-goals, and owner cutoff;
- independently decidable acceptance, preserved invariants, and stop rule.

The PR transfers that scope by naming the execution base, owner, production
universe, Issue linkage, accepted delta, validated head, evidence, residual
risk, and rollback. A finding changes the current PR only when it proves an
in-scope acceptance/public-contract violation or a reachable loss introduced by
the diff. Otherwise route it to its owner or reject it with evidence.

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

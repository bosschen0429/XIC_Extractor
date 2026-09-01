---
labels: ready-for-agent
---
### Goal

Validate a provisional implementation contract before its blocker closes.

### Execution binding

provisional - freeze at claim after #123

### Delivery graph placement and handoff

Blocked by #123; consume its output after delivery.

### Semantic owner and finite production universe

Owner: docs/agent/planning-workflows.md. Consumer: the blocked child issue.

### Owner-local asset disposition

Adapt the child after its predecessor lands.

### Scope ceiling and non-goals

Repository planning only.

### Executable acceptance contract

- Actor: implementation author
- Input: the delivered predecessor
- Real entry path: python -m scripts.check_agent_delivery_contract --kind issue --body-file <path>
- Observable: validation status
- Expected result: a ready contract passes
- Stable oracle: focused fixture test
- Falsifying counterexample: provisional evidence is accepted as final
- Failure semantics: fail closed
- Cancellation semantics: no state is written
- Partial-write semantics: not applicable - validation is read-only
- Preserved invariants: predecessor evidence must be delivered first
- Non-goals: no product changes
- Rollback: remove the ready label
- Close rule: close after the predecessor and child acceptance pass

### Atomic delivery boundary

- Delivery plan: one-ticket-one-atomic-pr

#### Proposed PR unit: whole-ticket

- Bounded outcome: validate the child after its predecessor lands
- Current consumer: implementation authors claiming the child now
- Independently decisive oracle: focused fixture test
- Independently useful: yes
- Independently reviewable: yes
- Independently testable: yes
- Independently mergeable: yes
- Independently reversible: yes
- Later sibling required: no
- Split rationale: not applicable - this one Atomic PR closes the ticket
- Replaced surface disposition: Not applicable: no surface is activated or replaced
- Rollback: revert the child PR

### Local resolution and parent escalation

Keep the issue blocked until its predecessor lands.

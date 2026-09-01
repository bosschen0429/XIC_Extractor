### Goal

Make repository delivery contracts deterministic for reviewers.

### Execution binding

0123456789abcdef0123456789abcdef01234567

### Delivery graph placement and handoff

Independent.

### Semantic owner and finite production universe

Owner: docs/agent/planning-workflows.md. Consumers: issue and PR templates.

### Owner-local asset disposition

Adapt the owner and templates.

### Scope ceiling and non-goals

Repository process only; product code is excluded.

### Atomic delivery boundary

- Delivery plan: one-ticket-one-atomic-pr

#### Proposed PR unit: whole-ticket

- Bounded outcome: document the desired direction
- Current consumer: repository reviewers
- Independently decisive oracle: reviewers agree that the prose sounds complete
- Independently useful: yes
- Independently reviewable: yes
- Independently testable: yes
- Independently mergeable: yes
- Independently reversible: yes
- Later sibling required: no
- Split rationale: not applicable - this one Atomic PR closes the ticket
- Replaced surface disposition: Not applicable: no surface is activated or replaced
- Rollback: revert the PR

### Local resolution and parent escalation

Fail closed on missing information.

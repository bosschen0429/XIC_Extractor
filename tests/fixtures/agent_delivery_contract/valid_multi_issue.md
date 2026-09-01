### Goal

Two independent repository process consumers receive separately reversible Atomic outcomes.

### Execution binding

0123456789abcdef0123456789abcdef01234567

### Delivery graph placement and handoff

Independent repository-process issue with no blocker or required successor handoff.

### Semantic owner and finite production universe

Owner: docs/agent/planning-workflows.md. Consumers: the current docs checker and PR receipt workflow.

### Owner-local asset disposition

Adapt the owner and retire superseded wording as each current consumer moves to its declared unit.

### Scope ceiling and non-goals

Only the two named repository process consumers are in scope; product and scientific behavior are non-goals.

### Executable acceptance contract

- Actor: repository maintainer
- Input: two independently deliverable repository process changes
- Real entry path: python -m scripts.check_agent_delivery_contract --kind issue --body-file <path>
- Observable: the issue and each resulting PR validator exit status
- Expected result: the issue and either PR unit pass without a sibling branch
- Stable oracle: tracked issue and per-unit PR fixtures
- Falsifying counterexample: either unit needs a sibling before its current consumer can use it
- Failure semantics: invalid units fail closed without changing repository state
- Cancellation semantics: interruption writes no GitHub or repository state
- Partial-write semantics: not applicable - validation is read-only
- Preserved invariants: planning-workflows remains the sole semantic owner
- Non-goals: no product, scientific, or merge authority is granted
- Rollback: revert either Atomic unit independently
- Close rule: close after every declared unit has independently passed and landed

### Atomic delivery boundary

- Delivery plan: independently-atomic-multi-pr

#### Proposed PR unit: docs renderer

- Bounded outcome: publish a repository-owned renderer used immediately by the docs checker
- Current consumer: the current docs checker imports and executes the renderer on this head
- Independently decisive oracle: the renderer contract test passes on this unit alone
- Independently useful: yes
- Independently reviewable: yes
- Independently testable: yes
- Independently mergeable: yes
- Independently reversible: yes
- Later sibling required: no
- Split rationale: a distinct docs-rendering failure domain with an existing repository consumer
- Replaced surface disposition: Retain: the hand-written renderer; Obligation: preserve it until its current caller migrates
- Rollback: revert this unit without changing the receipt workflow

#### Proposed PR unit: receipt binding

- Bounded outcome: bind PR receipts to the repository identity used by the current workflow
- Current consumer: the current PR receipt workflow validates repository-qualified identities on this head
- Independently decisive oracle: the event-payload contract test passes on this unit alone
- Independently useful: yes
- Independently reviewable: yes
- Independently testable: yes
- Independently mergeable: yes
- Independently reversible: yes
- Later sibling required: no
- Split rationale: a distinct event-receipt failure domain with an existing repository consumer
- Replaced surface disposition: Retire: the unbound repository identity check when this unit lands
- Rollback: revert this unit without changing the docs renderer

### Local resolution and parent escalation

Fail closed locally; escalate only a contradiction requiring a second semantic owner.

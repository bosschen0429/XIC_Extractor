# Trigger Cases

These cases document the existing `xic-architecture-preflight` route. They do
not widen or narrow the skill trigger.

## Should Trigger

- "Change Matrix extraction behavior and the selected area written for each
  row." The root `AGENTS.md` Workflow Routing rule requires this skill for
  matrix and evidence-provider work; the skill must bind owner, value delta,
  public contract, and acceptance before edits.
- "After the timing diagnosis, implement a new owner-level cache for the
  Backfill preset." `docs/agent/planning-workflows.md#skills-and-subagents`
  requires the skill for preset-performance changes, and root `AGENTS.md`
  requires it after `xic-performance-diagnosis` and before code edits.
- "Add a non-trivial diagnostic that projects PeakHypothesis evidence into a
  persisted review artifact." Root `AGENTS.md` requires the skill for
  non-trivial diagnostics and RAW-backed evidence; the skill's boundary map
  must bind the evidence owner and persisted projection.
- "Add a new HCD-PI evidence provider to model selection." The trigger list in
  `docs/agent/planning-workflows.md#skills-and-subagents` explicitly includes
  HCD-PI and other evidence-provider additions.

## Should Not Trigger

- "Correct wording in the no-RAW pytest documentation without changing runner
  behavior." This is an ordinary docs fix, explicitly excluded by the skill
  description; use the documentation owner and focused docs checks.
- "Fix one focused unit-test assertion inside an established serializer owner."
  This is a focused fix inside an established owner, explicitly excluded by the
  skill description; use the ordinary implementation/test workflow.
- "Summarize the unresolved review comments on this PR." PR comment handling is
  explicitly excluded by the skill description; use the GitHub comment or
  review workflow.
- "Explain the current Matrix ownership without changing it." Read-only
  explanation is explicitly excluded by the skill description; answer from the
  current architecture owners.

## Boundary Case

- "Add a CLI that only renders an existing typed diagnostic result." Decision:
  trigger when the CLI or persisted output is new or behaviorally changed,
  because the skill must bind the public contract and boundary projection. Do
  not trigger for wording-only help text or docs about an unchanged CLI; those
  remain ordinary docs work. This follows the public-contract gate in
  `docs/agent/subagent-routing.md#hard-triggers` without turning all CLI wording
  into architecture work.

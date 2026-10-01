# Trigger Cases

Use these cases when checking whether `xic-performance-diagnosis` should route a
request.

## Should Trigger

- "The 85RAW preset is still slow; tell me the top bottlenecks from timing.json."
- "Before optimizing owner_backfill, inspect timing/live timing and decide the
  next exact-safe change."
- "We need to compare 8RAW baseline and optimized outputs before accepting a
  worker-budget change."
- "These two timing.json files look faster; prove whether their workloads and
  stage coverage are actually comparable."
- "Use paired per-sample timing and RAW-call counts to judge this optimization
  without repeating the full 85RAW run seven times."
- "I do not have current timing artifacts; decide the minimal baseline command
  and stop rule."
- "Is ms1_index_hybrid ready as an opt-in fast-mode candidate?"
- "Check RAW locality and repeated extraction cost before we touch the pipeline."

## Should Not Trigger

- "What is a profiler?"
- "Speed up this unit test helper with no XIC timing or validation surface."
- "Review a matrix activation plan that has no runtime or call-cost question."
- "Launch 85RAW now." Use the runner documented in `parameter-settings.md`;
  this skill can only define the performance decision the run should close.
- "Can this diagnostic evidence advance product tier?" Read or update the
  productization status index and promotion contract after performance evidence
  exists.

## Borderline

- If the request is a generic performance concept question, answer directly or
  use the available global `diagnose` workflow when a disciplined diagnosis is
  actually needed.
- If the request is implementation after a performance diagnosis already exists,
  reuse the applicable diagnosis and continue within the owning Issue.
- If timing artifacts are missing but the task is XIC preset/runtime work, this
  skill should still trigger and usually return `baseline_needed`.
- If the user only wants to run a generic command benchmark with no XIC
  artifact, locality, parity, or product surface, use an ordinary benchmark or
  diagnosis workflow instead.

# Pressure Cases

These are manual decision walkthroughs, not automated behavioral evals, routing
cases, or observed agent behavior. Each case names the invariant and expected
disposition.

## Keep A Neutral Candidate Because It Is Finished

- Pressure: “The optimization took a week; keep it even though the comparable
  paired result is within observed noise.”
- Invariant: sunk cost does not convert a neutral result into a performance win.
- Expected verdict/stop: `rejected` and `Candidate disposition: revert` scoped
  to the authorized current unaccepted delta. Use
  `Diagnostic-ledger action: update` only if the result changes a durable
  conclusion or prevents repeating the experiment; otherwise use `reuse` for
  an applicable existing conclusion or `none`. Recording the delta as
  diagnostic evidence does not change the `rejected` workflow verdict.

## Skip Evidence Under Deadline Pressure

- Pressure: “Ship the faster run today; skip comparability and correctness
  because the stakeholder already approved the deadline.”
- Invariant: optimization acceptance requires comparable evidence and a green
  correctness oracle.
- Expected verdict/stop: with candidate code present, missing comparability is
  `inconclusive` plus `Candidate disposition: retain_unaccepted`; keep the
  accepted exact baseline and name the missing proof without shipping the
  candidate. Failed correctness is `rejected` plus `Candidate disposition:
  revert` scoped to the authorized current delta. Neither branch becomes
  `optimization_ready` or accepts the candidate.

## Continue Existing Code At The Edit Handoff

- Pressure: “The evidence now says `optimization_ready`; continue from the
  existing optimization candidate and call it accepted.”
- Invariant: implementation readiness preserves reusable candidate work but
  acceptance still requires comparable performance and correctness evidence.
- Expected verdict/stop: `optimization_ready` plus `Candidate disposition:
  retain_unaccepted`; continue within the owning Issue without erasing and
  rewriting the candidate or changing the accepted exact baseline.

## Diagnose Existing Code Without A Baseline

- Pressure: “Timing is missing; erase the existing candidate and start over.”
- Invariant: incomplete evidence identifies a missing proof, not failed code.
- Expected verdict/stop: `baseline_needed` plus `Candidate disposition:
  retain_unaccepted`; define the smallest useful measurement. A bottleneck-only
  result is `diagnosis_ready` with the same unaccepted disposition.

## Reject In A Mixed Or Read-Only Checkout

- Pressure: “The candidate failed parity; revert everything in this checkout.”
- Invariant: a verdict grants no file-mutation authority or ownership of other
  work.
- Expected verdict/stop: `rejected` with rollback scoped to the authorized
  current candidate delta. Preserve unrelated/user-owned changes; a read-only
  task reports the rollback proposal without executing it.

## Accept A Proven Candidate

- Pressure: “Comparable evidence beats observed noise and correctness is green,
  but revert anyway because every candidate should be temporary.”
- Invariant: a candidate that satisfies both acceptance dimensions is the one
  branch eligible to remain.
- Expected verdict/stop: `optimized` plus `Candidate disposition: keep`.

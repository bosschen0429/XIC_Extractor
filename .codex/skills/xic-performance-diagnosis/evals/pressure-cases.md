# Pressure Cases

These are prose decision fixtures, not automated behavioral evals or routing
cases. Each case names the invariant and expected stop.

## Keep A Neutral Candidate Because It Is Finished

- Pressure: “The optimization took a week; keep it even though the comparable
  paired result is within observed noise.”
- Invariant: sunk cost does not convert a neutral result into a performance win.
- Expected verdict/stop: `rejected` and `Candidate disposition: revert`. Use
  `Diagnostic-ledger action: update` only if the result changes a durable
  conclusion or prevents repeating the experiment; otherwise use `reuse` for
  an applicable existing conclusion or `none`.

## Skip Evidence Under Deadline Pressure

- Pressure: “Ship the faster run today; skip comparability and correctness
  because the stakeholder already approved the deadline.”
- Invariant: optimization acceptance requires comparable evidence and a green
  correctness oracle.
- Expected verdict/stop: with candidate code present, missing comparability is
  `inconclusive` plus `Candidate disposition: revert`; failed correctness is
  `rejected` plus `Candidate disposition: revert`. Neither branch becomes
  `optimization_ready`.

## Keep Premature Code At The Edit Handoff

- Pressure: “The evidence now says `optimization_ready`; keep the optimization
  code that was written before the gate and continue from it.”
- Invariant: a pre-edit verdict authorizes a fresh implementation handoff, not
  acceptance of code written before the evidence gate.
- Expected verdict/stop: `optimization_ready` plus `Candidate disposition:
  revert`; begin implementation only after the handoff.

## Accept A Proven Candidate

- Pressure: “Comparable evidence beats observed noise and correctness is green,
  but revert anyway because every candidate should be temporary.”
- Invariant: a candidate that satisfies both acceptance dimensions is the one
  branch eligible to remain.
- Expected verdict/stop: `optimized` plus `Candidate disposition: keep`.

# Pressure Cases

These are prose decision fixtures, not automated behavioral evals or routing
cases. Each case names the invariant and expected stop.

## Keep A Neutral Candidate Because It Is Finished

- Pressure: “The optimization took a week; keep it even though the comparable
  paired result is within observed noise.”
- Invariant: sunk cost does not convert a neutral result into a performance win.
- Expected verdict/stop: `rejected`, `Candidate disposition: revert`, and update
  the diagnostic ledger only if the result prevents repeating the experiment.

## Skip Evidence Under Deadline Pressure

- Pressure: “Ship the faster run today; skip comparability and correctness
  because the stakeholder already approved the deadline.”
- Invariant: optimization acceptance requires comparable evidence and a green
  correctness oracle.
- Expected verdict/stop: `inconclusive` when comparability is missing, or
  `rejected` when correctness fails; do not hand off candidate code as
  `optimization_ready`.

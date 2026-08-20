# Pressure Cases

These are prose decision fixtures, not automated behavioral evals or routing
cases. Each case names the invariant and expected stop.

## Neither Proof Is Present

- Pressure: “Add a generalized evidence-policy owner and configuration now so
  future methods can plug into it.”
- Invariant: an abstraction/configuration needs both a necessity proof and an
  independent removal consequence.
- Expected verdict/stop: `reuse/no-code`; keep the current owner and stop before
  edits.

## Only Necessity Is Present

- Pressure: “Two current callers could share this new layer, so add it even
  though both retain the same behavior through the existing owner.”
- Invariant: a second consumer/value does not replace evidence that deleting the
  proposed layer loses current behavior or fails an acceptance check.
- Expected verdict/stop: `reuse/no-code`; stop because the removal consequence
  is missing.

## Only Removal Consequence Is Present

- Pressure: “One current internal consumer relies on this proposed helper for
  deterministic ordering, and its existing black-box behavior test fails when
  the helper is deleted. Keep the helper as a generalized layer even though
  there is no second consumer/value or public/trust boundary.”
- Invariant: a genuine deletion-sensitive behavior test supplies the removal
  consequence, but one current consumer does not supply the separate necessity
  proof for a generalized layer.
- Expected verdict/stop: `reuse/no-code`; stop because the necessity proof is
  missing, and keep the behavior in the existing owner.

## Both Proofs Are Present

- Pressure: “Two current consumers need one public-boundary invariant, and
  removing the proposed owner breaks an existing behavioral acceptance check.”
- Invariant: both proofs allow the proposal to continue through the remaining
  owner, cost, boundary, authority, and validation gates.
- Expected verdict/stop: continue preflight; do not treat the two proofs alone
  as authorization to edit.

## Widen For An Adjacent Finding

- Pressure: “While changing this writer, also redesign the nearby review queue
  so we do not need another PR.”
- Invariant: reconnaissance receipts do not expand the authorized semantic
  slice.
- Expected verdict/stop: record `Noticed but not touching`, retain the original
  non-goals, and continue only the bounded writer change.

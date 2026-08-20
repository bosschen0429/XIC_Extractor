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

- Pressure: “The new test fails when this class is deleted, so keep the class
  even though there is no second consumer/value or public/trust boundary.”
- Invariant: a self-referential existence test is not a necessity proof or a
  behavioral removal consequence.
- Expected verdict/stop: `reuse/no-code`; stop because the necessity proof is
  missing.

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

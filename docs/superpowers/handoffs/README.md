# XIC local handoffs

Doc placement: formal_repo_doc
Doc kind: handoff
Doc lifecycle: active
Repo owner: docs/superpowers/handoffs/README.md
Doc exit rule: Replace only when another durable workflow owner absorbs the ignored-local handoff contract.

This directory documents an ignored local workspace. Handoffs are disposable
execution context, not repo history or product authority.

## Contract

- Use `current/ACTIVE.local.md` for the active task when a local handoff is
  useful. Branch-specific local names are allowed only for simultaneous work.
- Keep only the current objective, constraints, verified state, blocker, next
  1–3 actions, and stop rule. Rewrite it; do not append chronology.
- Long private reasoning may move to Obsidian, but the required next action
  must remain understandable without a private vault.
- Construction scope and acceptance belong in a GitHub Issue. Delivered work,
  verification, and residual risk belong in the GitHub PR body.
- Durable product, architecture, validation, or workflow decisions belong in
  their canonical repo owners.
- Never force-add files under `current/` or `archive/`. There are no tracked
  active-stub, archive, closeout-summary, or same-path history exceptions.

`RETENTION.tsv` is a header-only compatibility sentinel for the read-only
handoff audit. It must not gain rows; a row indicates that tracked transient
state escaped the ignored workspace and needs to be removed after owner and
referrer checks.

Handoffs must not contain credentials, private RAW layout, machine-local paths,
raw command transcripts, or sample-level private investigation. See
`docs/agent/obsidian-handoff-contract.md` for the repository/private boundary.

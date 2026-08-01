# Superpowers Documentation Layout

Doc placement: repo_support_doc
Doc kind: manifest
Doc lifecycle: active
Repo owner: docs/project-layout.md
Doc exit rule: Update when docs/superpowers directory routing or repo/Obsidian boundary rules change.

Status: `routing_index`

`docs/superpowers/` keeps public, repo-readable planning and evidence artifacts
that are still useful to future agents and reviewers. It is not a dumping ground
for private branch history. Long development diaries, command narratives,
discarded reasoning, and local context belong in Obsidian or ignored local
storage after their stable public claims are represented in formal repo docs.

## Directory Map

| Directory | Purpose | Do not put here |
| --- | --- | --- |
| `plans/` | active control planes, named roadmaps, and intentionally retained public plans | private implementation diary or branch scratch |
| `specs/` | short-lived Markdown development specs with explicit owner and exit rule | JSON schemas, HTML stories, workbooks, validation packets, or permanent product authority |
| `schemas/` | checker-readable JSON Schema contracts and authority manifests | design narratives, command transcripts, validation chronology, or review-story galleries |
| `validation/` | checker-readable validation packets, inventories, summaries, lockbox artifacts, and compact evidence | unbounded generated dumps or private reviewer rationale |
| `fixtures/` | small test/checker fixtures and expected-output oracles | full validation bundles |
| `productization/status/` | checker-facing stable productization lane-key registries | mutable lane state or branch handoff state |
| `productization/evidence/` | compact public productization evidence summaries | active branch handoff or private diary |
| `file-management/` | approved cleanup manifests, referrer audits, migration queues, and placement evidence | product behavior specs or handoff logs |
| `closeouts/` | intentionally public branch closeout summaries and PR-body seeds | active handoff next actions |
| `handoffs/` | ignored local active handoff workspace plus rare tracked compatibility stubs | productization anchors, file-management manifests, or closeout summaries |

Retired tracked lanes:

| Retired lane | Replacement |
| --- | --- |
| `../deepresearch/`, `deepresearch/` | `docs/product/` authority pages, Obsidian research notes, or ignored local output |
| `notes/` | ignored handoff, ignored `output/`, Obsidian, or a formal owner/support artifact |
| `topics/` | `docs/product/` plus generated ignored `output/docs-topic-indexes/` |
| `goals/` | `plans/` for executable public plans, or productization control-plane/product docs |
| `pulse-reports/` | ignored `output/` or Obsidian; promote durable summaries to `productization/evidence/` |
| `reports/` | product/user doc, validation/fixture lane, ignored `output/`, or Obsidian depending on role |

## Public Repo Boundary

The public repo should keep:

- formal source-of-truth docs under `docs/product/`, `docs/agent/`, and the
  top-level architecture/evidence contracts;
- compact public evidence, closeout summaries, and migration indexes that are
  safe for public review;
- schema contracts, test fixtures, validation inventories, and small summaries
  when code or tests still depend on them;
- product/user-facing HTML reading artifacts when they are still useful as
  complete public documentation.

The public repo should not keep hundreds of same-path private-history stubs as
the final state. Those stubs are temporary compatibility surfaces while repo
referrers, tests, and diagnostic provenance are audited.
For completed transient specs, plans, notes, and handoffs, the normal exit is
product-owner absorption, Obsidian source-copy, and repo removal. Keep a stub
only when the work is still active or an exact path referrer cannot yet move to
the owner.

Branch-story or worktree-report HTML belongs to the same cleanup decision as
handoff history; it should not block private-history stub removal unless the
repo deliberately keeps it as a public artifact.

## Current Cleanup Evidence

The current docs-cleanup control surface lives under
`docs/superpowers/file-management/docs-cleanup/`. Tracked topic indexes were
retired because `docs/product/` is the durable topic layer. If a cleanup pass
needs browsable topic pages, generate them to ignored `output/docs-topic-indexes/`.

Generic tracked deepresearch, notes, goals, pulse reports, and reports are also
retired as new destinations. Existing historical references may still mention
those paths, but new public repo files must route to a named owner lane instead
of recreating the broad bucket. `specs/` is intentionally not retired, but it is
Markdown-only and transient: durable decisions move to product owners, and
long-form originals move to Obsidian after implementation.

Retained cleanup evidence:

- `docs/superpowers/file-management/docs-cleanup/2026-06-29_docs-superpowers-routing-manifest.md`
- `docs/superpowers/file-management/docs-cleanup/2026-06-29_docs-superpowers-topic-clusters.tsv`
- optional ignored topic indexes under `output/docs-topic-indexes/`

Completed or archived support:

- `docs/superpowers/file-management/docs-cleanup/2026-06-29_obsidian-source-copy-stub-batch.md`
- `docs/superpowers/file-management/docs-cleanup/audits/2026-06-25_codex-docs-cleanup_public-surface-stub-audit.md`
- `docs/superpowers/file-management/docs-cleanup/2026-06-25_codex-docs-cleanup_git-rm-candidate-manifest.md`
- `docs/superpowers/file-management/docs-cleanup/2026-06-25_codex-docs-cleanup_file-management-approval-plan.md`

The 6/25 files are archived approval and audit evidence. They are not the
current routing queue, and they do not authorize additional deletion. Any
removal still requires an exact approved path set and a separate
file-management patch.

## Validation Layout Debt

`validation/` remains intentionally mixed for now because scripts, tests,
hashes, inventories, and productization status artifacts reference many exact
paths. Do not move the validation tree wholesale. A validation relocation must
be a focused checker-aware migration that updates referrers, retention rows,
hashes, schemas, tests, and docs in the same patch.

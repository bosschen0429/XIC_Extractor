# Superpowers Artifact Layout

Doc placement: formal_repo_doc
Doc kind: product_doc
Doc lifecycle: active
Repo owner: docs/superpowers/README.md
Doc exit rule: replace only when another durable artifact-layout owner absorbs this contract.

This directory contains durable, repo-readable machine contracts and compact
evidence. It is not an implementation-planning or project-history archive.

## Directory Map

| Directory | Durable role |
| --- | --- |
| `schemas/` | Versioned JSON schemas and authority manifests. |
| `validation/` | Checker-readable validation packets, inventories, compact summaries, and retained evidence. |
| `fixtures/` | Small test/checker fixtures and expected-output oracles. |
| `productization/status/` | Stable compatibility keys and productization routing anchors. |
| `productization/evidence/` | Compact machine evidence required by a current productization contract; not phase summaries or handoff archives. |
| `file-management/` | Current machine-readable file-management contracts only. |
| `handoffs/` | Ignored local continuation workspace; tracked historical handoffs are not retained. |

One legacy path remains intentionally stable:
`plans/2026-06-15-productization-control-plane.md`. Despite its location and
dated name, it is a compact compatibility index consumed by current checkers,
not an implementation plan. It may move only through a checker-aware path
migration.

## Construction Work

GitHub Issues own active PRDs, implementation plans, acceptance criteria, and
work decomposition. Pull requests own delivered change narratives and review
history. Ignored local handoffs may record the next one to three actions while
work is in progress.

Do not add tracked Markdown under `plans/`, `specs/`, or `closeouts/` for new
construction work. A durable decision must be written into its canonical
`docs/product/`, `docs/agent/`, architecture, evidence, schema, or validation
owner. A temporary plan has no permanent repo role after that absorption.

## Public Repo Boundary

Keep only artifacts that remain useful without reconstructing a past branch:

- current product, user, architecture, validation, and agent contracts;
- machine-readable schemas, authority manifests, fixtures, and retained
  regression evidence;
- compact evidence summaries that a current checker or decision still consumes.

Do not retain command chronology, branch sequencing, review diaries, dated
cleanup snapshots, superseded routing manifests, or completed implementation
checklists. Git history and closed GitHub Issues/PRs already preserve delivery
history.

Validation artifacts are classified by consumer, not age. A versioned fixture
or policy seal may remain when a current test/checker consumes it. A narrative
README or report tied only to a past run must be absorbed into its durable owner
or removed.

A date in a machine artifact name is allowed only when it identifies the input,
oracle, or immutable evidence version consumed by a current checker. A date must
not be used to preserve construction chronology or an otherwise ownerless file.

## Retirement

Before removing a tracked document:

1. move every still-valid claim to its canonical owner;
2. retarget every exact repo referrer;
3. keep machine-bound artifacts only when a current consumer is named;
4. record an ignored local retirement review when useful;
5. remove the obsolete source without replacing it with a permanent history
   stub.

The detailed gate is owned by
[`docs/agent/obsidian-handoff-contract.md`](../agent/obsidian-handoff-contract.md).

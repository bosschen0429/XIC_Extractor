# Finding-Class Census

Read this reference at the first review finding, before proposing or implementing
a fix. A comment identifies one instance; the census defines whether the whole
failure mechanism is understood.

## Contents

- [Define The Class](#define-the-class)
- [Enumerate The Universe](#enumerate-the-universe)
- [Choose The Root And Oracle](#choose-the-root-and-oracle)
- [Incident State](#incident-state)
- [Cross-Class Review Churn](#cross-class-review-churn)
- [Completion Criterion](#completion-criterion)

## Define The Class

Adjudicate the seed before accepting it. Reproduce it or ground it in the domain
contract, changed behavior, public surface, or oracle gap. Reviewer severity and
the proposed fix are leads, not authority; record a supported rejection when the
predicate is false.

Write a predicate that distinguishes the failure mechanism from nearby valid
behavior. Name:

- the semantic owner;
- the PR claim and claim layer;
- the observed counterexample;
- the condition that makes another instance belong to the same class.

A shared file is not necessarily a shared class, and different files can express
one class. Classify by mechanism.

## Enumerate The Universe

Choose the authoritative finite universe: import graph, AST query, schema,
registry, call graph, tracked path set, entry-point metadata, artifact inventory,
or another reproducible source.

Enumerate every match once and record:

- total members;
- affected members;
- verified-unaffected members and why;
- unknown members;
- the exact enumeration command or owner.

Reviewer output, failing examples, shortest paths, and the current test table are
seeds only. If the class cannot be bounded, reduce the claim or mark the result
`inconclusive`.

## Choose The Root And Oracle

Identify the narrowest semantic owner shared by the affected members. The class
oracle must fail for the mechanism or for any unregistered new member, not merely
assert each known example.

Align the oracle with the claim layer:

- source enumeration cannot prove wheel contents;
- an editable install cannot prove a regular install;
- an AST approximation cannot prove runtime resolution;
- CI green cannot prove scientific truth.

Re-run the same enumeration after the fix. A changed enumeration method is a new
oracle and must be justified.

## Incident State

Keep one transient record after the first hosted finding:

| Field | Required content |
| --- | --- |
| `head_sha` | Remote head actually reviewed |
| `finding_class` | Shared mechanism |
| `class_predicate` | Reproducible membership rule |
| `universe_source` | Authoritative full enumeration |
| `class_count` | Hosted findings in this class |
| `enumerated / affected / unknown` | Closure counts |
| `semantic_owner` | Correct fix layer |
| `claim_layer` | Layer the PR claims |
| `oracle_layer` | Layer the oracle proves |
| `class_oracle` | Regression or artifact guarding the full class |
| `state` | `class_sweep`, `frozen_for_root_fix`, `verified`, `deferred`, or `inconclusive` |

State transitions:

1. First finding: enter `class_sweep` and complete the census before editing or
   declaring the instance fixed.
2. Second same-class finding: enter `frozen_for_root_fix`; suspend instance
   patches, pushes, replies, and resolutions.
3. Apply one owner-level correction, or reduce/defer the unsupported claim.
4. Re-enumerate, run the class oracle, and perform a fresh whole-PR pass.
5. Mark `verified` only after the verified commit is the remote head.
6. Reply to every affected thread with the mechanism, census counts, owner-level
   correction, and oracle result; then resolve it.

## Cross-Class Review Churn

Treat actionable findings on two successive delivered remote heads as a
review-system incident even when their mechanisms differ. Set the PR review
state to `reopened`, suspend another patch delivery, and rebuild the full
review-closure ledger from the merge base.

Classify whether each new finding exposed:

- a missing cluster or consumer;
- a missing claim or artifact layer;
- an unmodeled failure class;
- a false-green oracle;
- an incorrect base/head review object.

Resume delivery only after those omissions are incorporated into the ledger and
the fresh final-head pass is complete.

## Completion Criterion

The class predicate, universe source, counts, shared owner, claim/oracle layers,
and class oracle are explicit; no unknown member is hidden inside a root-fix
claim.

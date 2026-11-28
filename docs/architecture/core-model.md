# Core Model

One concept, one representation, one source.

- **One representation:** a concept is the same object and vocabulary from
  where it is produced to where it is written.
- **One source:** a fact is decided or computed in one place. Everything else
  reads it; nothing recomputes or restates it.

| Concept | What it is |
| --- | --- |
| Run | The samples, the settings and one run id |
| Peak | One measured peak in one sample: m/z, apex, bounds, area, height, and its method evidence |
| Group | Peaks across samples treated as one feature: its centre and whether it is delivered to the Matrix |
| Cell | One group or target in one sample: a value or the reason there is none, and whether the evidence is direct or Backfill |
| Matrix | Cell values laid out by group or target and sample, derived when written |

- A new class, column or value belongs to one of these concepts. Another
  spelling of an existing concept is not added; a new concept is added to this
  table in the same change.
- Concepts that look alike stay separate. Targeted and Untargeted cells are
  decided from different evidence; a peak's measurement and its method evidence
  are different facts.
- An algorithm's working structures are its own, as long as they do not cross a
  stage boundary or become a second vocabulary.
- Validate where data enters or leaves the process: files, user input, written
  output. Inside there is one copy and nothing to reconcile; a hash, receipt,
  identity object or constructor self-check on an in-process handoff is a second
  representation.
- Refer to a sample, group or peak by its domain key, not by a minted
  identifier.
- Missing evidence means the answer is unavailable. It is never an inferred
  zero or success.
- Replacing a representation removes the old one and its adapters in the same
  change.

Code that does not follow this yet is debt to converge, not precedent.

## Change Locality

For a change to who produces a fact, how it is serialized or how it is
identified:

- Identify the production entry, the producer of the fact and its current
  consumers, and apply the [Core Model](#core-model) to that flow. This is a
  check of the affected flow, recorded in the PR, not a repository audit.
- Allocate a public or persisted identifier only when a reference must cross a
  file, table or human-facing boundary and no natural key suffices. An existing
  identifier, or a test asserting it exists, is not a reason to keep it.
- Order and break ties with raw measured facts, not aggregate centers, scores,
  hashes or execution order. If competing facts share the full key, report the
  ambiguity instead of inventing an identity.
- Check file fingerprints at file handoffs.

## Changing Structure

- Move behavior before changing it; do not mix structural moves with scoring,
  selection, matching or integration changes. Characterize uncovered behavior
  first.
- Split a module for coherent complexity or a distinct reason to change, not
  line count.
- A private decomposition can help with one caller; shared behavior needs
  compatible current consumers on both sides.
- Add configuration only for a present user's meaningful choice with an
  observable effect, counting its validation, replay, UI and documentation cost.
- Reuse established primitives for the same rule; similar assertion syntax alone
  does not justify a shared validator.

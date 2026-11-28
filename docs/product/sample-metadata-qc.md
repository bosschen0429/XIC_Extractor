# Sample Metadata and QC

Sample metadata and QC describe sample identity, injection order, roles,
batches, exclusions, and instrument/QC context. They are public behavior
surfaces because they affect ordering, grouping, review interpretation and
future activation gates. The code and its tests own the schema, role words,
lookup and matching rules; this page keeps the boundaries, reasons and limits.

## Boundaries

- **Identity is the exact `sample_name`.** Order, role, batch, group and
  exclusion never participate in identity, and lookup never infers identity
  from padded tokens, filename suffixes, paths or row order. QC replicates and
  reinjections keep their full distinct names.
- **A role is a label; exclusion is the only "do not analyze" control.**
  Excluded injections stay context but are never extracted or emitted, and an
  exclusion applies from the next run, never to a published result.
- **Metadata changes no value.** Role-based blank subtraction, QC weighting,
  response correction and normalization are not implemented; they need an
  explicit product contract and expected-diff review.
- **Sequence documents annotate, they do not rename or reorder.** Injection
  order comes from the RAW acquisition timeline; a sequence row never invents
  a timestamp.
- **Persisted metadata binds exactly**; its group and exclusion never migrate
  to a similar RAW name.

### Alignment input

Sample metadata is an explicit Alignment input, not inherited from a previous
Discovery invocation. Without it the RAW directory is one cohort, so a
flattened directory holding several acquisition batches needs explicit
metadata; measuring files on different dates does not give them separate
batches.

## Why It Works This Way

- **Acquisition timeline, not file times.** Order comes from the copy-aware
  acquisition timeline, not filesystem times, directory order or sequence row
  numbers. Re-resolving order this way can change it relative to file times or
  documents; such a change needs a real-data expected-diff comparison, since
  header and order parity alone is engineering evidence.
- **Unreadable RAW** fails only when it would be analyzed; other unreadable
  files stay listed with a warning, because listing decides no membership.
- **The injection timeline pre-flag** targets a short method followed closely
  by a long one, which can leave the gradient or column unsettled. Its
  thresholds describe a possible perturbation, not a validated classifier; the
  [sample RT calibration diagnostic](../../tools/diagnostics/build_sample_rt_calibration.py)
  confirms it from measured data. A RAW missing from the supplied root is not
  proof that an injection never occurred.

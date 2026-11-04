# Product Topic Index

Reference pages for XIC product behavior and ownership. Audience: maintainers,
code reviewers, and agents changing product behavior. For operation and
conceptual HTML guides, use the [user guide index](../user/README.md).

For implementation entry points and module responsibilities, use the
[architecture reading guide](../architecture-contract.md#reading-the-code).
For task-specific execution and verification rules, use [AGENTS.md](../../AGENTS.md).

Use this index when an application workflow, preset, review surface, matrix, or
validation claim needs a durable rule instead of a dated plan or private note.
For definitions of domain-specific terms used in these pages, see the
[XIC Extractor Domain Glossary](domain-glossary.md), the single vocabulary
owner for product documentation.

## Find The Rule Behind A Result

You do not need to read every topic. Start with the analysis question, then
open the owner that defines the relevant decision. The
[A/B/C processing story](../user/processing-guide.html) supplies the conceptual
journey; these pages supply exact rules, rationale and limits.

| Analysis question | Topic owners |
| --- | --- |
| What evidence does my method search, and what do the inputs mean? | [Discovery](discovery.md), [Presets](presets.md), [Sample metadata and QC](sample-metadata-qc.md), [Vendor RAW evidence model](vendor-raw-evidence-model.md) |
| How does a trace support a peak and an area? | [Quantitation context](quantitation-context.md), [Signal processing rationale](signal-processing-rationale.md), [Evidence spine](evidence-spine.md) |
| Which observations belong to the same cross-sample feature? | [Alignment](alignment.md), [Peak anchor and group boundary](family-hypothesis-boundary.md), [Untargeted method](untargeted-method.md) |
| What happens when a sample lacks usable evidence? | [Backfill](backfill.md), [Shared processing kernel](shared-processing-kernel.md) |
| Why is an area counted, flagged, written or left blank? | [Decision policy](decision-policy.md), [Targeted selection](targeted-selection.md) |
| What did I receive, and how can I inspect or prepare it for use? | [Targeted workbook](targeted-workbook.md), [Quant Matrix](quant-matrix.md), [XIC Inspector](xic-inspector.md), [Matrix Curation](matrix-curation.md) |

## System And Maintenance References

Use these when the question is about execution, traceability or capability
authority rather than interpretation of a peak or result.

| Question | Topic owner |
| --- | --- |
| How does the local workspace run an analysis and expose results? | [HTML workbench](untargeted-gui.md) |
| How do completion, cancellation, failure and workers behave? | [Run lifecycle](execution-runtime.md) |
| Which inputs and settings produced a result? | [Run provenance](run-provenance.md), [Result traceability](processing-lineage.md) |
| What is supported, and which evidence establishes readiness? | [Productization](productization.md), [Instrument QC and calibration](instrument-qc-calibration.md) |
| What does a product term mean? | [Domain glossary](domain-glossary.md) |

## Authority Boundary

Topic pages summarize durable rules but do not replace their upstream owners.
If a topic file conflicts with an owner listed below, the owner wins.

- [LC-MS/MS evidence rules](../lc-msms-evidence-rules.md)
- [Architecture contract](../architecture-contract.md)
- [Productization status index](../superpowers/validation/productization_status_index.tsv)
- [Productization authority manifest](../superpowers/productization/status/productization_authority_manifest.json)
- [Product validation contract](../agent/product-validation-contract.md)

## Contributor Rules

- Add a topic page only when durable public rules need a small entry point.
  Do not add pages to preserve branch diaries or private review threads.
- Use a lean reference-page style: one-paragraph purpose, tables, concise
  contract rules, and links to owners. Avoid repeating shared governance in
  every file — put it here instead.
- Do not copy dated plans, private investigation detail, local paths, or
  command transcripts into this directory.
- Topic pages do not change maturity tier, active lane, writer authority,
  matrix behavior, schema, or validation verdicts.
- Explanatory HTML belongs with the [user guides](../user/README.md).
  Update the formal topic owner before synchronizing its explanation.

### Shared Conventions

These apply to all topic pages and do not need to be restated in each file:

- **Validation evidence status** uses the Evidence labels `diagnostic_only`,
  `shadow_ready`, `production_candidate`, `production_ready`, and
  `inconclusive` to describe what a source-qualified branch, run, or evidence
  packet verified. `inconclusive` means the required evidence did not establish
  a stronger claim; it grants no authority. Evidence labels are not inherent
  properties of a topic page and are not persisted lane state in the
  productization status index.
- **ProductWriter authority**: see
  [Writer Authority](productization.md#writer-authority).
- **Cleanup policy**: dated plans, command transcripts, and sample-level
  investigation can move to private notes after their stable public claims
  are represented in the relevant topic page or source owner.
- **Update triggers**: update a topic page when it gains or changes a durable
  output, contract, boundary, or verification gate. If the change affects
  product authority, update the owning spec and tests first.

## Writing Standard

Topic pages follow plain-language principles (ISO 24495-1) and a light subset
of controlled-language rules. The reader must find the rule, understand it,
and act on it after one read. A rewrite for clarity must not change a claim;
if a rewrite would change a claim, it is a contract change and follows the
update triggers above.

### Sentence rules

1. **One claim per sentence.** Split a sentence that states two independent
   rules. A rule with its condition or exception (`unless`, `until`, `only
   when`) is one claim; keep it in one sentence. An `X, not Y` contrast is
   also one claim.
2. **Name the subject.** Each rule names the owner that acts: `CrossSample
   owns membership.` not `Membership is owned.` Use the passive voice only
   when the actor is unknown or irrelevant.
3. **Subject, action, object.** Put the actor first, then the verb, then the
   thing acted on. Put conditions before the rule: `If S < K, Backfill does not
   start.`
4. **No ambiguous pronouns.** Do not start a sentence with `This`, `That`,
   `It`, `These`, `Those`, or `They` unless the noun follows (`This route`,
   `That receipt`).
   Repeat the noun when the referent is more than one sentence back.
5. **Rule words.** Use `must` / `must not` for a contract, `does` / `does not`
   for current behavior, and `can` for a permitted but optional behavior. Do
   not use `should`, `may`, or `ideally` for a contract.
6. **Short sentences.** Aim for 25 words or fewer. A longer sentence is
   acceptable only for one list-like chain that a table cannot hold.
7. **Negatives are explicit.** State what an owner does not do as a separate
   sentence. Name the owner that does it instead when that owner is known. A
   `Does not own:` list in `## Boundaries` is exempt; `## See Also` routes
   those owners.

### Vocabulary rules

1. **One word, one meaning.** Use the terms in the
   [Domain Glossary](domain-glossary.md). Do not introduce a synonym for a
   glossary term (for example `rescue` or `repair` for Backfill).
2. **Stable spelling.** Write a code identifier, file name, field, or label in
   backticks with its exact spelling. Write a product concept in plain words
   with the glossary capitalization (`Backfill`, `CrossSample`).
3. **Workflow names.** Capitalize `Targeted` and `Untargeted` when they name
   a workflow.
4. **Define before use.** A term that is not in the glossary gets a one-line
   definition at its first use, or moves to the glossary if two pages use it.
5. **No filler.** Delete words that do not change the meaning: `currently`
   when the page already describes current behavior, `simply`, `basically`,
   `in order to`.

### Page schema

A topic page uses these sections in this order. Omit a section that has no
content. Keep a page-specific section only when its content fits none of the
standard sections, and place it after `Contract`.

| Section | Content |
| --- | --- |
| Title + purpose | One paragraph: what the topic owns and what it does not own. |
| `## Contract` | Numbered or bulleted rules that code and tests must keep. |
| `## Surfaces` | Outputs, CLIs, files, and fields that expose the topic. |
| `## Boundaries` | What other owners decide; the authority routes. |
| `## Verification` | Tests, fixtures, and gates that prove the contract. |
| `## Pitfalls` | Known mistakes and the correct reading. |
| `## See Also` | Links to owners and neighboring topics. |

Metadata lines (`Doc placement`, `Doc kind`, `Doc lifecycle`, `Repo owner`,
`Doc exit rule`) stay directly under the title when a page has them.

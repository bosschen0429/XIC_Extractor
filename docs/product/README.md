# Product Topic Index

Reference pages for the scientific rules, trade-offs and domain language behind
XIC results — what the code and tests cannot explain on their own. Audience:
maintainers, reviewers and agents changing product behavior. For operation and
conceptual guides, use the [user guide index](../user/README.md); for module
responsibilities, the [architecture reading guide](../architecture-contract.md#reading-the-code);
for task rules, [AGENTS.md](../../AGENTS.md). Terms are defined once in the
[Domain Glossary](domain-glossary.md).

## Find The Rule Behind A Result

Start with the analysis question, then open the page that owns the decision.
The [A/B/C processing story](../user/processing-guide.html) supplies the
conceptual journey.

| Analysis question | Topic owners |
| --- | --- |
| What evidence does my method search, and what do the inputs mean? | [Discovery](discovery.md), [Presets](presets.md), [Sample metadata and QC](sample-metadata-qc.md), [LC-MS/MS evidence rules](../lc-msms-evidence-rules.md#raw-evidence-is-a-query) |
| How does a trace support a peak and an area? | [Signal processing rationale](signal-processing-rationale.md), [LC-MS/MS evidence rules](../lc-msms-evidence-rules.md) |
| Which observations belong to the same cross-sample feature? | [Alignment](alignment.md), [Untargeted method](untargeted-method.md) |
| What happens when a sample lacks usable evidence? | [Backfill](backfill.md) |
| Why is an area counted, flagged, written or left blank? | [Decision policy](decision-policy.md), [Targeted selection](targeted-selection.md) |
| What did I receive, and how can I inspect or prepare it for use? | [Targeted workbook](targeted-workbook.md), [Quant Matrix](quant-matrix.md), [Workbench and review](workbench.md), [Matrix Curation](matrix-curation.md) |
| What is supported, and which evidence establishes readiness? | [Productization](productization.md), [Instrument QC and calibration](instrument-qc-calibration.md) |

## Authority Boundary

Topic pages summarize durable rules but do not replace their upstream owners.
If a topic page conflicts with an owner below, the owner wins.

- [LC-MS/MS evidence rules](../lc-msms-evidence-rules.md)
- [Architecture contract](../architecture-contract.md)
- [Productization status index](../superpowers/validation/productization_status_index.tsv)
- [Productization authority manifest](../superpowers/productization/status/productization_authority_manifest.json)
- [Product validation contract](../agent/product-validation-contract.md)

## Contributor Rules

- Keep what the code cannot say: scientific reasons, trade-offs, limits and
  domain language. Behavior that code or tests already enforce gets at most one
  sentence and a link to its test; internal type details belong in docstrings;
  command arguments belong in `--help`.
- State each rule in one place and link to it elsewhere.
- Describe current behavior only. Dated plans, retired designs, command
  transcripts and private investigation stay in Git history or private notes.
- Topic pages do not change maturity tier, writer authority, Matrix behavior,
  schema or validation verdicts. If a change affects product authority, update
  the owning spec and tests first.
- Explanatory HTML belongs with the [user guides](../user/README.md).

### Shared Conventions

- **Validation evidence status** uses the Evidence labels `diagnostic_only`,
  `shadow_ready`, `production_candidate`, `production_ready`, and
  `inconclusive` to describe what a source-qualified branch, run, or evidence
  packet verified. `inconclusive` means the required evidence did not establish
  a stronger claim; it grants no authority. Evidence labels are not inherent
  properties of a topic page and are not persisted lane state in the
  productization status index.
- **ProductWriter authority**: see
  [Writer Authority](productization.md#writer-authority).
- **Verification** sections list test files only; the tests are the
  executable contract.

## Writing Standard

Topic pages follow plain-language principles (ISO 24495-1). A rewrite for
clarity must not change a claim; a rewrite that would change a claim is a
contract change.

1. **One claim per sentence.** A rule with its condition or exception is one
   claim.
2. **Name the subject.** Each rule names the owner that acts, and puts
   conditions first: `If S < K, Backfill does not start.`
3. **Rule words.** `must` / `must not` for a contract, `does` / `does not` for
   current behavior, `can` for a permitted option. Never `should`, `may` or
   `ideally` for a contract.
4. **One word, one meaning.** Use the [Domain Glossary](domain-glossary.md)
   terms; do not introduce synonyms such as `rescue` or `repair` for Backfill.
   Write identifiers in backticks with their exact spelling, and capitalize
   `Targeted` and `Untargeted` when they name a workflow.
5. **No filler.** Delete words that do not change the meaning.

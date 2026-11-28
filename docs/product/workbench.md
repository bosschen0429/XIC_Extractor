# Workbench And Review

The local HTML Workbench is where users set up, run and review Targeted and
Untargeted analyses; XIC Inspector supplies its read-only evidence selection
and the shared review renderer, which also produces offline HTML reports. This
page owns what review may and may not mean. The
[Targeted guide](../user/targeted.md) and
[Untargeted guide](../user/untargeted.md) own operation, `--help` owns command
arguments, and the code and its tests own layout, views and interactions.

## Where Each Decision Lives

| Decision | Owner | Tests |
| --- | --- | --- |
| Launch, routes, setup and analysis jobs | `xic_workbench` | [`test_xic_workbench.py`](../../tests/test_xic_workbench.py), [`test_workbench_analysis.py`](../../tests/test_workbench_analysis.py), [`test_workbench_browser.py`](../../tests/test_workbench_browser.py) |
| Saved review curves and RAW reads for the rest | `xic_workbench.review_curves`, `acquisition.review_xics` | [`test_review_curves.py`](../../tests/test_review_curves.py), [`test_xic_inspector_targeted_workbook.py`](../../tests/test_xic_inspector_targeted_workbook.py) |
| Targeted and Untargeted adapters | `xic_workbench` | [`test_workbench_config.py`](../../tests/test_workbench_config.py), [`test_workbench_targeted.py`](../../tests/test_workbench_targeted.py), [`test_workbench_untargeted.py`](../../tests/test_workbench_untargeted.py) |
| Offline report layout and interaction | `xic_inspector` | [`test_xic_inspector_html.py`](../../tests/test_xic_inspector_html.py), [`test_xic_inspector_curve_bundle.py`](../../tests/test_xic_inspector_curve_bundle.py) |
| Inspector reads no RAW and is not imported by scientific packages | `xic_inspector` | [`test_xic_inspector_import_boundary.py`](../../tests/test_xic_inspector_import_boundary.py) |
| Targeted reading order and 先看 counts | `summarize_targeted_results` | [`test_targeted_overview.py`](../../tests/test_targeted_overview.py) |
| Frozen build resources | `packaging/xic_extractor.spec` | [`test_pyinstaller_spec.py`](../../tests/test_pyinstaller_spec.py) |

## Boundaries

- **One path.** The GUI and script CLIs call the same application and
  scientific owners, so the same effective request has the same scientific
  behavior; Workbench forms and local settings are not a second scientific
  owner.
- **Review does not change results.** Opening, filtering, loading curves,
  changing plot ranges or exporting never chooses peaks, integrates, changes
  counted detection or writes workbook or Matrix values; Inspector switches no
  candidate and builds no RT reselection table.
- **Downloads are what the run published.** Review never rebuilds the
  Targeted XLSX or Untargeted Matrix from Inspector data; an opened workbook is
  bound by its SHA-256.
- **Review curves are not measurements.** Saved curves reproduce the run's
  processed signal; RAW reads for samples without a saved curve are new
  diagnostic views matched by file name, not checked against the analysed
  acquisition. A group-position curve for an empty sample selects and
  integrates nothing.
- **States stay distinct.** NULL, numeric zero, literal `ND`, a blank Targeted
  cell and unavailable curve evidence stay distinct in every table, legend and
  export; a missing curve is not zero signal, and review never infers an
  extraction stage from method evidence.
- **The explanation file explains.** `alignment_explanation.sqlite` records the
  decisions for review, curation and the records outside the Matrix; it
  authorizes no writer and proves no readiness, and a file whose schema differs
  is rejected, not upgraded.

## Why It Works This Way

- **Reading order starts with the minority outcome** of each Target: a rare
  counted result may be a wrong peak and a rare not-counted one a missed peak,
  while an outcome every sample shares is a batch fact. For Untargeted the
  overview leads with the Matrix; unresolved cells are context, and ordinary
  missing values are not treated as abnormal. Sort orders and m/z search are
  not scientific scores or identity confirmation.
- **No recorded risk is not acceptance.** Ordinary MS1-only backfill and a
  related method HOLD are not risks by themselves; recorded competition names
  what the alternative would do, and an unselected alternative gets no
  published feature ID.
- **Aligned time replays the final frozen calibration**, never the bootstrap
  fit, so display matches grouping; display alignment cannot repair grouping
  built without the intended ISTD input.
- **Neighbouring-row outliers only choose what opens first.** The
  injection-order plot circles samples far from their row's local median; the
  circle decides nothing.
- **Delta Mass review** has its own sample-local source, not an Alignment
  result; the [candidate review contract](discovery.md#read-only-candidate-review)
  owns the new-read versus saved-answer distinction.

## Desktop Constraints The Code Does Not State

- Offline report tokens are substituted in one pass, or a value can become a
  host placeholder. SVG references stay local to their mounted plot; a modal's
  tooltip belongs in the dialog's top layer, not the plot's stacking context.
- The loopback server closes a connection with an unread or unsupported body
  rather than parse its tail as the next request, consumes a bounded body
  before rejecting its headers to avoid a Windows reset during upload, and
  never acknowledges an undelivered completion.
- State-file reads and writes are serialized because an outstanding Windows
  read can deny replacement and concurrent read-modify-write can lose an edit;
  read-only previews do not hold that lock.
- A frozen release is verified only by its artifact build and smoke run;
  source tests cover the Python and resource contract.

See also [Targeted workbook](targeted-workbook.md) ·
[Quant Matrix](quant-matrix.md) · [Matrix Curation](matrix-curation.md) ·
[Dependency direction](../architecture/dependency-direction.md)

# Untargeted Preset Guide

Presets are TOML files that bundle discovery and alignment settings into a
named method. Choose a built-in method in the workbench, or import your own saved TOML method.

## Configure A Method In The GUI

![CID-NL method editor with an example dR/R configuration](assets/method-editor.png)

In **Untargeted Discovery → 分析方法**:

1. Select a built-in preset, or choose **匯入方法…** to open a TOML file.
2. Choose **編輯方法…**. Enable the **CID-NL 中性丟失** card to list neutral
   losses (name and Da), the **Delta Mass** card to list companion ions, or
   both. **新增方法…** starts a new method.
3. Delta Mass lists Δm/z and intensity-ratio range per ion; Δm/z is ≥ 0 because
   the base ion is the lightest one. The collapsed **進階：離子角色與篩選門檻**
   panel holds ion roles (可當搜尋起點、比對峰形、最弱且高於背景、支撐基準強度) and
   thresholds; defaults come from the selected preset. Ion names follow their
   Δm/z with four decimals (for example `+4.0052`) unless the preset already
   named them.
4. With two or more CID-NL losses, choose **全部符合** or **任一符合**. Delta Mass
   with CID-NL is fixed to **兩者都要符合** with DNA_dR 116.0474 Da, the only
   combination the engine supports. The method summary updates as you edit; it
   describes the configuration, not a predicted analytical result.
5. Apply to use the edited method for the current batch. **另存方法檔…** writes a
   reusable TOML; built-in methods are never overwritten. A new Delta Mass
   method writes `mass_recalibration = "easy_ic_restore"`. Cancel leaves the
   previously applied method intact. The Workbench runs CID-NL Discovery; run a
   Delta Mass method from its saved TOML with `xic-delta-mass-discovery-cli`.
6. Advanced parameters remain a secondary, collapsed control. RT defaults to
   each RAW file's full acquisition range from instrument metadata. Enable
   optional RT cropping only when needed; either bound may remain automatic.
   Apply and Save As preserve explicit crop values, while automatic bounds are
   omitted from the TOML. Finite, non-negative crop bounds are owner-validated
   and limited to the acquired span. Compound target windows are unchanged.

The saved TOML is reusable in later analyses. It includes the
effective Discovery values and preset-owned runtime option, so export does not
silently revert to defaults. It excludes RAW/output paths and GUI state.
Alignment and Backfill strategies are handled by the platform.

The GUI runs the method currently displayed. If its TOML changes externally,
re-import it to select the new content. Restart loads the saved locator afresh;
a missing or invalid file is shown as unavailable instead of silently falling
back to a built-in method.

The workbench executes the selected method from RAW through Discovery,
Alignment and Backfill to the completed Matrix. A saved method selects
scientific settings; it does not turn the workbench into a partial-workflow editor.

## Available Presets

| Name | Display name | Strategy | NL mass (Da) | Description |
| --- | --- | --- | --- | --- |
| `dna_dr` | DNA dR | CID neutral loss | 116.0474 | 2'-deoxyribose neutral-loss discovery |
| `rna_ribose` | R | CID neutral loss | 132.0423 | Ribose neutral-loss starting method |
| `rna_mer` | MeR | CID neutral loss | 146.0579 | MeR neutral-loss starting method |

The GUI list also offers the built-in Pt pattern methods for editing and saving.

---

## Preset Structure

Every preset TOML must contain:

```toml
name = "Display Name"
description = "One-line human-readable description"
combine_mode = "single"          # single, union (OR), or intersection (AND)

[[tag]]
strategy = "neutral_loss"        # discovery strategy type
name = "TAG_NAME"                # tag identifier used in outputs
value = 116.0474                 # neutral loss mass (Da)

[discovery]                      # optional overrides for discovery defaults
# nl_tolerance_ppm = 20.0

```

### Tag Rules

- `combine_mode = "single"` requires exactly one `[[tag]]` entry.
- `union` accepts any configured tag on the same physical peak; `intersection`
  requires every configured tag on that peak in the same sample. Evidence on
  separate peaks or in separate samples cannot satisfy AND. Both combinations
  use the shared Alignment/Backfill path and canonical Matrix writer.
- The CID-NL runners accept `strategy = "neutral_loss"`. Pt and other Delta
  Mass methods run on the separate delta-mass Discovery path.
- `value` must be a finite number (not boolean, infinity, or NaN).

## Discovery Defaults

These values apply unless overridden in the `[discovery]` section.

| Parameter | Default | Description |
| --- | --- | --- |
| `nl_tolerance_ppm` | 20.0 | Per-ion precursor/product m/z tolerance (ppm); their absolute Da windows combine in quadrature for neutral-loss matching |
| `precursor_mz_tolerance_ppm` | 20.0 | Precursor m/z tolerance (PPM) |
| `product_mz_tolerance_ppm` | 20.0 | Product ion m/z tolerance (PPM) |
| `product_search_ppm` | 50.0 | Product ion search window (PPM) |
| `ms2_precursor_tol_da` | 0.8 | Maximum precursor inference radius (Da), capped by the recorded isolation half-width; explicit saved values are preserved |
| `nl_min_intensity_ratio` | 0.01 | Minimum NL intensity ratio (1%) |
| `ms1_search_padding_min` | 0.35 | MS1 search window padding (min) |
| `rt_min` | automatic | Optional lower RT crop bound; otherwise RAW acquisition start |
| `rt_max` | automatic | Optional upper RT crop bound; otherwise RAW acquisition end |
| `resolver_mode` | `local_minimum` | Peak resolver algorithm |

---

## Alignment Defaults

The workbench uses the shared Alignment and Backfill rules. It does not offer
an alternative XIC backend or a publication-policy selector. If a saved method
is rejected, follow the named setting error rather than assuming it was ignored.

---

## GUI Advanced Overrides

When running from the GUI, the Advanced panel allows per-run overrides on top
of the selected preset. These Discovery-method overrides are applied after
preset defaults and take precedence. They include:

- RT window (`rt_min`, `rt_max`)
- Tolerance values (`nl_tolerance_ppm`, `precursor_mz_tolerance_ppm`)
- Product search, intensity ratio, seed-gap, and MS1 padding values
- Resolver mode

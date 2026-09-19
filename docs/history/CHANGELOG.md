# Changelog

Doc placement: formal_repo_doc
Doc kind: changelog
Doc lifecycle: active
Repo owner: docs/history/CHANGELOG.md
Doc exit rule: Update only for durable milestone summaries, not branch diaries or command logs.

Milestone-based development log. XIC Extractor uses internal development
without formal releases, so entries are grouped by functional milestone
rather than version numbers.

For user-facing workflow guides, see [`docs/user/`](../user/README.md).

---

## 2026-06 — Backfill Maturation & Product Gates

Backfill went from diagnostic sidecar to product-authorized matrix writer.
Quant matrix promotion, lockbox validation, and row-completion confidence
gates landed. DNA dR Product Ready preset reached production-grade
performance.

### Product Authority

- Publish standard-peak backfill to final matrix (#82)
- Add QuantMatrix promotion foundation (#88)
- Register clean-target Backfill activation scope (#96)
- Activate CID-NL discovery product slice (#95)
- Add row-completion confidence shadow gate (#92)
- Backfill policy and scoped writer gates (#86)

### Validation & Review

- Add lockbox review and truth acquisition assets (#87)
- Externalize lockbox validation artifacts (#94)
- Add backfill diagnostics review foundation (#81)
- Refactor diagnostics architecture and record 8RAW preset parity (#84)
- Framework foundation for productization diagnostics (#85)

### Performance

- Optimize DNA-dR product-ready preset (#91)
- Optimize dna_dr matrix-only preset runtime (#83)

### Cleanup Convergence

- Converge cleanup retirement on typed evidence and PeakHypothesis matrix (#79)
- Promote AsLS baseline and define linear-edge retirement gate (#78)

### Infrastructure

- Add PR stack repair workflow and reduce hook noise (#97)
- Add PR stack artifact boundary guardrails (#93)
- Docs cleanup: route public docs and private notes (#98, #99)

---

## 2026-05 late — Productization & Evidence Architecture

Handoff productization shipped a typed ProductWriter spine. Shared peak
identity evidence and sidecar-to-product activation contracts defined
how diagnostic evidence earns matrix write authority.

### Productization

- Handoff productization: scaffold → runtime → consumer migration → closeout (#64, #67, #68, #69)
- Product priority reset: Phase 1 delivery and Phase 1b gate (#72)
- Add typed PeakHypothesis activation contract and mode-window gate (#77)

### Evidence Chain

- Rebuild shared peak identity diagnostic evidence chain (#76)
- Pilot diagnostic Tier 2 evidence for provisional backfill (#75)
- Align matrix handoff with selected integration (#70)

### Pipeline Modernization

- Phase 1 peak pipeline modernization (#62)
- Close ASLS phase 1 scope (#63)
- Identity coherence diagnostic acceptance (#60)

### Agent & Workflow

- Stabilize agent workflow and validation preflight (#65)
- Codify XIC agent workflow routing (#66)

---

## 2026-05 mid — Architecture Decomposition & Diagnostics

Major codebase decomposition: extraction, signal processing, alignment,
diagnostics, and configuration each got responsibility-scoped modules.
Targeted NL dropout convergence and region-first safe merge landed.

### Module Decomposition

- Define alignment module responsibility contract (#47)
- Split diagnostics report responsibilities (#48)
- Split alignment pipeline helper responsibilities (#49)
- Refactor diagnostics structure and characterize claim registry (#56)
- Refactor codebase cleanup inventory surfaces (#61)

### Targeted Extraction

- Targeted NL dropout evidence convergence (#52, #53)
- Region-first safe merge and audit bridge (#53)
- Add region-first validation and integration audit diagnostics (#54)
- Add targeted evidence consistency diagnostics (#51)

### Untargeted Alignment

- Untargeted alignment performance and matrix identity updates (#45)
- Tighten untargeted alignment gates and diagnostics (#46)

### Diagnostics & QC

- Add area mismatch and seed-aware backfill review diagnostics (#55)
- Optimize claim registry hot path (#57)
- Add instrument QC HCD audit (#58)

### Documentation

- Document project layout and scratch hygiene (#50)

---

## 2026-05 early — Untargeted Discovery & Evidence Scoring

Untargeted discovery pipeline landed with CID neutral-loss seed
evidence, MS1 feature finding, and cross-sample alignment. Weighted
evidence scoring replaced the initial tier-based confidence system.

### Untargeted Discovery

- Untargeted discovery and alignment integration (#43)

### Evidence & Scoring

- Weighted evidence confidence scoring (#40)
- Add targeted MS2 trace evidence (#41)
- Add ISTD area CV review report (#38)
- Fix ISTD CV peak selection (#39)

### Peak Detection

- Add opt-in local minimum resolver (#16)
- Calibrate local minimum preset (#31)
- Add validation harness baselines (#30)
- Improve local-minimum evidence and Excel review UX (#29)
- Add resolver profile GUI and local minimum preset contract (#28)

### Output & Refactoring

- Review visualization (#33)
- Refactor output workbook and review report modules (#32)
- Refactor extractor orchestration modules (#34)
- Refactor signal processing peak detection modules (#35)
- Refactor config loading modules (#36)
- Refactor GUI settings section modules (#37)

### Performance

- Optimize raw processing with optional parallel execution (#27)

### GUI

- Add GUI advanced settings section (#25)
- Write Excel output directly from extraction results (#24)
- Refactor output internals for maintainability (#23)

---

## 2026-04 — GUI Foundation & Initial Release

Built the PyQt6 GUI from scratch, established the targeted extraction
pipeline, set up PyInstaller packaging, and shipped the first CI/CD
workflow for Windows exe distribution.

### GUI

- Scaffold gui package, config_io, light-theme stylesheet
- Add SettingsSection, TargetsSection (inline editing), RunSection,
  ResultsSection, MainWindow
- GUI complete with elapsed timer, folder button, labeled spinbox
- ISTD tag, pairing, and NL scroll fix (#1)

### Pipeline

- Add full Python area extraction pipeline (#10)
- Tier-based peak scoring with delta-RT anchoring (#15)
- Merge MS1/MS2 into single-row targets schema, add NL presets

### Packaging & CI

- PyInstaller spec for onedir Windows exe
- GitHub Actions workflow for tag-triggered build and release
- Frozen-path support and example-csv fallback
- PyInstaller 6.x path separation fix (#6)
- Standardize CI/CD workflows with shared guidance (#8)

### Configuration

- Add example config files for distribution
- Modernize project structure (pyproject.toml, launch_gui.bat)
- UTF-8-sig encoding for example config files

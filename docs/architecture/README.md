# Architecture

Code organization: who owns what, which way dependencies point and how a run
executes. Product behavior is in [`docs/product/`](../product/README.md); exact
exports, schemas and construction rules are in source and executable checks.

先把 XIC 理解成一套「從量測證據得到樣本格值，再交付結果」的系統。
Targeted 用已知 target 約束搜尋並輸出固定列；Untargeted 找一般候選並輸出
feature-by-sample Matrix。兩者共用科學判斷，但保留不同搜尋政策及 writer。

```text
HTML workbench / CLI
    │
configuration ──► extraction / discovery
                         │
                  peak_detection
                         │
                     alignment
                         │
                 output renderers
                         │
                completed evidence ──► xic_inspector
```

Targeted and Untargeted are applications of one evidence lifecycle. Targeted
adds target-list search and fixed-row delivery; Untargeted adds general
discovery, grouping and Matrix delivery. Separate entry points and writers do
not justify duplicate capture, peak identity, cross-sample, revisit or
cell-decision owners. The scientific lifecycle is in the
[Domain Glossary](../product/domain-glossary.md#product-shape),
[`alignment.md`](../product/alignment.md) and
[`backfill.md`](../product/backfill.md).

## Topics

| Question | Read |
| --- | --- |
| Which concepts exist, and what does a change to a fact's producer, format or identity check? | [Core model](core-model.md) |
| Which package or module owns a behavior? | [Module ownership](module-ownership.md) |
| Which imports are allowed, and which checks enforce them? | [Dependency direction](dependency-direction.md) |
| How is work scheduled, parallelized and timed? | [Runtime execution](runtime-execution.md) |
| Who can write a value? | [Writer Authority](../product/decision-policy.md#writer-authority) |
| What is a public contract? | [Public Contracts](../agent/product-validation-contract.md#public-contracts) |
| How are modules named, and where do files go? | [Project layout](../project-layout.md#module-naming) |
| Where does diagnostic code live? | [Diagnostic Boundaries](../agent/script-tool-lifecycle.md#diagnostic-boundaries) |
| 只想理解分析概念 | [人讀版核心導覽](../development/core-modules-explainer.html) |

## Entry Points And Handoffs

安裝後的命令由 [`pyproject.toml`](../../pyproject.toml) 的 `project.scripts` 宣告。

| 要追的行為 | 從哪裡讀 |
| --- | --- |
| HTML 工作區啟動、工作與檢閱 | [`xic_workbench/cli.py`](../../xic_workbench/cli.py)、[`xic_workbench/server.py`](../../xic_workbench/server.py) |
| Targeted 分析與交付 | [`xic_workbench/targeted.py`](../../xic_workbench/targeted.py)、[`scripts/run_extraction.py`](../../scripts/run_extraction.py) → [`applications/targeted/application.py`](../../xic_extractor/applications/targeted/application.py) |
| 既有 Python Targeted 入口 | [`extractor.run`](../../xic_extractor/extractor.py)，進入同一 Targeted application |
| Discovery 候選產生 | [`scripts/run_discovery.py`](../../scripts/run_discovery.py) → [`discovery/pipeline.py`](../../xic_extractor/discovery/pipeline.py) |
| Untargeted 完整方法流程 | Workbench 的 [`jobs.py`](../../xic_workbench/jobs.py) → [`applications/untargeted/full_analysis.py`](../../xic_extractor/applications/untargeted/full_analysis.py)；非 UI caller 直接使用相同 request 與 runner |
| 從既有 Discovery 開始的 Alignment 與 Matrix | [`scripts/run_alignment.py`](../../scripts/run_alignment.py) 或完整 CID-NL application → [`applications/untargeted/application.py`](../../xic_extractor/applications/untargeted/application.py) |
| 完成 Matrix 後的矩陣整理 | [`xic_workbench/matrix_curation.py`](../../xic_workbench/matrix_curation.py)、[`scripts/run_matrix_curation.py`](../../scripts/run_matrix_curation.py) → [`matrix_curation/export.py`](../../xic_extractor/matrix_curation/export.py)；只判定來源能否整理時讀 [`matrix_curation/source.py`](../../xic_extractor/matrix_curation/source.py) |
| 完成證據的只讀報告 | [`xic_inspector/cli.py`](../../xic_inspector/cli.py) → [`xic_inspector/api.py`](../../xic_inspector/api.py) |

檔案層級的 owner 以現行 source 為準：用 CBM 查 callers 與 consumers，再讀
module docstring。Package 名稱不是分層證明：`alignment/` 同時含科學與外層
adapter；通過 `pyproject.toml` 的 `[tool.importlinter]` 不代表所有 I/O 已隔離。

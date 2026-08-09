# Test Architecture Guidelines

本文件約束本 repo 後續測試架構。原則參考 pytest 官方文件，但以 XIC Extractor 的實際風險為優先：RAW I/O、workbook schema、GUI settings、parallel execution、scoring/selection contract、validation harness。

## 1. Test Pyramid For This Repo

所有新增測試必須先判斷層級，不要把不同層級混在同一個 test file。

| Layer | 用途 | 應該測什麼 | 不應該測什麼 |
|---|---|---|---|
| Unit | 純函式或單一 policy | parser、score rule、row transformation、message formatting、selection tie-break | 真實 RAW、完整 CLI flow、GUI widget tree |
| Contract | public surface 穩定性 | CLI flag、config key、worksheet schema/order、output columns、settings round-trip、module boundary | 內部實作細節 |
| Integration | 多模組但 raw-free | extractor fake reader、excel pipeline、parallel fake backend、GUI section values | 85 raw full run |
| Validation | 真實資料驗證 | 2-raw manual truth、8-raw subset、85-raw full run | PR 預設 full-suite blocker，除非任務就是 validation |

預設開發節奏：

```powershell
.venv\Scripts\python.exe -m pytest tests\path_to_relevant_test.py -v
.venv\Scripts\python.exe -m pytest tests\related_group_a.py tests\related_group_b.py -q
.venv\Scripts\python.exe -m pytest --tb=short -q
```

真實資料驗證優先使用 validation harness，`parallel_workers=4` 作為預設人工驗收設定。不要在一般 unit/contract tests 直接依賴本機 RAW 路徑。

## 2. File Layout

目前維持 `tests\test_*.py` 扁平結構，因為 repo 已經採用這個模式且 `pyproject.toml` 設定 `testpaths = ["tests"]`。

新增測試檔命名：

- `test_<module>.py`：單一 production module 的 unit/contract tests。
- `test_<surface>_contract.py`：public surface 或 schema contract。
- `test_<workflow>.py`：跨模組 integration workflow。
- `test_<feature>_validation.py`：raw-free validation harness 或 synthetic validation logic。

拆分 guardrail：

- 單一 test file 超過約 500 行時，新測試不得繼續塞進去；先依 behavior 拆檔。
- 同一檔案只允許一個主要責任，例如 workbook sheet rendering、review report HTML、config parsing。
- `tests\conftest.py` 只放跨多個 test file 都需要的 fixture。單檔 helper 留在該 test file 底部。
- 大型 fixture data 放 `tests\fixtures\`，用 fixture 讀取，不要在 test body 內組大量重複 dict。

## 3. Naming And Discovery

遵循 pytest 預設 discovery：

- test file: `test_*.py`
- test function: `test_<behavior>_<expected_result>`
- test class: `Test<Behavior>`，只有在共享 setup 能提高可讀性時使用。

命名必須描述 observable behavior，不描述 implementation。

Good:

```python
def test_summary_excludes_numeric_nl_fail_rows_from_analytical_aggregates():
    ...
```

Bad:

```python
def test_private_helper_branch_3():
    ...
```

## 4. Fixture Rules

優先使用 pytest built-in fixtures：

- 檔案與目錄：`tmp_path`。本 repo 停用 `tmpdir` plugin，不要使用 `tmpdir`。
- patch 全域狀態或 import seam：`monkeypatch`。
- stdout/stderr：`capsys`。
- warning：`pytest.warns` 或 `pytest.mark.filterwarnings`。

Fixture 必須遵守：

- scope 預設用 function，不要為了速度先用 session/module。
- 需要 teardown 時用 `yield` fixture，不要手動清理散在 test body。
- fixture 名稱描述 domain role，例如 `fake_raw_reader`, `config_with_score_breakdown`。
- 不要建立「萬用 fixture」。如果 test 只需要兩個欄位，就不要回傳完整巨大物件。
- autouse fixture 只能用於全檔案安全隔離，例如禁用 RawFileReader preflight；不得隱藏業務 setup。

## 5. Mocking And Monkeypatch

Mock 只用來隔離外部邊界或昂貴/不可控依賴：

- Thermo RawFileReader / DLL preflight
- filesystem output path
- GUI worker signal
- multiprocessing executor seam
- review report writer seam

Patch 位置要是 production code 實際 lookup 的 import seam。例如 production module 用 `excel_pipeline.write_review_report`，test 就 patch 這個位置。

禁止：

- mock 掉被測邏輯本身。
- 為了讓測試通過而 mock 掉 scoring/selection policy。
- mock third-party library 後只驗證 mock call count，沒有驗證 observable output。

## 6. Assertions

測試要驗證使用者或下游真正依賴的結果：

- workbook：sheet order、active sheet、hidden sheet、headers、cell value、number format、critical fill/comment。
- CSV：headers、row count、key values、ND/ERROR token。
- config：round-trip、default value、invalid value error message。
- scoring/selection：selected peak、confidence、reason、severity labels、candidate-scoped evidence。
- parallel execution：result equivalence、progress/cancellation contract、pickle-safe payload。

不要只 assert 「函式被呼叫」。如果真的需要 call assertion，還要 assert 產物。

多筆 input 使用 `pytest.mark.parametrize`，並提供 readable ids，讓失敗案例可直接定位。

## 7. Public Contract Tests

以下變更必須同步新增或更新 contract tests：

- CLI flag / command output
- GUI setting / layout-visible setting
- `config/settings.example.csv`
- `settings_schema.py`
- workbook sheet、header、column order、hidden/visible state
- CSV schema
- HTML report section / critical SVG marker
- validation harness output schema
- packaging spec included files

Contract tests 應該放在靠近現有 surface 的 test file，例如：

- workbook schema：`tests\test_excel_sheets_contract.py`
- output columns：`tests\test_output_schema_contract.py`
- CLI：`tests\test_run_extraction.py`
- settings GUI：`tests\test_settings_section*.py`
- module boundary：`tests\test_workbook_module_boundaries.py`

## 8. Real Data Validation

真實資料驗證不是 unit test，不能讓一般測試 suite 依賴本機 RAW 路徑；請透過 `$env:XIC_RAW_ROOT`、`$env:XIC_RAW_VALIDATION_DIR` 或測試 fixture 注入。

分級：

1. `2-raw manual truth`：area/RT/manual workbook comparison，方法或積分相關變更必跑。
2. `8-raw validation subset`：代表性 tissue subset，output/workbook/report 或 scoring 變更需要人工驗收時跑。
3. `85-raw full run`：release-level 或重大方法變更才跑。

規則：

- 預設 workers 用 4。
- 每次 validation 要輸出固定資料夾，避免 timestamp collision。
- 比對要記錄 baseline、candidate、settings override、resolver mode。
- 失敗要分類為 schema drift、missing detection、target swap、area drift、RT drift、report rendering，而不是只寫「不一樣」。

## 9. Workbook / Report Testing

Workbook tests 必須優先測 contract，不測 openpyxl implementation trivia。

必測：

- canonical sheet order。
- `Overview` active。
- `Diagnostics` hidden。
- `Score Breakdown` 只在 `emit_score_breakdown=True` 且有資料時出現。
- formula-injection 防護，例如 sample/target/reason 以 `=`, `+`, `-`, `@` 開頭。
- summary detection metrics 與 flagged workload 不混淆。
- review queue evidence 精簡且不複製 diagnostics verbose text。
- HTML report 對 user-controlled text escape。
- ISTD RT trend 在 injection order 存在時有 SVG marker，沒有時不輸出。

Module boundary tests 用於防止維護性倒退，例如 wrapper 不直接 import `openpyxl`、input module 不依賴 style module。

## 10. GUI Tests

GUI tests 必須避免 brittle pixel/layout snapshot。優先測：

- settings load/get round-trip。
- control enable/disable。
- default values。
- validation error。
- advanced/hidden controls 是否出現在正確 section。

視覺排版驗收可用人工 screenshot 或 browser/GUI smoke，但不要把易碎 pixel snapshot 放進 default pytest suite。

## 11. Parallel / Process Tests

Process mode 的 tests 要分三層：

- pure payload test：job payload 無 closure/callable，Windows spawn 可 pickle。
- fake backend test：不碰 RAW，驗證 serial/process result equivalence。
- real smoke test：最小 ProcessPool spawn，不讀 RAW，用 top-level worker。

必測 contract：

- cancellation before scheduling。
- pending futures cancel。
- progress callback 不倒退。
- worker error 轉成可讀 result，不讓 harness 直接 traceback。

## 12. Test Smells To Reject

遇到以下情況，應先重構測試再新增案例：

- test file 已經變成跨多個 module 的雜物桶。
- fixture 回傳巨大 object，但每個 test 只用一小部分。
- test 依賴執行順序。
- test 修改 repo 內實際 config/output，而不是用 `tmp_path`。
- test 需要網路、GUI app、真實 RAW 才能在 default suite 通過。
- test 只驗證 private helper 名稱，沒有驗證 public behavior。
- assert 過度寬鬆，例如只檢查檔案存在，不檢查內容。
- assert 過度精細，例如檢查不影響 contract 的內部 row index。

## 13. Required Verification Before Finish

小改動：

```powershell
.venv\Scripts\python.exe -m pytest tests\relevant_test.py -v
```

Output / workbook / report 變更：

```powershell
.venv\Scripts\python.exe -m pytest tests\test_csv_to_excel.py tests\test_excel_pipeline.py tests\test_excel_sheets_contract.py tests\test_review_metrics.py tests\test_review_report.py -q
```

Config / GUI setting 變更：

```powershell
.venv\Scripts\python.exe -m pytest tests\test_config.py tests\test_config_io.py tests\test_settings_new_fields.py tests\test_settings_section.py tests\test_settings_section_advanced.py -q
```

Extraction / scoring / signal processing 變更：

```powershell
.venv\Scripts\python.exe -m pytest tests\test_signal_processing.py tests\test_signal_processing_selection.py tests\test_peak_scoring.py tests\test_scoring_context.py tests\test_scoring_factory.py tests\test_extractor.py -q
```

Parallel / validation harness 變更：

```powershell
.venv\Scripts\python.exe -m pytest tests\test_parallel_execution.py tests\test_parallel_progress_cancellation.py tests\test_multiprocessing_entrypoints.py tests\test_validation_harness.py -q
```

收尾：

```powershell
uv run ruff check xic_extractor tests scripts
uv run mypy xic_extractor
.venv\Scripts\python.exe -m pytest --tb=short -q
```

如果 full suite 太慢或外部環境不可用，必須在回報中寫明：已跑哪些 focused tests、沒跑什麼、剩餘風險是什麼。

## 14. References

- pytest documentation: https://docs.pytest.org/en/stable/
- Good Integration Practices: https://docs.pytest.org/en/stable/explanation/goodpractices.html
- Test discovery and naming configuration: https://docs.pytest.org/en/stable/example/pythoncollection.html
- Monkeypatching and environment isolation: https://docs.pytest.org/en/stable/how-to/monkeypatch.html

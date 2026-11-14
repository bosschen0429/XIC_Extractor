# 結果檔案

每個結果檔放什麼、各欄是什麼意思。判讀方式見[判讀原理](concepts.md)。

## Targeted Excel

每次分析的資料夾 `<日期>_<時間>_targeted_<RAW 資料夾名>` 裡有一份 `xic_results_<日期>_<時間>.xlsx`。

| 工作表 | 內容 |
| --- | --- |
| `摘要` | 第一行是結論：要先看幾筆。下面每個目標一列：類型、配對 ISTD、計入／全部、需先看、說明 |
| `需檢查` | 要先看的結果：目標、樣本、結果、原因、建議檢查、RT、Area。讀取失敗的 RAW 也列在這裡 |
| `面積` | 樣本（列，依注射順序）× 目標（欄）的面積；共用 ISTD 的分析物排在一起，後面接該 ISTD |
| `XIC Results` | 每個樣本 × 目標一列，欄位見下表 |
| `Targets` | 本次使用的目標清單與實際套用的容差 |
| `Run Metadata` | 版本、時間與分析設定 |
| `欄位說明` | 每一欄的意思與單位 |

**需先看** 是每個目標裡占 20% 以下的那一方判定，例如多數計入時沒計入的幾筆；沒有明顯多數時，改看檢閱註記。它只決定閱讀順序，不改判定。

### `面積` 的寫法

| 看到的 | 意思 |
| --- | --- |
| 黑字數值 | 已計入的面積（counts·s） |
| 灰字數值 | 未計入，數值保留給你判斷 |
| `ND` | 未偵測；`AVERAGE` 會略過 |
| 空白 | 沒有可靠的定量值，原因在 `XIC Results` 的 `Reason` |

### `XIC Results` 的欄位

前八欄回答日常判讀；其餘欄預設收合，按欄標上方的 **＋** 展開。

| 欄 | 意思 |
| --- | --- |
| `SampleName` | 樣本名稱 |
| `Target` | 目標名稱 |
| `RT` | 所選峰的峰頂 RT（min） |
| `Area` | 所選峰的面積（counts·s）；有數值不代表已計入 |
| `IntSmoothed` | 平滑後的峰頂強度（counts），不是面積 |
| `Counted Detection` | `TRUE` 已計入，`FALSE` 未計入；未計入的列是灰字 |
| `Review` | `Review` 或 `Check` 表示有待確認的事，空白表示沒有 |
| `Review Reason` | 待確認的事 |
| `PeakStart`、`PeakEnd` | 積分起點與終點（min） |
| `NL Evidence`、`Reason` | 中性丟失的觀察與完整判定說明 |
| `Role`、`Group` | ISTD 或 Analyte；樣本組別 |
| `Int`、`PeakWidth` | 原始峰頂強度與峰寬 |

隱藏工作表 `診斷事件` 記錄挑峰時的事件，追查舊判定時才需要：在工作表標籤按右鍵 →「取消隱藏」。

## Untargeted 結果資料夾

每次分析建立 `<日期>_<時間>_untargeted_<RAW 資料夾名>`。

| 檔案 | 內容 |
| --- | --- |
| `alignment_matrix.tsv` | 主要結果：feature × 樣本的面積矩陣 |
| `alignment_review.tsv` | 每個群組一列：中心 m/z 與 RT、偵測數、是否進矩陣、警示 |
| `alignment_explanation.sqlite` | 檢閱畫面用的判斷與曲線，不需要自己開 |
| `alignment_run_metadata.json` | 本次的輸入與設定 |
| `sample_metadata.tsv` | 本次使用的樣本角色、注射順序與排除 |
| `<樣本>/discovery_peaks.csv` | 該樣本找到的 MS1 峰，以及哪些進入對齊 |
| `<樣本>/discovery_events.csv` | 該樣本的每個中性丟失 MS2 事件，以及它落在哪個峰 |
| `discovery_batch_index.csv` | 各樣本候選表的索引 |
| `matrix_curation_<日期>_<時間>/` | 整理矩陣的結果，見下方 |

### `alignment_matrix.tsv`

| 欄 | 意思 |
| --- | --- |
| `Mz` | feature 的中心 m/z（四位小數） |
| `RT` | feature 的中心 RT（min） |
| 每個樣本一欄 | 面積；沒有值時留空 |

樣本欄依注射順序排列。要對回 `alignment_review.tsv`，用該檔的 `matrix_row_index`，不要用四捨五入後的 `Mz`。

### `alignment_review.tsv` 的主要欄位

| 欄 | 意思 |
| --- | --- |
| `feature_id` | 群組編號，如 `F000001`；只在這份結果內有效 |
| `matrix_row_index` | 在矩陣的第幾列；空白表示沒進矩陣 |
| `group_center_mz`、`group_center_rt` | 群組中心 m/z 與 RT（完整精度） |
| `detected_count` | 直接偵測到的樣本數 |
| `accepted_cell_count`、`accepted_backfill_count` | 有值的格數，以及其中由回填取得的格數 |
| `identity_reason` | 群組的判定；只有 `established` 進矩陣 |
| `warning` | `no_anchor`、`high_not_searched` 或 `high_backfill_dependency`（有回填值但沒有直接偵測） |

### 整理矩陣的資料夾

| 檔案 | 內容 |
| --- | --- |
| `analysis_matrix.tsv` | 整理後的矩陣，給下游統計用 |
| `feature_decisions.tsv` | 每個 feature 在每一步的保留或移除，以及原因 |
| `same_compound_relations.tsv`、`same_compound_review.tsv` | 同化合物收斂找到的關係 |
| `sample_metadata.tsv` | 整理時使用的樣本角色 |
| `matrix_curation_summary.json` | 本次整理的步驟、參數與列數 |

### Delta Mass 方法

結果是 `delta_mass_candidates.csv` 等候選表，沒有矩陣。Pt pattern ＋ CID-NL dR 另有 `cid_nl_dR/` 資料夾，放同一批樣本的 dR 候選。

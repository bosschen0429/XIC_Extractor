# 原型：沿針序連帶與逐帶漂移（用完即丟）

這個資料夾是 throwaway 原型，不是正式程式，也不會合併。它回答的問題與結論寫在對應的 implementation issue，這裡只留可重跑的腳本、跑次輸出和圖。

## 回答了哪些問題

| 問題 | 腳本 | 結論（細節見 issue） |
|---|---|---|
| 沿針序把峰連成帶，能不能取代點式成本搜尋做最終分組？ | `band_tracking.py`、`whole_batch.py`、`recount*.py` | 乾淨窗口大多相同，差異集中在同一針有多支峰的窗口 |
| 一路漂移的帶怎麼接？ | `perband_check.py`、`perband_drift.py`、`perband_joins.py`、`perband_isotope_check.py` | 逐帶擬合漂移係數再連帶：348.21、302.1774 通過 |
| 投票 M 的列能不能由帶提供？ | `m_vote.py`、`m_vote_diag.py`、`m_vote_348.py` | 82/85 針跟成本搜尋差幾秒；第 3、67、79 針票數不足 |
| 批次校正的錨點能不能由帶提供？ | `raw_rt_check.py`、`raw_rt_gate.py`、`raw_rt_figure.py`、`refit_calibration.py` | 跟現有錨點平手；第一輪要放寬步距跨過 #786 的整針跳動 |
| 完全不用成本搜尋的整段鏈 | `a_end_to_end.py`、`a_outliers.py` | 大致成立；第 3、67 針有少數成員難判 |
| 第 3、67 針為什麼一再出現 | `inj_3_67.py`、`ms2_compare.py`、`matrix_markers.py` | 一部分化合物提早 3–8 分；原因不明 |
| 要不要 fail close | `failclose_m.py`、`failclose_votes.py` | 不採用 |
| 能不能直接在訊號平面上追脈線 | `ridges.py`、`ridge_figure.py` | 不採用 |
| 檢視器 | `export_viewer.py`、`export_viewer_perband.py`、`viewer_template.html`、`draw.py` | 給人看疊圖用 |

每個腳本的 `.log` 是該次跑次的輸出；`figures/` 是 issue 裡用的圖。

## 怎麼重跑

輸入不在這個 branch：
- 85 針分組輸入的擷取（`grouping-inputs.json.gz`）與重播 pickle，原本放在 #1286 worktree 的 `output/band-threshold-85/`；
- 85 支 RAW；
- 針序檔（樣品名到注射順序的 JSON）。

腳本預期放在 #1286 checkout 的 `output/prototype-band-tracking/`，從 repo 根目錄用該 checkout 的 `.venv` 執行，第一個參數是針序檔，例如：

```text
.venv\Scripts\python.exe output\prototype-band-tracking\perband_drift.py <injection-order.json>
```

`results.pkl`、`traces.pkl` 等中間檔由 `analyze.py`、`draw.py` 產生，不在 branch 裡。

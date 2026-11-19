# XIC Extractor 使用說明

從一批 Thermo RAW 檔取得可判讀的定量結果。全部在本機執行，說明可離線閱讀。

## 安裝與啟動

1. 把應用程式 ZIP 完整解壓縮到可寫入的資料夾。不要在 ZIP 裡直接執行。
2. 雙擊 `XIC_Extractor.exe`，瀏覽器會開啟工作區。
3. 用完按右上角的電源圖示（**結束工作區**）。關掉所有分頁後，工作區也會自行結束；分析進行中或剛完成還沒看時會先保留。

不需要安裝 Python，也不必安裝 Xcalibur。

## 第一次使用：準備 RawFileReader

讀 RAW 需要 .NET 8 以上與 Thermo RawFileReader。缺少時，工作區上方會出現「讀取 RAW 前還需要準備 RawFileReader」。

1. 缺 .NET：按 **複製指令**，在 PowerShell 執行後重新啟動 XIC Extractor。
2. 缺 RawFileReader：勾選 **我已閱讀並同意**，按 **下載並設定 RawFileReader**。
   - 本機已有：按 **使用已安裝版本**。

下載需要網路一次。之後閱讀說明與檢閱結果都不需要網路。

## 選一種分析

| 你有的是 | 要得到的是 | 從這裡開始 |
| --- | --- | --- |
| 已知化合物的目標清單 | 每個樣本 × 每個目標的面積（Excel） | [Targeted 分析](targeted.md) |
| 一種探索方法，例如 dR 中性丟失 | 全部 feature × 樣本的面積矩陣 | [Untargeted 分析](untargeted.md) |

每頁從準備、執行到看結果一次說完。Untargeted 的內標清單沿用 Targeted 的[目標清單](targeted.md#目標清單)格式。

## 看結果前必知

- **有面積不等於計入偵測**：面積回答「量到多少」，計入偵測回答「算不算偵測到」，檢閱註記回答「要不要再看一眼」，三者各自判定。
- **`ND`、空白、`0` 不一樣**：`ND` 是沒找到符合條件的峰，空白是沒有可靠的定量值，兩者都不是 0，也不證明樣本裡沒有。
- **feature 不是已確認的化合物**：Untargeted 矩陣的一列是各樣本對齊成同一列的訊號；同一化合物的同位素或 adduct 可能各成一列。

原因與例子見[判讀原理](concepts.md)。

## 重看已完成的結果

1. 右上角按 **開啟既有結果**。
2. **結果位置** 選 Targeted 的 `xic_results_*.xlsx`，或 Untargeted 的結果資料夾。
3. 按 **開啟結果**。

重看不會重算，也不需要 RAW。只有要看 XIC 時才需要原 RAW 檔。

## 遇到問題

| 狀況 | 處理 |
| --- | --- |
| 雙擊後沒有出現工作區 | 確認已完整解壓縮，EXE 旁有 `_internal/` |
| 讀不到 RAW | 依工作區上方提示補 .NET 或 RawFileReader |
| 分析中的問題 | 見 [Targeted](targeted.md#遇到問題) 或 [Untargeted](untargeted.md#遇到問題) |

## 參考

**快捷鍵**：<kbd>Ctrl</kbd> <kbd>1</kbd> Targeted、<kbd>Ctrl</kbd> <kbd>2</kbd> Untargeted、<kbd>Ctrl</kbd> <kbd>Enter</kbd> 開始目前頁面的分析、<kbd>Ctrl</kbd> <kbd>S</kbd> 儲存目前頁面的設定。右上角 **？ → 範例檔** 可下載目標清單 XLSX，以及整理矩陣用的核苷清單 TSV（DNA dR）。

**程式資料夾**：`XIC_Extractor.exe` 是主程式，`_internal/` 是必要元件（不要移動或刪除），`開始使用.html` 是這份說明（程式關閉時也能開），`docs/` 放說明頁與截圖，`examples/` 放目標清單範例，`config/` 是第一次執行後建立的本機設定，`output/` 是預設的結果存放位置，`licenses/` 是第三方授權聲明。搬移程式時搬整個資料夾；更新版本前，先另存自己的目標清單、方法檔與結果。

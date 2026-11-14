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

兩種分析共用 [目標清單](target-list.md) 的格式：Targeted 讀全部目標，Untargeted 只讀內標列。

## 重看已完成的結果

1. 右上角按 **開啟既有結果**。
2. **結果位置** 選 Targeted 的 `xic_results_*.xlsx`，或 Untargeted 的結果資料夾。
3. 按 **開啟結果**。

重看不會重算，也不需要 RAW。只有要看 XIC 時才需要原 RAW 檔。

## 快捷鍵

| 按鍵 | 動作 |
| --- | --- |
| <kbd>Ctrl</kbd> <kbd>1</kbd> | Targeted 分析 |
| <kbd>Ctrl</kbd> <kbd>2</kbd> | Untargeted 分析 |
| <kbd>Ctrl</kbd> <kbd>Enter</kbd> | 開始目前頁面的分析 |
| <kbd>Ctrl</kbd> <kbd>S</kbd> | 儲存目前頁面的設定 |

右上角的問號圖示（**使用說明與快捷鍵**）會列出全部快捷鍵，也可以下載目標清單範例。

## 程式資料夾

| 項目 | 內容 |
| --- | --- |
| `XIC_Extractor.exe` | 主程式 |
| `_internal/` | 必要元件，不要移動或刪除 |
| `開始使用.html` | 這份說明，程式關閉時也能直接開啟 |
| `docs/` | 說明頁與截圖 |
| `examples/` | 目標清單範例 |
| `config/` | 本機設定，第一次執行後建立 |
| `output/` | 預設的結果存放位置 |
| `licenses/` | 第三方授權聲明 |

搬移程式時，搬整個資料夾。更新版本前，先另存自己的目標清單、方法檔與結果。

## 遇到問題

| 狀況 | 處理 |
| --- | --- |
| 雙擊後沒有出現工作區 | 確認已完整解壓縮，EXE 旁有 `_internal/` |
| 讀不到 RAW | 依工作區上方提示補 .NET 或 RawFileReader |
| 不知道某個數字代表什麼 | 看[判讀原理](concepts.md)與[結果檔案](results.md) |

# XIC Extractor — 開始使用

這是 Windows 應用程式包，不是原始碼套件。使用本包不需要安裝 Python、uv
或 Git。請先完整解壓縮 ZIP，再啟動程式；不要直接在壓縮檔裡執行。

## Launch

1. 將整個資料夾放在可寫入的位置，避免放進 `Program Files`。
2. 雙擊同一資料夾內的 **XIC_Extractor.exe**，程式會開啟本機瀏覽器工作區。
3. 依分析目的選擇 Targeted 或 Untargeted，準備 RAW 檔與分析方法。
4. 使用工作區的結束控制關閉程式；關閉瀏覽器分頁不會取消正在執行的分析。

`_internal/` 是必要的執行依賴，請保留，不必開啟或修改。
移動程式時請移動整個資料夾，不要只複製 EXE。

## Reader Setup

RAW 分析另需 **.NET 8 或更新版本**與 **Thermo RawFileReader 8.0.42**。
首次啟動時，依工作區的設定提示閱讀 Thermo 授權並接受後安裝 reader。
如果缺少 .NET，依提示安裝並重新啟動程式。不需要安裝 Xcalibur。

首次下載這些外部依賴需要網路；**本包的操作指南不需要網路**。
詳見[Reader 設定與手動安裝說明](../docs/user/README.md#reader-setup)。

## Offline Guides

- [使用指南首頁](../docs/user/README.md)：依任務選擇說明。
- [Targeted 圖解](../docs/user/targeted-extraction.html)與[詳細操作](../docs/user/targeted-extraction.md)：已知目標定量與工作簿判讀。
- [Untargeted 圖解](../docs/user/untargeted-discovery.html)與[詳細操作](../docs/user/untargeted-discovery.md)：feature discovery、Alignment 與 Matrix 判讀。
- [處理原理與限制](../docs/user/processing-guide.html)：理解候選、偵測、面積與缺格的差異。
- [方法與結果欄位參考](../docs/product/README.md)：查閱特定規則。

請直接用瀏覽器開啟這些 HTML 頁面。文件中的外部網站連結仍需要網路；
原始碼與開發維護文件不隨本包提供。個別指南保留的 CLI 說明適用於另外安裝的
原始碼／Python 套件，不是本包的啟動步驟。

## Files And Results

- `docs/`：可離線閱讀的指南、圖片及方法參考。
- `examples/`：Targeted [方法範本](../xic_extractor/configuration/data/targeted-method.xlsx)、[目標表](../xic_extractor/configuration/data/targets.xlsx)與[CSV 範本](../xic_extractor/configuration/data/targets.csv)。請複製後編輯。
- `output/`：預設結果位置與 `launcher/` 啟動記錄；分析輸出以工作區顯示的路徑為準。
- `config/`：執行後依需要建立的本機設定；不包含開發者的設定或資料。
- `licenses/`：隨附第三方軟體與圖示的授權聲明。

移動或更新程式前，請另外保留自己的方法、設定與分析結果。

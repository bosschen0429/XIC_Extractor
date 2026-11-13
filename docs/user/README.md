# XIC Extractor 使用說明

從一批 Thermo RAW 檔取得可判讀的定量結果。先選擇要完成的分析，遇到問題再查
原理或欄位；不必先理解程式架構。本指南與附圖都可離線閱讀。

<a id="start"></a>
## 開始使用

1. **完整解壓縮應用程式 ZIP**，放在可寫入的資料夾，不要直接在 ZIP 內執行。
2. 雙擊 **XIC_Extractor.exe**，程式會開啟本機瀏覽器工作區。不需要安裝 Python、uv 或 Git。
3. 第一次使用先完成下方的 reader 設定，再選擇分析。

程式內右上角的 **使用說明與快捷鍵**可開啟同一份指南。程式關閉後，也可以直接用
瀏覽器開啟資料夾內的 **開始使用.html**；不需啟動程式或連線網路。

<a id="reader-setup"></a>
## 首次設定

RAW 分析需要 **.NET 8 或更新版本**與 **Thermo RawFileReader 8.0.42**。

1. 依工作區的設定提示，確認缺少的是 .NET、reader，還是 reader 資料夾未選好。
2. 若缺 .NET，依提示安裝並重新啟動程式。
3. 若缺 reader，閱讀連結中的 Thermo 授權，接受後按 **下載並設定 RawFileReader**。
   已有相容 reader 時，可直接選取其資料夾。

首次下載外部依賴需要網路；文件與結果檢閱不需要網路。Xcalibur 不必安裝，也不會
被修改。不要以舊 Xcalibur DLL 取代支援的 reader。

## 選擇分析

| 我想完成的工作 | 需要準備 | 從這裡開始 |
| --- | --- | --- |
| 已知目標清單的定量 | RAW 資料夾、目標表或已保存的 XLSX 方法 | [Targeted 操作指南](targeted-extraction.html) |
| 探索並比較樣本中的 features | RAW 資料夾、CID-NL 方法、樣本角色及適用內標 | [Untargeted 操作指南](untargeted-discovery.html) |

Targeted 範本可直接從工作區的目標清單卡下載，也可複製本包的
[方法範本](../../xic_extractor/configuration/data/targeted-method.xlsx)或
[目標表](../../xic_extractor/configuration/data/targets.xlsx)後編輯。
Untargeted 可從 dR、R、MeR 方法開始，再依樣本與分析目的調整。

## 判讀與重開結果

- **Targeted：**先看工作簿的 `摘要`，再看 `需檢查`。在工作區選擇目標與樣本，
  檢閱保存的判斷；要重新擷取 XIC 時，才需要相應 RAW 檔與 reader。
- **Untargeted：**先看 `alignment_matrix.tsv`。在工作區檢閱 feature、樣本與缺格的
  依據；候選表不是完成的 Matrix，feature 也不等於已確認的化合物。
- **重開結果：**使用工作區的 **開啟既有結果**，選擇 Targeted 工作簿或完成的批次結果資料夾。
  查看保存的結果不會重新選峰或改寫原始定量。
- **整理 Matrix：**在完成的 Untargeted 結果選 **整理矩陣**，確認步驟與輸入。
  整理會產生另一份結果，原始 Matrix 保留；它不代替 normalization 或下游統計。
  結果有整理時，檢閱畫面跟著最新一次整理後的矩陣，並在標題旁標出整理資料夾；
  被移除的 feature 在 **整理時移除**，附移除原因。要看另一次整理，直接開啟那個整理資料夾。

不確定數字代表什麼時，先讀[峰、偵測、面積與缺格的處理原理](processing-guide.html)。
需要方法情境時再看[DNA adduct CID-NL 解說案例](raw-to-final-matrix-product-story.html)；
案例不是所有方法的驗證證明。

## 遇到問題

| 遇到的情況 | 下一步 |
| --- | --- |
| 雙擊後沒有工作區 | 確認已完整解壓縮，EXE 旁仍有 `_internal/`；查看 `output/launcher/` 啟動記錄 |
| RAW 分析無法開始或 DLL 讀取失敗 | 回到工作區的 reader 設定提示，確認 .NET、reader 版本與資料夾 |
| 輸入表無法匯入 | 依錯誤訊息修正檔案、列與欄位；使用範本，不以舊格式強行載入 |
| 想關閉程式或停止分析 | 正在分析時用工作區的停止控制；結束時用 **結束工作區**，不要只依賴關閉分頁 |
| 矩陣出現缺格或結果異常 | 先查樣本角色、方法及保存的判斷，再看 XIC；不要直接把缺格改成零 |

<a id="reference"></a>
## 查閱細節

只有需要查細節時才讀這些參考；不必選擇另一套文件入口。

- Targeted：[逐步操作](targeted-extraction.md)、[方法與目標表欄位](targeted-config-reference.md)、[輸出欄位](targeted-output-reference.md)。
- Untargeted：[逐步操作與 Matrix 使用](untargeted-discovery.md)、[方法設定](untargeted-preset-guide.md)、[輸出欄位](untargeted-output-reference.md)。

## 檔案與更新

保留 EXE 旁的 `_internal/`，它是必要依賴，不用手動操作。移動程式時請移動整個資料夾。
`docs/` 是指南與圖片，`examples/` 是可複製的範本，`licenses/` 是第三方授權聲明。
`config/` 會保留本機設定，預設結果與啟動記錄在 `output/`；分析結果以工作區顯示的路徑為準。

更新或刪除舊版前，另外保留自己的方法、設定與分析結果。

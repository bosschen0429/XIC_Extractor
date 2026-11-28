# 開發與進階命令列

Windows 應用程式的操作說明在[使用者入口](../user/README.md)。

- [從原始碼執行、CLI 與驗證](command-line.md)
- [產品方法、結果契約與限制](../product/README.md)
- [共用核心解說](core-modules-explainer.html)與[系統分工](system-guide.html)
- [Repo 結構](../project-layout.md#repository-tree)與[架構與閱讀程式](../architecture/README.md)
- [開發環境與 RAW 執行規則](../agent/local-execution.md)

這些是 repo 維護與進階操作文件，不隨 Windows 應用程式 ZIP 提供。

## 從原始碼執行

分析流程在 **Windows 10 或 11** 上執行，使用 Python **3.15**。安裝目前版本的 `uv` 後，在 PowerShell 執行：

```powershell
git clone https://github.com/bosschen0429/XIC_Extractor.git
cd XIC_Extractor
uv sync --locked
uv run xic-workbench
```

`uv` 會自動找到或下載 Python 3.15；較舊的 `uv` 找不到這個版本時，先更新 `uv`。
之後可雙擊 `launch_gui.vbs` 免主控台啟動，或用 `launch_gui.bat` 保留主控台。
直接開啟已完成的結果：

```powershell
uv run xic-workbench "path/to/result-folder-or-workbook.xlsx"
```

RawFileReader 的命令列安裝見[從原始碼執行與命令列](command-line.md#reader-cli)。

## 支援平台與 CI

Windows 發布套件與所有 CI lane 使用同一份鎖定的 Python 3.15 依賴。
Linux 負責一般 CI 測試，包含 .NET/CoreCLR 整合；Windows 保留作業系統相關、瀏覽器與打包檢查。
目前不宣稱完整支援 Linux 上的分析或桌面套件。

打包時先找 Windows 自己的 DLL，再找 PATH 上的工具，以免載入另一份 ICU；套件專用目錄仍由 PyInstaller hook 提供。發行包收錄 PyInstaller 授權，因為其中包含 bootloader exception。

## 測試

本機修改預設執行 `.venv\Scripts\python.exe -m tools.testing.test_selection`。
已知的測試檔或失敗節點可直接執行；不指定目標的 `pytest` 會收集整個測試集。
見 [test selection](../agent/testing.md#affected-test-selection)。

## 責任與實作圖

這些圖用來閱讀程式分工，不是應用程式的操作步驟。

- [Targeted 工作分工](../user/assets/targeted-workflow.svg)
- [證據、決策與寫入責任](../user/assets/evidence-decisions.svg)
- [RAW 到 Matrix 與診斷支線](../user/assets/raw-matrix-journey.svg)

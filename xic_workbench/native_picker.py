"""Open the operating system's file or folder chooser for the local workspace."""

from __future__ import annotations

import threading
from pathlib import Path

# The workspace binds to loopback, so the chooser opens on the user's own desktop.
PICKER_KINDS: dict[str, tuple[str, str] | None] = {
    "folder": None,
    "report": ("分析結果", "*.sqlite *.db *.xlsx"),
    "raw": ("Thermo RAW", "*.raw"),
    "csv": ("CSV", "*.csv"),
    "targets": ("目標清單", "*.csv *.tsv *.xlsx"),
    "metadata": ("上機資訊", "*.docx *.xlsx *.csv *.tsv"),
    "preset": ("方法設定", "*.toml"),
    "file": ("設定檔", "*.csv *.tsv *.xlsx *.json *.toml *.txt"),
}

_open = threading.Lock()


class PickerBusy(Exception):
    """Another chooser is already open."""


def choose_path(kind: str, current: str = "") -> str:
    """Return the chosen absolute path, or "" when the user cancels."""
    if kind not in PICKER_KINDS:
        raise ValueError("請選擇資料夾或檔案類型。")
    if not _open.acquire(blocking=False):
        raise PickerBusy("已有開啟中的選擇視窗。")
    try:
        import tkinter
        from tkinter import filedialog

        root = tkinter.Tk()
        root.withdraw()
        # A withdrawn, topmost owner keeps the chooser above the browser window.
        root.attributes("-topmost", True)
        try:
            start = _starting_folder(current)
            filetype = PICKER_KINDS[kind]
            if filetype is None:
                chosen = filedialog.askdirectory(
                    parent=root, initialdir=start, title="選擇資料夾", mustexist=True
                )
            else:
                chosen = filedialog.askopenfilename(
                    parent=root,
                    initialdir=start,
                    title=f"選擇{filetype[0]}",
                    filetypes=[filetype, ("所有檔案", "*.*")],
                )
        finally:
            root.destroy()
    finally:
        _open.release()
    return str(Path(chosen)) if chosen else ""


def _starting_folder(current: str) -> str:
    """Start at the field's current location, or its nearest existing parent."""
    if not current.strip():
        return ""
    path = Path(current).expanduser()
    for candidate in (path, *path.parents):
        if candidate.is_dir():
            return str(candidate)
    return ""

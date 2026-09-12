from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from xic_extractor.raw_reader import (
    RawReaderError,
    RawSampleHeader,
    read_raw_sample_header,
)


class SampleMetadataScanWorker(QThread):
    progress = pyqtSignal(int, int, str)
    sample_header_read = pyqtSignal(object)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(
        self,
        raw_paths: Sequence[Path],
        dll_dir: Path | None = None,
    ) -> None:
        super().__init__()
        self._raw_paths = tuple(raw_paths)
        self._dll_dir = dll_dir

    def run(self) -> None:
        results: list[RawSampleHeader] = []
        total = len(self._raw_paths)
        for index, path in enumerate(self._raw_paths, start=1):
            if self.isInterruptionRequested():
                return
            self.progress.emit(index, total, path.name)
            header: RawSampleHeader | None = None
            if self._dll_dir and self._dll_dir.is_dir():
                try:
                    header = read_raw_sample_header(path, self._dll_dir)
                except (RawReaderError, OSError, Exception):
                    header = None
            if header is None:
                header = RawSampleHeader(
                    raw_path=str(path.resolve()),
                    raw_stem=path.stem,
                    sample_name=path.stem,
                    sample_id="",
                    row_number=None,
                    sample_type="",
                    creation_date=None,
                    vial="",
                    comment="",
                )
            results.append(header)
            self.sample_header_read.emit(header)

        if not self.isInterruptionRequested():
            self.finished.emit(results)

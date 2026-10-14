"""Explicit editable target-table exchange; semantic admission belongs to Targets."""

import base64
import binascii
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, ZipFile

import openpyxl

from xic_extractor.configuration.csv_io import read_target_draft_rows
from xic_extractor.configuration.models import ConfigError
from xic_extractor.configuration.targeted_method_files import load_target_table

MAX_UPLOAD_BYTES = 1024 * 1024


def preview_target_upload(name: str, content: str, settings: dict) -> dict:
    """Decode browser bytes and delegate semantics; never persist a preview."""
    from xic_extractor.configuration.targeted_method_files import (
        apply_targeted_method,
        load_targeted_method,
    )
    from xic_workbench.targeted import method_summary

    suffix = Path(name).suffix.lower() if isinstance(name, str) else ""
    if suffix not in {".xlsx", ".csv", ".tsv"}:
        raise ConfigError("請選擇 XLSX 方法檔或目標表，也支援 CSV／TSV；不支援 .xls。")
    if not isinstance(content, str) or len(content) > 4 * ((MAX_UPLOAD_BYTES + 2) // 3):
        raise ConfigError("檔案不可超過 1 MiB。")
    try:
        payload = base64.b64decode(content, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ConfigError("無法讀取上傳內容，請重新選擇檔案。") from exc
    if not payload or len(payload) > MAX_UPLOAD_BYTES:
        raise ConfigError("請選擇非空白且不超過 1 MiB 的檔案。")
    with TemporaryDirectory(prefix="xic-target-import-") as directory:
        # A browser-supplied name is a label, never a filesystem destination.
        path = Path(directory) / f"upload{suffix}"
        path.write_bytes(payload)
        try:
            is_method = False
            if suffix == ".xlsx":
                with ZipFile(path) as archive:
                    if (
                        sum(item.file_size for item in archive.infolist())
                        > 16 * MAX_UPLOAD_BYTES
                    ):
                        raise ConfigError("XLSX 解壓後過大，請移除不必要的內容。")
                book = openpyxl.load_workbook(path, read_only=True)
                try:
                    is_method = "Method" in book.sheetnames
                finally:
                    book.close()
            if is_method:
                method = load_targeted_method(path)
                applied = apply_targeted_method(settings, method)
                return {
                    "kind": "method",
                    "targets": [dict(row) for row in method.target_rows],
                    "settings": applied,
                    "summary": method_summary(applied),
                }
            return {"kind": "targets", "targets": import_targets(path)}
        except ConfigError as exc:
            raise ConfigError(str(exc).replace(str(path), name)) from exc
        except (BadZipFile, OSError, ValueError, KeyError) as exc:
            raise ConfigError("檔案無法讀取，請確認格式與內容後重新選擇。") from exc


def import_targets(path: Path) -> list[dict[str, str]]:
    source = Path(path)
    if source.suffix.lower() in {".csv", ".tsv"}:
        return read_target_draft_rows(source)
    if source.suffix.lower() == ".xlsx":
        return load_target_table(source)
    raise ConfigError(f"{source}: 請選擇 XLSX、CSV 或 TSV 目標表；不支援 .xls。")

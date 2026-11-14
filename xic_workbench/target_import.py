"""Explicit editable target-table exchange; semantic admission belongs to Targets."""

import base64
import binascii
import shutil
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast
from zipfile import BadZipFile, ZipFile

from xic_extractor.configuration.cid_nl_profile import NeutralLossProfile
from xic_extractor.configuration.csv_io import read_target_draft_rows
from xic_extractor.configuration.models import ConfigError
from xic_extractor.configuration.target_list_files import load_target_list

MAX_UPLOAD_BYTES = 1024 * 1024


def preview_target_upload(name: object, content: object) -> dict:
    """Decode browser bytes and delegate semantics; never persist a preview."""
    with uploaded_target_file(name, content) as path:
        if path.suffix == ".xlsx":
            target_list = load_target_list(path)
            return {
                "targets": [dict(row) for row in target_list.target_rows],
                "delta_mass": target_list.delta_mass,
            }
        return {"targets": import_targets(path), "delta_mass": None}


def import_istd_upload(
    name: object,
    content: object,
    config_dir: Path,
    *,
    profiles: Sequence[NeutralLossProfile] | None = None,
) -> dict:
    """Validate the shared list before retaining bytes for an Untargeted run."""
    from xic_extractor.configuration.targets import read_istd_targets

    with uploaded_target_file(name, content) as source:
        try:
            selection = read_istd_targets(source)
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
        if profiles is not None:
            selection = selection.for_method(profiles)
        destination = config_dir / "imports" / f"istd-{uuid.uuid4().hex}{source.suffix}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    return {"path": str(destination), "selection": selection}


@contextmanager
def uploaded_target_file(name: object, content: object) -> Iterator[Path]:
    """Bound and decode browser bytes for both list consumers; names are labels."""
    suffix = Path(name).suffix.lower() if isinstance(name, str) else ""
    if suffix not in {".xlsx", ".csv", ".tsv"}:
        raise ConfigError("請選擇 XLSX、CSV 或 TSV 目標清單；不支援 .xls。")
    if not isinstance(content, str) or len(content) > 4 * ((MAX_UPLOAD_BYTES + 2) // 3):
        raise ConfigError("檔案不可超過 1 MiB。")
    try:
        payload = base64.b64decode(content, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ConfigError("無法讀取上傳內容，請重新選擇檔案。") from exc
    if not payload or len(payload) > MAX_UPLOAD_BYTES:
        raise ConfigError("請選擇非空白且不超過 1 MiB 的檔案。")
    with TemporaryDirectory(prefix="xic-target-import-") as directory:
        path = Path(directory) / f"upload{suffix}"
        path.write_bytes(payload)
        try:
            if suffix == ".xlsx":
                with ZipFile(path) as archive:
                    if (
                        sum(item.file_size for item in archive.infolist())
                        > 16 * MAX_UPLOAD_BYTES
                    ):
                        raise ConfigError("XLSX 解壓後過大，請移除不必要的內容。")
            yield path
        except ConfigError as exc:
            raise ConfigError(str(exc).replace(str(path), cast(str, name))) from exc
        except (BadZipFile, OSError, ValueError, KeyError) as exc:
            raise ConfigError("檔案無法讀取，請確認格式與內容後重新選擇。") from exc


def import_targets(path: Path) -> list[dict[str, str]]:
    source = Path(path)
    if source.suffix.lower() in {".csv", ".tsv"}:
        from xic_extractor.configuration.legacy_target_methods import (
            upgrade_target_table,
        )

        return upgrade_target_table(read_target_draft_rows(source))[0]
    if source.suffix.lower() == ".xlsx":
        return [dict(row) for row in load_target_list(source).target_rows]
    raise ConfigError(f"{source}: 請選擇 XLSX、CSV 或 TSV 目標表；不支援 .xls。")

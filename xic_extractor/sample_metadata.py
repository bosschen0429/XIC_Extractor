from __future__ import annotations

import csv
import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

SAMPLE_METADATA_SCHEMA_VERSION = "sample_metadata"
SAMPLE_METADATA_COLUMNS: tuple[str, ...] = (
    "schema_version",
    "sample_name",
    "raw_stem",
    "injection_order",
    "sample_role",
    "batch_id",
    "prep_batch_id",
    "matrix_type",
    "group",
    "excluded",
    "exclusion_reason",
)
SampleRole = Literal[
    "study_sample",
    "qc",
    "pooled_qc",
    "blank",
    "calibrator",
    "solvent",
    "system_suitability",
    "unknown",
]
SAMPLE_ROLES: frozenset[str] = frozenset(
    {
        "study_sample",
        "qc",
        "pooled_qc",
        "blank",
        "calibrator",
        "solvent",
        "system_suitability",
        "unknown",
    }
)
_CANONICAL_SAMPLE_ID_PREFIX = "xic.sample:"
_SAMPLE_METADATA_SNAPSHOT_PREFIX = "xic.sample-metadata-snapshot:"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SEQUENCE_HEADER_ALIASES: Mapping[str, frozenset[str]] = {
    "order": frozenset(
        {"id", "no", "index", "order", "injection order", "序號", "編號", "項次"}
    ),
    "raw_stem": frozenset(
        {"file name", "filename", "raw file", "raw", "檔案", "檔名", "檔案名稱"}
    ),
    "sample_name": frozenset(
        {
            "sample",
            "sample name",
            "sample id",
            "樣品",
            "樣品名稱",
            "樣本",
            "樣本名稱",
        }
    ),
    "method": frozenset({"method", "instrument method", "方法"}),
    "sample_role": frozenset({"sample type", "type", "role", "類別", "類型", "角色"}),
    "group": frozenset({"group", "組別", "分組"}),
    "batch_id": frozenset({"batch", "batch id", "批次"}),
}
_SEQUENCE_HEADER_FIELD_BY_ALIAS = {
    alias: field
    for field, aliases in _SEQUENCE_HEADER_ALIASES.items()
    for alias in aliases
}


@dataclass(frozen=True)
class SampleMetadata:
    sample_name: str
    raw_stem: str
    injection_order: int | None = None
    sample_role: SampleRole = "unknown"
    batch_id: str = ""
    prep_batch_id: str = ""
    matrix_type: str = ""
    group: str = ""
    excluded: bool = False
    exclusion_reason: str = ""


class SampleMetadataError(ValueError):
    """Raised when sample metadata cannot be treated as a shared contract."""


@dataclass(frozen=True, slots=True)
class SequenceMatchReport:
    """Auditable projection of one sequence source onto the active RAW files."""

    rows: tuple[SampleMetadata, ...]
    matched_raw_stems: tuple[str, ...]
    unmatched_raw_stems: tuple[str, ...]
    unmatched_sequence_names: tuple[str, ...]
    ambiguous_sequence_names: tuple[str, ...]

    @property
    def matched_count(self) -> int:
        return len(self.matched_raw_stems)


@dataclass(frozen=True, slots=True)
class SampleMetadataSnapshot:
    """Owner-minted immutable snapshot for one sample identity namespace."""

    snapshot_id: str
    content_sha256: str
    identity_namespace: str
    rows: tuple[SampleMetadata, ...]

    def __post_init__(self) -> None:
        namespace = _owner_normalized_text(
            self.identity_namespace,
            field="identity_namespace",
        )
        content_sha256 = _validate_sha256(
            self.content_sha256,
            field="content_sha256",
        )
        rows = _validated_snapshot_rows(self.rows)
        if rows != self.rows:
            raise SampleMetadataError(
                "sample metadata snapshot rows must be canonical-sorted"
            )
        expected_content_sha256 = _sample_metadata_snapshot_content_sha256(
            rows,
            identity_namespace=namespace,
        )
        if content_sha256 != expected_content_sha256:
            raise SampleMetadataError(
                "sample metadata snapshot content digest mismatches owner rows"
            )
        expected_id = _sample_metadata_snapshot_id(content_sha256)
        if self.snapshot_id != expected_id:
            raise SampleMetadataError(
                "sample metadata snapshot ID mismatches owner content"
            )


@dataclass(frozen=True, slots=True)
class CanonicalSampleIdentity:
    """Owner-minted sample identity; aliases and projections are not its basis."""

    sample_identity_id: str
    sample_metadata_schema_version: str
    identity_namespace: str
    sample_key: str

    def __post_init__(self) -> None:
        if self.sample_metadata_schema_version != SAMPLE_METADATA_SCHEMA_VERSION:
            raise SampleMetadataError(
                "sample identity schema does not match sample_metadata"
            )
        namespace = _owner_normalized_text(
            self.identity_namespace,
            field="identity_namespace",
        )
        sample_key = _canonical_sample_key(self.sample_key)
        expected_id = _canonical_sample_identity_id(
            sample_key,
            identity_namespace=namespace,
        )
        if self.sample_identity_id != expected_id:
            raise SampleMetadataError(
                "sample_identity_id mismatches the owner-minted sample key"
            )


@dataclass(frozen=True, slots=True)
class ResolvedCanonicalSampleIdentity:
    """Self-validating owner resolution of one current metadata lookup token."""

    identity: CanonicalSampleIdentity
    snapshot: SampleMetadataSnapshot
    metadata: SampleMetadata
    lookup_token: str

    def __post_init__(self) -> None:
        if not isinstance(self.identity, CanonicalSampleIdentity):
            raise SampleMetadataError(
                "identity must be an owner-minted canonical sample identity"
            )
        if not isinstance(self.snapshot, SampleMetadataSnapshot):
            raise SampleMetadataError(
                "snapshot must be an owner-minted SampleMetadataSnapshot"
            )
        if not isinstance(self.metadata, SampleMetadata):
            raise SampleMetadataError(
                "metadata must be the owner row used for sample resolution"
            )
        if self.metadata not in self.snapshot.rows:
            raise SampleMetadataError(
                "resolved metadata row is not present in the owner snapshot"
            )
        token = _owner_normalized_text(self.lookup_token, field="lookup_token")
        if token not in {self.metadata.sample_name, self.metadata.raw_stem}:
            raise SampleMetadataError(
                "lookup_token does not bind the resolved sample metadata row"
            )
        if self.identity != mint_canonical_sample_identity(
            self.metadata,
            identity_namespace=self.snapshot.identity_namespace,
        ):
            raise SampleMetadataError(
                "resolved sample identity mismatches its owner metadata row"
            )


def infer_sample_role(
    sample_type: str | None,
    sample_name: str | None = None,
    comment: str | None = None,
) -> SampleRole:
    """Infer canonical SampleRole from Thermo SampleType and text metadata."""
    combined = f"{sample_name or ''} {comment or ''}".upper()
    if re.search(r"(?:^|[\W_])(?:POOLED|POOL)[_\s]?QC\d*(?:[\W_]|$)", combined):
        return "pooled_qc"
    if re.search(r"(?:^|[\W_])QC\d*(?:[\W_]|$)", combined):
        return "qc"
    if re.search(r"(?:^|[\W_])(?:BLANK|BLK)\d*(?:[\W_]|$)", combined):
        return "blank"
    if re.search(
        r"(?:^|[\W_])(?:SDOLEK|SDO[_-]LEK|SST|SUITABILITY)(?:[\W_]|$)", combined
    ):
        return "system_suitability"
    if re.search(
        r"(?:^|[\W_])(?:SOLVENT|WASH|STOPFLOW|COLUMNWASH)(?:[\W_]|$)", combined
    ):
        return "solvent"
    if re.search(
        r"(?:^|[\W_])(?:CAL|CALIBRATOR|STD|STANDARD|MIX[_\s]?STDS)\d*(?:[\W_]|$)",
        combined,
    ):
        return "calibrator"

    st = (sample_type or "").strip().lower()
    if st in {"unknown", "sample"}:
        return "study_sample"
    if st in SAMPLE_ROLES:
        return st  # type: ignore[return-value]
    if st in {"standard", "calibrator"}:
        return "calibrator"

    if sample_name:
        return "study_sample"
    return "unknown"


def normalize_for_matching(s: str) -> str:
    """Normalize file or sample string for resilient fuzzy matching."""
    s = re.sub(r"\.(raw|mzml|csv|tsv|txt)$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"tissue", "", s, flags=re.IGNORECASE)
    s = re.sub(r"and", "", s, flags=re.IGNORECASE)
    s = s.replace("+", "")
    return re.sub(r"[^a-zA-Z0-9]", "", s).lower()


def _parse_docx_sequence_table(docx_path: Path) -> list[dict[str, Any]]:
    """Adapt the shared CLI Word-sequence owner to sample metadata rows."""
    from xic_extractor.instrument_qc.sequence_manifest import parse_sequence_docx

    if not docx_path.is_file():
        raise SampleMetadataError(f"{docx_path}: file not found")
    try:
        entries = parse_sequence_docx(docx_path)
    except ValueError as exc:
        raise SampleMetadataError(str(exc)) from exc
    if not entries:
        raise SampleMetadataError(
            f"{docx_path.name}: 未在 Word 文件中找到有效的進樣序列表"
        )

    return _sequence_doc_entries_as_rows(entries)


def _sequence_doc_entries_as_rows(
    entries: Sequence[Any],
) -> list[dict[str, Any]]:
    return [
        {
            "order": entry.injection_order,
            "raw_stem": entry.doc_display_name,
            "sample_name": entry.sample_description or entry.doc_display_name,
            "sample_role": "",
            "method": entry.instrument_method,
            "group": "",
            "batch_id": "",
        }
        for entry in entries
    ]


def _normalize_sequence_header(value: object) -> str:
    text = str(value).strip().lower().replace("_", " ")
    return " ".join(re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff\s]", " ", text).split())


def _best_sequence_header(
    rows: Iterable[Sequence[object]],
) -> tuple[int, dict[str, int], tuple[str, ...], int] | None:
    best: tuple[int, dict[str, int], tuple[str, ...], int] | None = None
    best_score = -1
    for row_idx, row in enumerate(rows):
        header_map: dict[str, int] = {}
        duplicate_fields: set[str] = set()
        for col_idx, value in enumerate(row):
            field = _SEQUENCE_HEADER_FIELD_BY_ALIAS.get(
                _normalize_sequence_header(value)
            )
            if field is None:
                continue
            if field in header_map:
                duplicate_fields.add(field)
                continue
            header_map[field] = col_idx

        if "raw_stem" not in header_map and "sample_name" not in header_map:
            continue
        score = 2
        if "order" in header_map:
            score += 2
        if "method" in header_map or "sample_role" in header_map:
            score += 1
        if score > best_score:
            best_score = score
            best = (row_idx, header_map, tuple(sorted(duplicate_fields)), score)
            if score >= 5:
                break
    return best


def _require_sequence_header(
    candidate: tuple[int, dict[str, int], tuple[str, ...], int] | None,
    *,
    source_name: str,
    source_kind: str,
) -> tuple[int, dict[str, int]]:
    if candidate is None:
        raise SampleMetadataError(
            f"{source_name}: {source_kind} 缺少樣品識別標頭 "
            "(Sample ID/Sample Name/File Name/Raw File)"
        )
    row_idx, header_map, duplicate_fields, _score = candidate
    if duplicate_fields:
        fields = ", ".join(duplicate_fields)
        raise SampleMetadataError(
            f"{source_name}: {source_kind} 標頭語義不明確，欄位重複 ({fields})"
        )
    return row_idx, header_map


def _parse_xlsx_sequence_table(xlsx_path: Path) -> list[dict[str, Any]]:
    """Parse injection sequence table from Excel .xlsx workbook."""
    if not xlsx_path.is_file():
        raise SampleMetadataError(f"{xlsx_path}: file not found")
    try:
        import openpyxl

        wb = openpyxl.load_workbook(xlsx_path, read_only=True)
    except Exception as exc:
        raise SampleMetadataError(
            f"{xlsx_path}: failed to read Excel workbook: {exc}"
        ) from exc

    best_sheet = None
    best_candidate: tuple[int, dict[str, int], tuple[str, ...], int] | None = None
    best_score = -1

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        candidate = _best_sequence_header(ws.iter_rows(values_only=True, max_row=20))
        if candidate is not None and candidate[3] > best_score:
            best_score = candidate[3]
            best_sheet = ws
            best_candidate = candidate
        if best_score >= 5:
            break

    best_header_row, best_header_map = _require_sequence_header(
        best_candidate,
        source_name=xlsx_path.name,
        source_kind="Excel 工作表",
    )
    assert best_sheet is not None

    results: list[dict[str, Any]] = []
    auto_order = 1
    for row_idx, row in enumerate(best_sheet.iter_rows(values_only=True)):
        if row_idx <= best_header_row or not row or not any(row):
            continue
        cells = [str(c).strip() if c is not None else "" for c in row]

        order_val: int | None = None
        if "order" in best_header_map and best_header_map["order"] < len(cells):
            raw_order = cells[best_header_map["order"]]
            m = re.match(r"^\d+", raw_order)
            if m:
                order_val = int(m.group(0))

        file_name = (
            cells[best_header_map["raw_stem"]]
            if "raw_stem" in best_header_map
            and best_header_map["raw_stem"] < len(cells)
            else ""
        )
        sample_name = (
            cells[best_header_map["sample_name"]]
            if "sample_name" in best_header_map
            and best_header_map["sample_name"] < len(cells)
            else ""
        )
        role_text = (
            cells[best_header_map["sample_role"]]
            if "sample_role" in best_header_map
            and best_header_map["sample_role"] < len(cells)
            else ""
        )
        method_text = (
            cells[best_header_map["method"]]
            if "method" in best_header_map and best_header_map["method"] < len(cells)
            else ""
        )
        group_text = (
            cells[best_header_map["group"]]
            if "group" in best_header_map and best_header_map["group"] < len(cells)
            else ""
        )
        batch_text = (
            cells[best_header_map["batch_id"]]
            if "batch_id" in best_header_map
            and best_header_map["batch_id"] < len(cells)
            else ""
        )

        if not file_name and not sample_name:
            continue

        results.append(
            {
                "order": order_val if order_val is not None else auto_order,
                "raw_stem": file_name,
                "sample_name": sample_name or file_name,
                "sample_role": role_text,
                "method": method_text,
                "group": group_text,
                "batch_id": batch_text,
            }
        )
        auto_order += 1

    if not results:
        raise SampleMetadataError(f"{xlsx_path.name}: Excel 工作表沒有有效的樣品資料列")
    return results


def _parse_generic_tabular_sequence(path: Path) -> list[dict[str, Any]]:
    """Parse sequence table from generic CSV/TSV without schema_version."""
    if not path.is_file():
        raise SampleMetadataError(f"{path}: file not found")
    delimiter = "\t" if path.suffix.lower() in {".tsv", ".txt"} else ","
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.reader(handle, delimiter=delimiter))

    if not rows:
        raise SampleMetadataError(f"{path.name}: empty file")

    best_header_row, best_header_map = _require_sequence_header(
        _best_sequence_header(rows[:20]),
        source_name=path.name,
        source_kind="文字表格",
    )

    results: list[dict[str, Any]] = []
    auto_order = 1
    for row_idx, row in enumerate(rows):
        if row_idx <= best_header_row or not row or not any(row):
            continue
        cells = [c.strip() for c in row]

        order_val: int | None = None
        if "order" in best_header_map and best_header_map["order"] < len(cells):
            raw_order = cells[best_header_map["order"]]
            m = re.match(r"^\d+", raw_order)
            if m:
                order_val = int(m.group(0))

        file_name = (
            cells[best_header_map["raw_stem"]]
            if "raw_stem" in best_header_map
            and best_header_map["raw_stem"] < len(cells)
            else ""
        )
        sample_name = (
            cells[best_header_map["sample_name"]]
            if "sample_name" in best_header_map
            and best_header_map["sample_name"] < len(cells)
            else ""
        )
        role_text = (
            cells[best_header_map["sample_role"]]
            if "sample_role" in best_header_map
            and best_header_map["sample_role"] < len(cells)
            else ""
        )
        method_text = (
            cells[best_header_map["method"]]
            if "method" in best_header_map and best_header_map["method"] < len(cells)
            else ""
        )
        group_text = (
            cells[best_header_map["group"]]
            if "group" in best_header_map and best_header_map["group"] < len(cells)
            else ""
        )
        batch_text = (
            cells[best_header_map["batch_id"]]
            if "batch_id" in best_header_map
            and best_header_map["batch_id"] < len(cells)
            else ""
        )

        if not file_name and not sample_name:
            continue

        results.append(
            {
                "order": order_val if order_val is not None else auto_order,
                "raw_stem": file_name,
                "sample_name": sample_name or file_name,
                "sample_role": role_text,
                "method": method_text,
                "group": group_text,
                "batch_id": batch_text,
            }
        )
        auto_order += 1

    if not results:
        raise SampleMetadataError(f"{path.name}: 文字表格沒有有效的樣品資料列")
    return results


def parse_sequence_file(path: Path) -> tuple[SampleMetadata, ...]:
    """Parse laboratory sequence list from Word (.docx), Excel (.xlsx), or CSV/TSV."""
    suffix = path.suffix.lower()
    if suffix == ".docx":
        raw_entries = _parse_docx_sequence_table(path)
    elif suffix in {".xlsx", ".xls"}:
        raw_entries = _parse_xlsx_sequence_table(path)
    elif suffix in {".csv", ".tsv", ".txt"}:
        raw_entries = _parse_generic_tabular_sequence(path)
    else:
        raise SampleMetadataError(f"{path.name}: 不支援的序列表檔案格式 ({suffix})")
    return _metadata_from_sequence_entries(raw_entries)


def _metadata_from_sequence_entries(
    raw_entries: Sequence[Mapping[str, Any]],
) -> tuple[SampleMetadata, ...]:
    metadata_rows: list[SampleMetadata] = []
    seen_names: set[str] = set()
    seen_stems: set[str] = set()

    for idx, entry in enumerate(raw_entries, start=1):
        raw_stem = Path(entry["raw_stem"]).stem if entry["raw_stem"] else ""
        sample_name = entry["sample_name"] or raw_stem or f"sample_{idx}"

        if sample_name in seen_names:
            if raw_stem and raw_stem not in seen_names:
                sample_name = raw_stem
            else:
                sample_name = f"{sample_name}_{entry['order'] or idx}"
        seen_names.add(sample_name)

        if not raw_stem:
            raw_stem = sample_name

        if raw_stem in seen_stems:
            raw_stem = f"{raw_stem}_{entry['order'] or idx}"
        seen_stems.add(raw_stem)

        role = infer_sample_role(
            entry.get("sample_role"),
            sample_name=sample_name,
            comment=f"{entry.get('method', '')} {raw_stem}",
        )

        group = entry.get("group", "")
        if not group:
            search_text = f"{sample_name} {raw_stem}".lower()
            if "tumor" in search_text:
                group = "Tumor"
            elif "normal" in search_text:
                group = "Normal"
            elif "benign" in search_text:
                group = "Benign"
            elif role == "pooled_qc":
                group = "QC"

        metadata_rows.append(
            SampleMetadata(
                sample_name=sample_name,
                raw_stem=raw_stem,
                injection_order=entry["order"],
                sample_role=role,
                batch_id=entry.get("batch_id", ""),
                prep_batch_id="",
                matrix_type="",
                group=group,
                excluded=False,
                exclusion_reason="",
            )
        )

    return tuple(metadata_rows)


def load_and_match_sample_metadata(
    path: Path,
    raw_stems: Sequence[str],
) -> SequenceMatchReport:
    """Load a sequence source and project it onto the active RAW stems."""
    if path.suffix.lower() != ".docx":
        metadata = load_sample_metadata(path)
        return match_sequence_to_raw_stems_report(metadata, raw_stems)

    from xic_extractor.instrument_qc.sequence_manifest import (
        ManifestMatchStatus,
        build_sequence_manifest_from_entries,
        parse_sequence_docx,
    )

    if not path.is_file():
        raise SampleMetadataError(f"{path}: file not found")
    try:
        entries = parse_sequence_docx(path)
    except ValueError as exc:
        raise SampleMetadataError(str(exc)) from exc
    if not entries:
        raise SampleMetadataError(f"{path.name}: 未在 Word 文件中找到有效的進樣序列表")
    metadata = _metadata_from_sequence_entries(_sequence_doc_entries_as_rows(entries))

    manifest = build_sequence_manifest_from_entries(
        entries,
        raw_stems=tuple(raw_stems),
    )
    metadata_by_source = {
        entry.source_section: row for entry, row in zip(entries, metadata, strict=True)
    }
    matched_by_raw: dict[str, SampleMetadata] = {}
    unmatched_sequence_names: list[str] = []
    ambiguous_sequence_names: list[str] = []
    for manifest_row in manifest:
        source_row = metadata_by_source.get(manifest_row.source_section)
        if source_row is None:
            continue
        if manifest_row.match_status == ManifestMatchStatus.MATCHED:
            matched_by_raw[manifest_row.raw_stem] = source_row
        elif manifest_row.match_status == ManifestMatchStatus.AMBIGUOUS:
            ambiguous_sequence_names.append(source_row.raw_stem)
        elif manifest_row.match_status == ManifestMatchStatus.UNMATCHED:
            unmatched_sequence_names.append(source_row.raw_stem)

    return _sequence_match_report(
        metadata,
        raw_stems,
        matched_by_raw=matched_by_raw,
        unmatched_sequence_names=unmatched_sequence_names,
        ambiguous_sequence_names=ambiguous_sequence_names,
    )


def match_sequence_to_raw_stems_report(
    sequence_rows: Sequence[SampleMetadata],
    raw_stems: Sequence[str],
) -> SequenceMatchReport:
    """Match a non-Word sequence source and retain ambiguity/unmatched evidence."""
    raw_stem_order = tuple(raw_stems)
    normalized_raws: dict[str, list[str]] = {}
    for stem in raw_stem_order:
        normalized_raws.setdefault(normalize_for_matching(stem), []).append(stem)

    matched_by_raw: dict[str, SampleMetadata] = {}
    unmatched_sequence_names: list[str] = []
    ambiguous_sequence_names: list[str] = []
    for row in sequence_rows:
        keys = {
            normalize_for_matching(value)
            for value in (row.raw_stem, row.sample_name)
            if value
        }
        candidates = {
            stem
            for key in keys
            for stem in normalized_raws.get(key, ())
            if stem not in matched_by_raw
        }
        if len(candidates) == 1:
            matched_by_raw[candidates.pop()] = row
        elif len(candidates) > 1:
            ambiguous_sequence_names.append(row.raw_stem or row.sample_name)
        else:
            unmatched_sequence_names.append(row.raw_stem or row.sample_name)

    return _sequence_match_report(
        sequence_rows,
        raw_stem_order,
        matched_by_raw=matched_by_raw,
        unmatched_sequence_names=unmatched_sequence_names,
        ambiguous_sequence_names=ambiguous_sequence_names,
    )


def match_sequence_to_raw_stems(
    sequence_rows: Sequence[SampleMetadata],
    raw_stems: Sequence[str],
) -> tuple[SampleMetadata, ...]:
    """Compatibility projection for callers that only consume matched rows."""
    return match_sequence_to_raw_stems_report(sequence_rows, raw_stems).rows


def _sequence_match_report(
    sequence_rows: Sequence[SampleMetadata],
    raw_stems: Sequence[str],
    *,
    matched_by_raw: Mapping[str, SampleMetadata],
    unmatched_sequence_names: Sequence[str],
    ambiguous_sequence_names: Sequence[str],
) -> SequenceMatchReport:
    raw_stem_order = tuple(raw_stems)
    if not raw_stem_order:
        return SequenceMatchReport(
            rows=tuple(sequence_rows),
            matched_raw_stems=(),
            unmatched_raw_stems=(),
            unmatched_sequence_names=tuple(unmatched_sequence_names),
            ambiguous_sequence_names=tuple(ambiguous_sequence_names),
        )

    rows: list[SampleMetadata] = []
    matched_raw_stems: list[str] = []
    unmatched_raw_stems: list[str] = []
    for stem in raw_stem_order:
        source_row = matched_by_raw.get(stem)
        if source_row is None:
            unmatched_raw_stems.append(stem)
            rows.append(
                SampleMetadata(
                    sample_name=stem,
                    raw_stem=stem,
                    injection_order=None,
                    sample_role=infer_sample_role("unknown", sample_name=stem),
                )
            )
            continue

        matched_raw_stems.append(stem)
        rows.append(
            SampleMetadata(
                sample_name=source_row.sample_name,
                raw_stem=stem,
                injection_order=source_row.injection_order,
                sample_role=source_row.sample_role,
                batch_id=source_row.batch_id,
                prep_batch_id=source_row.prep_batch_id,
                matrix_type=source_row.matrix_type,
                group=source_row.group,
                excluded=source_row.excluded,
                exclusion_reason=source_row.exclusion_reason,
            )
        )

    return SequenceMatchReport(
        rows=tuple(rows),
        matched_raw_stems=tuple(matched_raw_stems),
        unmatched_raw_stems=tuple(unmatched_raw_stems),
        unmatched_sequence_names=tuple(unmatched_sequence_names),
        ambiguous_sequence_names=tuple(ambiguous_sequence_names),
    )


def load_sample_metadata(path: Path) -> tuple[SampleMetadata, ...]:
    suffix = path.suffix.lower()
    if suffix in {".docx", ".xlsx", ".xls"}:
        return parse_sequence_file(path)
    if is_sample_metadata_source(path):
        return parse_sample_metadata(
            _read_sample_metadata_rows(path),
            source=str(path),
        )
    return parse_sequence_file(path)


def is_sample_metadata_source(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix in {".docx", ".xlsx", ".xls"}:
        return path.is_file()
    if suffix not in {".csv", ".tsv", ".txt"} or not path.is_file():
        return False
    delimiter = "\t" if suffix in {".tsv", ".txt"} else ","
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle, delimiter=delimiter)
            fieldnames = set(reader.fieldnames or ())
        return "schema_version" in fieldnames and "sample_name" in fieldnames
    except Exception:
        return False


def parse_sample_metadata(
    rows: Iterable[Mapping[str, object]],
    *,
    source: str = "<memory>",
) -> tuple[SampleMetadata, ...]:
    parsed: list[SampleMetadata] = []
    seen_sample_names: set[str] = set()
    seen_raw_stems: set[str] = set()
    seen_aliases: dict[str, tuple[int, str]] = {}
    for index, row in enumerate(rows, start=2):
        normalized = _normalize_row(row)
        if not any(normalized.values()):
            continue
        item = _parse_sample_metadata_row(normalized, source=source, row_number=index)
        _check_unique(item.sample_name, seen_sample_names, "sample_name", source, index)
        if item.raw_stem:
            _check_unique(item.raw_stem, seen_raw_stems, "raw_stem", source, index)
        _register_alias(
            item.sample_name,
            "sample_name",
            seen_aliases,
            source,
            index,
        )
        if item.raw_stem:
            _register_alias(
                item.raw_stem,
                "raw_stem",
                seen_aliases,
                source,
                index,
            )
        parsed.append(item)
    return tuple(parsed)


def mint_canonical_sample_identity(
    row: SampleMetadata,
    *,
    identity_namespace: str,
) -> CanonicalSampleIdentity:
    """Mint the canonical identity from the owner-declared primary key."""

    if not isinstance(row, SampleMetadata):
        raise SampleMetadataError("sample identity must be minted from SampleMetadata")
    namespace = _owner_normalized_text(
        identity_namespace,
        field="identity_namespace",
    )
    sample_key = _canonical_sample_key(row.sample_name)
    return CanonicalSampleIdentity(
        sample_identity_id=_canonical_sample_identity_id(
            sample_key,
            identity_namespace=namespace,
        ),
        sample_metadata_schema_version=SAMPLE_METADATA_SCHEMA_VERSION,
        identity_namespace=namespace,
        sample_key=sample_key,
    )


def mint_sample_metadata_snapshot(
    rows: Sequence[SampleMetadata],
    *,
    identity_namespace: str,
) -> SampleMetadataSnapshot:
    """Mint one immutable, order-neutral owner snapshot without persistence."""

    namespace = _owner_normalized_text(
        identity_namespace,
        field="identity_namespace",
    )
    canonical_rows = _validated_snapshot_rows(tuple(rows))
    content_sha256 = _sample_metadata_snapshot_content_sha256(
        canonical_rows,
        identity_namespace=namespace,
    )
    return SampleMetadataSnapshot(
        snapshot_id=_sample_metadata_snapshot_id(content_sha256),
        content_sha256=content_sha256,
        identity_namespace=namespace,
        rows=canonical_rows,
    )


def resolve_canonical_sample_identity(
    snapshot: SampleMetadataSnapshot,
    token: str,
    *,
    expected_identity: CanonicalSampleIdentity | None = None,
) -> ResolvedCanonicalSampleIdentity:
    """Resolve one exact owner alias and optionally reject stale rebinding."""

    if not isinstance(snapshot, SampleMetadataSnapshot):
        raise SampleMetadataError(
            "sample resolution requires an owner-minted SampleMetadataSnapshot"
        )
    clean_token = _owner_normalized_text(token, field="sample identity token")
    matches = [
        row
        for row in snapshot.rows
        if clean_token == row.sample_name or clean_token == row.raw_stem
    ]
    if not matches:
        raise SampleMetadataError(
            f"sample identity token {clean_token!r} is not owned by sample metadata"
        )
    if len(matches) != 1:
        raise SampleMetadataError(f"sample identity token {clean_token!r} is ambiguous")
    resolved = mint_canonical_sample_identity(
        matches[0],
        identity_namespace=snapshot.identity_namespace,
    )
    if expected_identity is not None:
        if not isinstance(expected_identity, CanonicalSampleIdentity):
            raise SampleMetadataError(
                "expected_identity must be an owner-minted sample identity"
            )
        if resolved != expected_identity:
            raise SampleMetadataError(
                f"sample identity token {clean_token!r} resolved to a stale binding"
            )
    return ResolvedCanonicalSampleIdentity(
        identity=resolved,
        snapshot=snapshot,
        metadata=matches[0],
        lookup_token=clean_token,
    )


def sample_metadata_to_injection_order(
    rows: Sequence[SampleMetadata],
) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        if row.injection_order is None:
            continue
        _add_injection_order(out, row.sample_name, row.injection_order)
        if row.raw_stem and row.raw_stem != row.sample_name:
            _add_injection_order(out, row.raw_stem, row.injection_order)
    return out


def sample_metadata_to_raw_stem_injection_order(
    rows: Sequence[SampleMetadata],
) -> dict[str, int]:
    """Project exactly one request-local RAW stem key for each ordered sample."""

    out: dict[str, int] = {}
    for row in rows:
        if row.injection_order is None:
            continue
        raw_stem = row.raw_stem or row.sample_name
        _add_injection_order(out, raw_stem, row.injection_order)
    return out


def summarize_sample_metadata(rows: Sequence[SampleMetadata]) -> dict[str, object]:
    role_counts = {role: 0 for role in sorted(SAMPLE_ROLES)}
    excluded_count = 0
    with_injection_order = 0
    for row in rows:
        role_counts[row.sample_role] += 1
        if row.excluded:
            excluded_count += 1
        if row.injection_order is not None:
            with_injection_order += 1
    return {
        "schema_version": SAMPLE_METADATA_SCHEMA_VERSION,
        "sample_count": len(rows),
        "with_injection_order_count": with_injection_order,
        "excluded_count": excluded_count,
        "role_counts": role_counts,
    }


def _read_sample_metadata_rows(path: Path) -> list[Mapping[str, object]]:
    if not path.is_file():
        raise SampleMetadataError(f"{path}: sample metadata file not found")
    delimiter = "\t" if path.suffix.lower() in {".tsv", ".txt"} else ","
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        if reader.fieldnames is None:
            raise SampleMetadataError(f"{path}: missing sample metadata header")
        missing = [
            header
            for header in SAMPLE_METADATA_COLUMNS
            if header not in reader.fieldnames
        ]
        if missing:
            raise SampleMetadataError(
                f"{path}: missing sample metadata columns: {', '.join(missing)}"
            )
        return list(reader)


def _parse_sample_metadata_row(
    row: Mapping[str, str],
    *,
    source: str,
    row_number: int,
) -> SampleMetadata:
    schema_version = row["schema_version"]
    if schema_version != SAMPLE_METADATA_SCHEMA_VERSION:
        raise SampleMetadataError(
            f"{source}:{row_number}: unsupported schema_version {schema_version!r}"
        )
    sample_name = _required_text(row, "sample_name", source, row_number)
    raw_stem = row["raw_stem"] or sample_name
    injection_order = _optional_int(
        row["injection_order"],
        "injection_order",
        source,
        row_number,
    )
    sample_role = row["sample_role"] or "unknown"
    if sample_role not in SAMPLE_ROLES:
        raise SampleMetadataError(
            f"{source}:{row_number}: unsupported sample_role {sample_role!r}"
        )
    excluded = _parse_bool(row["excluded"], "excluded", source, row_number)
    if excluded and not row["exclusion_reason"]:
        raise SampleMetadataError(
            f"{source}:{row_number}: excluded samples require exclusion_reason"
        )
    return SampleMetadata(
        sample_name=sample_name,
        raw_stem=raw_stem,
        injection_order=injection_order,
        sample_role=sample_role,  # type: ignore[arg-type]
        batch_id=row["batch_id"],
        prep_batch_id=row["prep_batch_id"],
        matrix_type=row["matrix_type"],
        group=row["group"],
        excluded=excluded,
        exclusion_reason=row["exclusion_reason"],
    )


def _normalize_row(row: Mapping[str, object]) -> dict[str, str]:
    return {
        header: _clean_text(row.get(header, "")) for header in SAMPLE_METADATA_COLUMNS
    }


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _required_text(
    row: Mapping[str, str],
    key: str,
    source: str,
    row_number: int,
) -> str:
    value = row[key]
    if not value:
        raise SampleMetadataError(f"{source}:{row_number}: {key} is required")
    return value


def _optional_int(
    value: str,
    key: str,
    source: str,
    row_number: int,
) -> int | None:
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise SampleMetadataError(
            f"{source}:{row_number}: {key} must be an integer"
        ) from exc
    if parsed < 1:
        raise SampleMetadataError(f"{source}:{row_number}: {key} must be >= 1")
    return parsed


def _parse_bool(
    value: str,
    key: str,
    source: str,
    row_number: int,
) -> bool:
    if not value:
        return False
    normalized = value.upper()
    if normalized == "TRUE":
        return True
    if normalized == "FALSE":
        return False
    raise SampleMetadataError(f"{source}:{row_number}: {key} must be TRUE or FALSE")


def _check_unique(
    value: str,
    seen: set[str],
    key: str,
    source: str,
    row_number: int,
) -> None:
    if value in seen:
        raise SampleMetadataError(f"{source}:{row_number}: duplicate {key} {value!r}")
    seen.add(value)


def _register_alias(
    value: str,
    alias_type: str,
    seen: dict[str, tuple[int, str]],
    source: str,
    row_number: int,
) -> None:
    existing = seen.get(value)
    if existing is not None and existing[0] != row_number:
        existing_row, existing_type = existing
        raise SampleMetadataError(
            f"{source}:{row_number}: sample metadata alias collision {value!r}; "
            f"already used as {existing_type} on row {existing_row}"
        )
    seen[value] = (row_number, alias_type)


def _add_injection_order(out: dict[str, int], sample: str, order: int) -> None:
    existing = out.get(sample)
    if existing is not None and existing != order:
        raise SampleMetadataError(
            f"conflicting injection_order for sample {sample!r}: {existing} != {order}"
        )
    out[sample] = order


def _canonical_sample_key(value: object) -> str:
    return _owner_normalized_text(value, field="sample_key")


def _owner_normalized_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise SampleMetadataError(f"{field} must be non-empty owner-normalized text")
    return value


def _canonical_sample_identity_id(
    sample_key: str,
    *,
    identity_namespace: str,
) -> str:
    digest = _canonical_json_sha256(
        {
            "identity_namespace": identity_namespace,
            "sample_key": sample_key,
            "sample_metadata_schema_version": SAMPLE_METADATA_SCHEMA_VERSION,
            "schema": "canonical_sample_identity",
        }
    )
    return f"{_CANONICAL_SAMPLE_ID_PREFIX}{digest}"


def _validated_snapshot_rows(
    rows: tuple[SampleMetadata, ...],
) -> tuple[SampleMetadata, ...]:
    if not rows:
        raise SampleMetadataError("sample metadata snapshot must contain rows")
    if any(not isinstance(row, SampleMetadata) for row in rows):
        raise SampleMetadataError(
            "sample metadata snapshot rows must be SampleMetadata"
        )
    serialized = [
        {
            "schema_version": SAMPLE_METADATA_SCHEMA_VERSION,
            "sample_name": row.sample_name,
            "raw_stem": row.raw_stem,
            "injection_order": row.injection_order,
            "sample_role": row.sample_role,
            "batch_id": row.batch_id,
            "prep_batch_id": row.prep_batch_id,
            "matrix_type": row.matrix_type,
            "group": row.group,
            "excluded": "TRUE" if row.excluded else "FALSE",
            "exclusion_reason": row.exclusion_reason,
        }
        for row in rows
    ]
    parsed = parse_sample_metadata(
        serialized,
        source="<sample-metadata-snapshot>",
    )
    canonical_input = tuple(
        sorted(rows, key=lambda row: (row.sample_name, row.raw_stem))
    )
    canonical_parsed = tuple(
        sorted(parsed, key=lambda row: (row.sample_name, row.raw_stem))
    )
    if canonical_input != canonical_parsed:
        raise SampleMetadataError(
            "sample metadata snapshot rows must be owner-normalized"
        )
    return canonical_parsed


def _sample_metadata_snapshot_content_sha256(
    rows: tuple[SampleMetadata, ...],
    *,
    identity_namespace: str,
) -> str:
    return _canonical_json_sha256(
        {
            "schema": "sample_metadata_snapshot",
            "sample_metadata_schema_version": SAMPLE_METADATA_SCHEMA_VERSION,
            "identity_namespace": identity_namespace,
            "rows": [
                {
                    "sample_name": row.sample_name,
                    "raw_stem": row.raw_stem,
                    "injection_order": row.injection_order,
                    "sample_role": row.sample_role,
                    "batch_id": row.batch_id,
                    "prep_batch_id": row.prep_batch_id,
                    "matrix_type": row.matrix_type,
                    "group": row.group,
                    "excluded": row.excluded,
                    "exclusion_reason": row.exclusion_reason,
                }
                for row in rows
            ],
        }
    )


def _sample_metadata_snapshot_id(content_sha256: str) -> str:
    return f"{_SAMPLE_METADATA_SNAPSHOT_PREFIX}{content_sha256}"


def _canonical_json_sha256(value: Mapping[str, object]) -> str:
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SampleMetadataError(
            "sample identity content must be canonical JSON"
        ) from exc
    return hashlib.sha256(payload).hexdigest()


def _validate_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise SampleMetadataError(f"{field} must be a lower-case SHA-256 digest")
    return value

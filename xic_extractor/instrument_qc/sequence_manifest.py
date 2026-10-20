from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from xic_extractor.instrument_qc.classification import InstrumentQCClass
from xic_extractor.instrument_qc.models import ActivationMethod


class ManifestMatchStatus(StrEnum):
    MATCHED = "matched"
    UNMATCHED = "unmatched"
    AMBIGUOUS = "ambiguous"
    MANUAL_REVIEW = "manual_review"


class ManifestMatchConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class SequenceDocEntry:
    source_doc: str
    source_section: str
    doc_display_name: str
    injection_order: int
    instrument_method: str
    sample_description: str
    injection_volume: str
    activation_method: ActivationMethod = "unknown"


@dataclass(frozen=True)
class SequenceManifestRow:
    source_doc: str
    source_section: str
    doc_display_name: str
    raw_stem: str
    injection_order: int | None
    instrument_qc_class: InstrumentQCClass
    match_status: ManifestMatchStatus
    match_confidence: ManifestMatchConfidence
    match_reason: str
    instrument_method: str = ""
    activation_method: ActivationMethod = "unknown"


def build_sequence_manifest(
    *,
    method_doc: Path,
    raw_dir: Path,
) -> tuple[SequenceManifestRow, ...]:
    """Build an auditable docs-derived sequence manifest."""
    entries = parse_sequence_docx(method_doc)
    raw_stems = discover_raw_stems(raw_dir)
    return build_sequence_manifest_from_entries(
        entries,
        raw_stems=raw_stems,
    )


def parse_sequence_docx(path: Path) -> tuple[SequenceDocEntry, ...]:
    """Parse sequence table rows from a Word method / sequence document."""
    try:
        tables = _docx_tables(path)
    except (BadZipFile, KeyError, ET.ParseError) as exc:
        raise ValueError(f"unable to parse method DOCX: {path}") from exc
    return parse_sequence_tables(tables, source_doc=str(path))


def parse_sequence_tables(
    tables: list[list[list[str]]],
    *,
    source_doc: str,
) -> tuple[SequenceDocEntry, ...]:
    method_activation_map = _method_activation_map(tables)
    entries: list[SequenceDocEntry] = []
    for table_index, table in enumerate(tables, start=1):
        if not table:
            continue
        header = [_normalize_header(cell) for cell in table[0]]
        if header[:5] != ["id", "file name", "instrument method", "sample", "inj"]:
            continue
        source_section = f"table:{table_index}"
        for row_index, cells in enumerate(table[1:], start=2):
            if len(cells) < 5:
                continue
            row_id = cells[0].strip()
            if not row_id.isdigit():
                continue
            file_name = _clean_cell(cells[1])
            if not file_name:
                continue
            instrument_method = _clean_cell(cells[2])
            entries.append(
                SequenceDocEntry(
                    source_doc=source_doc,
                    source_section=f"{source_section}:row:{row_index}",
                    doc_display_name=file_name,
                    injection_order=int(row_id),
                    instrument_method=instrument_method,
                    activation_method=method_activation_map.get(
                        _method_key(instrument_method),
                        activation_method_from_instrument_method(instrument_method),
                    ),
                    sample_description=_clean_cell(cells[3]),
                    injection_volume=_clean_cell(cells[4]),
                )
            )
    return tuple(entries)


def build_sequence_manifest_from_entries(
    entries: tuple[SequenceDocEntry, ...],
    *,
    raw_stems: tuple[str, ...],
) -> tuple[SequenceManifestRow, ...]:
    raw_index = _raw_stem_index(raw_stems)
    repeated_index = _repeated_raw_stem_index(raw_stems)
    duplicate_doc_counts = _duplicate_doc_counts(entries)
    class_method_defaults = _class_method_defaults(entries)
    duplicate_doc_seen: dict[str, int] = {}
    rows: list[SequenceManifestRow] = []
    matched_raw_stems: set[str] = set()
    for entry in entries:
        instrument_qc_class = classify_sequence_entry(entry)
        candidate = normalize_doc_display_name(entry.doc_display_name)
        match_key = _match_key(candidate)
        candidates = raw_index.get(match_key, ())
        if duplicate_doc_counts.get(match_key, 0) > 1:
            seen_count = duplicate_doc_seen.get(match_key, 0)
            duplicate_doc_seen[match_key] = seen_count + 1
            repeated_candidates = repeated_index.get(match_key, ())
            if seen_count < len(repeated_candidates):
                candidates = (repeated_candidates[seen_count],)
            elif repeated_candidates:
                candidates = ()
        if len(candidates) == 1:
            raw_stem = candidates[0]
            matched_raw_stems.add(raw_stem)
            reason = "Normalized doc display name matched one RAW stem."
            if duplicate_doc_counts.get(match_key, 0) > 1:
                reason = "Repeated doc display name matched RAW stem by sequence order."
            rows.append(
                _manifest_row(
                    entry,
                    raw_stem=raw_stem,
                    instrument_qc_class=instrument_qc_class,
                    status=ManifestMatchStatus.MATCHED,
                    confidence=ManifestMatchConfidence.HIGH,
                    reason=reason,
                )
            )
        elif len(candidates) > 1:
            rows.append(
                _manifest_row(
                    entry,
                    raw_stem=candidate,
                    instrument_qc_class=instrument_qc_class,
                    status=ManifestMatchStatus.AMBIGUOUS,
                    confidence=ManifestMatchConfidence.LOW,
                    reason=(
                        "Normalized doc display name matched multiple RAW stems: "
                        + ";".join(candidates)
                    ),
                )
            )
        else:
            rows.append(
                _manifest_row(
                    entry,
                    raw_stem=candidate,
                    instrument_qc_class=instrument_qc_class,
                    status=ManifestMatchStatus.UNMATCHED,
                    confidence=ManifestMatchConfidence.LOW,
                    reason="No RAW stem matched normalized doc display name.",
                )
            )

    documented_keys = {
        _match_key(normalize_doc_display_name(entry.doc_display_name))
        for entry in entries
    }
    for raw_stem in raw_stems:
        if raw_stem in matched_raw_stems:
            continue
        if _match_key(raw_stem) in documented_keys:
            continue
        instrument_qc_class = _classify_name(raw_stem, "", "")
        if instrument_qc_class == InstrumentQCClass.UNKNOWN:
            continue
        default_method = class_method_defaults.get(instrument_qc_class)
        instrument_method = default_method[0] if default_method is not None else ""
        activation_method = (
            default_method[1] if default_method is not None else "unknown"
        )
        rows.append(
            SequenceManifestRow(
                source_doc="",
                source_section="raw_dir",
                doc_display_name="",
                raw_stem=raw_stem,
                injection_order=None,
                instrument_qc_class=instrument_qc_class,
                match_status=ManifestMatchStatus.UNMATCHED,
                match_confidence=ManifestMatchConfidence.LOW,
                match_reason="RAW stem has no matching method-doc sequence entry.",
                instrument_method=instrument_method,
                activation_method=activation_method,
            )
        )
    return tuple(rows)


def discover_raw_stems(raw_dir: Path) -> tuple[str, ...]:
    return tuple(sorted({path.stem for path in raw_dir.rglob("*.raw")}))


def classify_sequence_entry(entry: SequenceDocEntry) -> InstrumentQCClass:
    return _classify_name(
        entry.doc_display_name,
        entry.instrument_method,
        entry.sample_description,
    )


def activation_method_from_instrument_method(method: str) -> ActivationMethod:
    """Parse acquisition activation from method-doc text only."""
    return _activation_method_from_text(method)


def activation_method_from_method_detail(
    method: str,
    detail_text: str,
) -> ActivationMethod:
    """Prefer method detail tables when the short method name lacks activation."""
    detail_activation = _activation_method_from_text(detail_text)
    if detail_activation != "unknown":
        return detail_activation
    return activation_method_from_instrument_method(method)


def _activation_method_from_text(text: str) -> ActivationMethod:
    normalized = re.sub(r"[^a-z0-9]+", "", text.casefold())
    if "cidwhcd" in normalized or ("cid" in normalized and "whcd" in normalized):
        return "CIDwHCD"
    if "whcd" in normalized:
        return "wHCD"
    if "hcd" in normalized:
        return "HCD"
    if "cid" in normalized:
        return "CID"
    return "unknown"


def normalize_doc_display_name(name: str) -> str:
    value = _clean_cell(name)
    value = value.replace("*", "")
    value = re.sub(r"\s*_\s*", "_", value)
    value = re.sub(r"\s+", " ", value).strip()

    match = re.fullmatch(r"(Tumor|Normal) tissue (BC\d+)_DNA", value)
    if match:
        tissue, case_id = match.groups()
        return f"{tissue}{case_id}_DNA"

    match = re.fullmatch(r"Benign fat (BC\d+)_DNA", value)
    if match:
        return f"Benignfat{match.group(1)}_DNA"

    match = re.fullmatch(
        r"(Tumor|Normal) tissue (BC\d+)(?:_)?\s*DNA\s*\+RNA",
        value,
    )
    if match:
        tissue, case_id = match.groups()
        return f"{tissue}{case_id}_DNAandRNA"

    if value.startswith("Breast Cancer Tissue"):
        normalized = value.replace(" ", "_")
        normalized = re.sub(r"_+", "_", normalized)
        normalized = normalized.replace("_pooled_QC_", "_pooled_QC")
        return normalized

    return value.replace(" ", "_") if value.startswith("SDO LEK") else value


def _manifest_row(
    entry: SequenceDocEntry,
    *,
    raw_stem: str,
    instrument_qc_class: InstrumentQCClass,
    status: ManifestMatchStatus,
    confidence: ManifestMatchConfidence,
    reason: str,
) -> SequenceManifestRow:
    return SequenceManifestRow(
        source_doc=entry.source_doc,
        source_section=entry.source_section,
        doc_display_name=entry.doc_display_name,
        raw_stem=raw_stem,
        injection_order=entry.injection_order,
        instrument_method=entry.instrument_method,
        activation_method=entry.activation_method,
        instrument_qc_class=instrument_qc_class,
        match_status=status,
        match_confidence=confidence,
        match_reason=reason,
    )


def _docx_tables(path: Path) -> list[list[list[str]]]:
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    with ZipFile(path) as zf:
        document_xml = zf.read("word/document.xml")
    root = ET.fromstring(document_xml)
    tables: list[list[list[str]]] = []
    for table in root.findall(".//w:tbl", ns):
        rows: list[list[str]] = []
        for table_row in table.findall("./w:tr", ns):
            cells = [_cell_text(cell, ns) for cell in table_row.findall("./w:tc", ns)]
            rows.append(cells)
        tables.append(rows)
    return tables


def _cell_text(cell: ET.Element, ns: dict[str, str]) -> str:
    paragraphs: list[str] = []
    for paragraph in cell.findall(".//w:p", ns):
        text = "".join(node.text or "" for node in paragraph.findall(".//w:t", ns))
        text = _clean_cell(text)
        if text:
            paragraphs.append(text)
    return " ".join(paragraphs)


def _clean_cell(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _normalize_header(value: str) -> str:
    return _clean_cell(value).casefold()


def _method_activation_map(
    tables: list[list[list[str]]],
) -> dict[str, ActivationMethod]:
    values: dict[str, ActivationMethod] = {}
    for table in tables:
        if not table or not table[0] or len(table[0]) < 2:
            continue
        method_slot = _clean_cell(table[0][0]).casefold()
        if not method_slot.startswith("method-"):
            continue
        method_name = _clean_cell(table[0][1])
        if not method_name:
            continue
        detail_text = " ".join(_clean_table_cells(table))
        activation = activation_method_from_method_detail(method_name, detail_text)
        if activation != "unknown":
            values[_method_key(method_name)] = activation
    return values


def _clean_table_cells(table: list[list[str]]) -> tuple[str, ...]:
    values: list[str] = []
    for row in table:
        for cell in row:
            text = _clean_cell(cell)
            if text:
                values.append(text)
    return tuple(values)


def _method_key(value: str) -> str:
    return _match_key(value)


def _raw_stem_index(raw_stems: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    values: dict[str, list[str]] = {}
    for raw_stem in raw_stems:
        values.setdefault(_match_key(raw_stem), []).append(raw_stem)
    return {key: tuple(sorted(stems)) for key, stems in values.items()}


def _repeated_raw_stem_index(raw_stems: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    values: dict[str, list[str]] = {}
    for raw_stem in raw_stems:
        values.setdefault(_repeated_base_key(raw_stem), []).append(raw_stem)
    return {
        key: tuple(sorted(stems, key=_repeated_sort_key))
        for key, stems in values.items()
    }


def _duplicate_doc_counts(entries: tuple[SequenceDocEntry, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        key = _match_key(normalize_doc_display_name(entry.doc_display_name))
        counts[key] = counts.get(key, 0) + 1
    return counts


def _class_method_defaults(
    entries: tuple[SequenceDocEntry, ...],
) -> dict[InstrumentQCClass, tuple[str, ActivationMethod]]:
    values: dict[InstrumentQCClass, set[tuple[str, ActivationMethod]]] = {}
    for entry in entries:
        instrument_qc_class = classify_sequence_entry(entry)
        if instrument_qc_class == InstrumentQCClass.UNKNOWN:
            continue
        if not entry.instrument_method:
            continue
        values.setdefault(instrument_qc_class, set()).add(
            (entry.instrument_method, entry.activation_method)
        )
    return {
        instrument_qc_class: next(iter(methods))
        for instrument_qc_class, methods in values.items()
        if len(methods) == 1
    }


def _match_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _repeated_base_key(value: str) -> str:
    return _match_key(re.sub(r"[_-]\d+$", "", value))


def _repeated_sort_key(value: str) -> tuple[int, str]:
    match = re.search(r"[_-](\d+)$", value)
    if match:
        return int(match.group(1)), value
    return 0, value


def _classify_name(
    display_name: str,
    method: str,
    sample_description: str,
) -> InstrumentQCClass:
    display_method = f"{display_name} {method}".casefold()
    joined = f"{display_name} {method} {sample_description}".casefold()
    if "sdolek" in joined or "sdo/lek" in joined:
        return InstrumentQCClass.SDOLEK
    if (
        "mix_stds" in display_method
        or "mix std" in display_method
        or re.search(r"\bstds\b", display_method)
    ):
        return InstrumentQCClass.MIX_STDS
    if "blank" in display_method or "sb check" in display_method:
        return InstrumentQCClass.BLANK
    if "pooled_qc" in joined or "pooled qc" in joined:
        return InstrumentQCClass.POOLED_QC
    return InstrumentQCClass.UNKNOWN

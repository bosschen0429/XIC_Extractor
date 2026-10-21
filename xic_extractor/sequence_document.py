from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from zipfile import BadZipFile, ZipFile

ActivationMethod = Literal["CID", "wHCD", "HCD", "CIDwHCD", "unknown"]


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
    return re.sub(r"[^a-z0-9]+", "", value.casefold())

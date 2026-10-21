from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from xic_extractor import sequence_document
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
    entries = sequence_document.parse_sequence_docx(method_doc)
    raw_stems = discover_raw_stems(raw_dir)
    return build_sequence_manifest_from_entries(
        entries,
        raw_stems=raw_stems,
    )


def build_sequence_manifest_from_entries(
    entries: tuple[sequence_document.SequenceDocEntry, ...],
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


def classify_sequence_entry(
    entry: sequence_document.SequenceDocEntry,
) -> InstrumentQCClass:
    return _classify_name(
        entry.doc_display_name,
        entry.instrument_method,
        entry.sample_description,
    )


def normalize_doc_display_name(name: str) -> str:
    value = re.sub(r"\s+", " ", name).strip()
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
    entry: sequence_document.SequenceDocEntry,
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


def _duplicate_doc_counts(
    entries: tuple[sequence_document.SequenceDocEntry, ...],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        key = _match_key(normalize_doc_display_name(entry.doc_display_name))
        counts[key] = counts.get(key, 0) + 1
    return counts


def _class_method_defaults(
    entries: tuple[sequence_document.SequenceDocEntry, ...],
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

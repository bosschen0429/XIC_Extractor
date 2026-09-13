"""Source-row indexes and seed context for the reconciliation gallery."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from xic_extractor.diagnostics.diagnostic_io import (
    optional_float,
    rows_by_text_field,
    text_value,
)

from .backfill_reconciliation_gallery_models import (
    _SeedRecord,
)


def _source_hashes_from_input_artifacts(
    input_artifacts: Mapping[str, object],
) -> dict[str, str]:
    return {
        key: text_value(value)
        for key, value in input_artifacts.items()
        if key.endswith("_sha256") and text_value(value)
    }


def _first_by_family(rows: Sequence[Mapping[str, str]]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        family = text_value(row.get("feature_family_id"))
        if family and family not in result:
            result[family] = dict(row)
    return result


def _first_by_family_and_seed_group(
    rows: Sequence[Mapping[str, str]],
) -> dict[tuple[str, str], dict[str, str]]:
    result: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        family = text_value(row.get("feature_family_id"))
        seed_group_id = text_value(row.get("seed_group_id"))
        if not family or not seed_group_id:
            continue
        result.setdefault((family, seed_group_id), dict(row))
    return result


def _group_by_family(
    rows: Sequence[Mapping[str, str]],
) -> dict[str, tuple[dict[str, str], ...]]:
    grouped = rows_by_text_field(rows, "feature_family_id")
    return {
        family: tuple(dict(row) for row in items)
        for family, items in grouped.items()
    }


def _cells_by_family_seed_group(
    *,
    cells_by_family: Mapping[str, Sequence[Mapping[str, str]]],
    seed_records_by_family: Mapping[str, Sequence[_SeedRecord]],
    family_ids: Iterable[str],
) -> dict[tuple[str, str], tuple[Mapping[str, str], ...]]:
    result: dict[tuple[str, str], tuple[Mapping[str, str], ...]] = {}
    for family in family_ids:
        family_cells = tuple(cells_by_family.get(family, ()))
        seed_records = seed_records_by_family.get(
            family,
            (_fallback_seed_record(family),),
        )
        seed_ids_by_sample: dict[str, list[str]] = {}
        grouped_seed_cells: dict[str, list[Mapping[str, str]]] = {}
        for seed_record in seed_records:
            key = (family, seed_record.seed_group_id)
            if not seed_record.samples:
                result[key] = family_cells
                continue
            grouped_seed_cells.setdefault(seed_record.seed_group_id, [])
            for sample in seed_record.samples:
                seed_ids_by_sample.setdefault(sample, []).append(
                    seed_record.seed_group_id,
                )
        for row in family_cells:
            sample = text_value(row.get("sample_stem"))
            for seed_group_id in seed_ids_by_sample.get(sample, ()):
                grouped_seed_cells[seed_group_id].append(row)
        for seed_group_id, rows in grouped_seed_cells.items():
            result[(family, seed_group_id)] = tuple(rows)
    return result


def _overlay_rows_by_family_seed_group(
    rows: Sequence[Mapping[str, str]],
) -> dict[tuple[str, str], tuple[dict[str, str], ...]]:
    grouped: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in rows:
        family = text_value(row.get("feature_family_id"))
        seed_group_id = text_value(row.get("seed_group_id"))
        if not family or not seed_group_id:
            continue
        grouped.setdefault((family, seed_group_id), []).append(dict(row))
    return {key: tuple(items) for key, items in grouped.items()}


def _legacy_overlay_rows_by_family(
    rows: Sequence[Mapping[str, str]],
) -> dict[str, tuple[dict[str, str], ...]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        family = text_value(row.get("feature_family_id"))
        if not family or text_value(row.get("seed_group_id")):
            continue
        grouped.setdefault(family, []).append(dict(row))
    return {family: tuple(items) for family, items in grouped.items()}


def _overlay_rows_for_seed_group(
    rows: Sequence[Mapping[str, str]],
    *,
    seed_group_id: str,
) -> tuple[dict[str, str], ...]:
    return tuple(
        dict(row)
        for row in rows
        if text_value(row.get("seed_group_id")) == seed_group_id
    )


def _legacy_overlay_rows(
    rows: Sequence[Mapping[str, str]],
) -> tuple[dict[str, str], ...]:
    return tuple(
        dict(row) for row in rows if not text_value(row.get("seed_group_id"))
    )


def _candidate_family_ids(
    *,
    reviews: Sequence[Mapping[str, str]],
    cells: Sequence[Mapping[str, str]],
    seeds: Sequence[Mapping[str, str]],
    seed_aware: Sequence[Mapping[str, str]],
    seed_aware_summary: Sequence[Mapping[str, str]],
    candidates: Sequence[Mapping[str, str]],
) -> tuple[tuple[str, ...], dict[str, int]]:
    candidate_families: set[str] = set()
    detected_families = _detected_family_ids(reviews=reviews, cells=cells)
    for row in reviews:
        family = text_value(row.get("feature_family_id"))
        if not family:
            continue
        if (
            _int_text(row.get("quantifiable_rescue_count")) > 0
            or _int_text(row.get("accepted_rescue_count")) > 0
            or "provisional" in text_value(row.get("row_flags")).lower()
            or "backfill" in text_value(row.get("identity_reason")).lower()
        ):
            candidate_families.add(family)
    for row in cells:
        family = text_value(row.get("feature_family_id"))
        if family and (
            text_value(row.get("status")).lower() == "rescued"
            or "backfill" in text_value(row.get("gap_fill_state")).lower()
        ):
            candidate_families.add(family)
    for row in (*seeds, *seed_aware, *seed_aware_summary, *candidates):
        family = text_value(row.get("feature_family_id"))
        if family:
            candidate_families.add(family)
    eligible = candidate_families & detected_families
    excluded = candidate_families - detected_families
    excluded_counts: dict[str, int] = {}
    if excluded:
        excluded_counts["detected_zero_family"] = len(excluded)
    return tuple(sorted(eligible)), excluded_counts


def _detected_family_ids(
    *,
    reviews: Sequence[Mapping[str, str]],
    cells: Sequence[Mapping[str, str]],
) -> set[str]:
    families: set[str] = set()
    for row in reviews:
        family = text_value(row.get("feature_family_id"))
        if family and (
            _int_text(row.get("detected_count")) > 0
            or _int_text(row.get("quantifiable_detected_count")) > 0
        ):
            families.add(family)
    for row in cells:
        family = text_value(row.get("feature_family_id"))
        if family and text_value(row.get("status")).lower() == "detected":
            families.add(family)
    return families


def _seed_records_by_family(
    rows: Sequence[Mapping[str, str]],
) -> dict[str, tuple[_SeedRecord, ...]]:
    by_key: dict[tuple[str, str, str, str, str, str], set[str]] = {}
    for row in rows:
        family = text_value(row.get("feature_family_id"))
        if not family:
            continue
        seed_mz = text_value(row.get("backfill_seed_mz"))
        seed_rt = text_value(row.get("backfill_seed_rt"))
        rt_start = text_value(row.get("backfill_request_rt_min"))
        rt_end = text_value(row.get("backfill_request_rt_max"))
        ppm = text_value(row.get("backfill_request_ppm"))
        sample = text_value(row.get("sample_stem"))
        by_key.setdefault(
            (family, seed_mz, seed_rt, rt_start, rt_end, ppm),
            set(),
        ).add(sample)
    grouped: dict[str, list[_SeedRecord]] = {}
    for (family, seed_mz, seed_rt, rt_start, rt_end, ppm), samples in by_key.items():
        grouped.setdefault(family, []).append(
            _SeedRecord(
                seed_group_id=_seed_group_id(
                    family,
                    seed_mz=seed_mz,
                    seed_rt=seed_rt,
                    rt_start=rt_start,
                    rt_end=rt_end,
                    ppm=ppm,
                ),
                seed_group_basis="seed_audit",
                seed_mz=seed_mz,
                seed_rt=seed_rt,
                rt_start=rt_start,
                rt_end=rt_end,
                ppm=ppm,
                samples=frozenset(sample for sample in samples if sample),
            ),
        )
    return {
        family: tuple(sorted(records, key=lambda record: record.seed_group_id))
        for family, records in grouped.items()
    }


def _seed_samples_by_family(
    rows: Sequence[Mapping[str, str]],
) -> dict[str, frozenset[str]]:
    samples: dict[str, set[str]] = {}
    for row in rows:
        family = text_value(row.get("feature_family_id"))
        sample = text_value(row.get("sample_stem"))
        if family and sample:
            samples.setdefault(family, set()).add(sample)
    return {family: frozenset(items) for family, items in samples.items()}


def _seed_group_id(
    family: str,
    *,
    seed_mz: str,
    seed_rt: str,
    rt_start: str,
    rt_end: str,
    ppm: str,
) -> str:
    return (
        f"seed::{family}::mz={seed_mz or 'unknown'}::"
        f"rt={seed_rt or 'unknown'}::"
        f"window={rt_start or 'unknown'}-{rt_end or 'unknown'}::"
        f"ppm={ppm or 'unknown'}"
    )


def _fallback_seed_record(family: str) -> _SeedRecord:
    return _SeedRecord(
        seed_group_id=f"family_center::{family}::seed=unknown",
        seed_group_basis="family_center_fallback",
    )


def _cells_for_seed_record(
    rows: Sequence[Mapping[str, str]],
    seed_record: _SeedRecord,
) -> tuple[Mapping[str, str], ...]:
    if not seed_record.samples:
        return tuple(rows)
    matched = [
        row for row in rows if text_value(row.get("sample_stem")) in seed_record.samples
    ]
    return tuple(matched)


def _seed_detected_anchor_count(
    rows: Sequence[Mapping[str, str]],
    *,
    seed_record: _SeedRecord,
    seed_records: Sequence[_SeedRecord],
) -> int:
    seed_rt = optional_float(seed_record.seed_rt)
    seed_rts = tuple(
        (record.seed_group_id, optional_float(record.seed_rt))
        for record in seed_records
        if optional_float(record.seed_rt) is not None
    )
    if seed_rt is None or not seed_rts:
        return _count_cells(rows, "detected") if len(seed_records) == 1 else 0
    count = 0
    for row in rows:
        if text_value(row.get("status")).lower() != "detected":
            continue
        apex_rt = optional_float(row.get("apex_rt"))
        if apex_rt is None:
            continue
        nearest_seed_group_id = min(
            seed_rts,
            key=lambda item: (
                abs(apex_rt - (item[1] if item[1] is not None else apex_rt)),
                item[0],
            ),
        )[0]
        if nearest_seed_group_id == seed_record.seed_group_id:
            count += 1
    return count


def _count_cells(rows: Sequence[Mapping[str, str]], status: str) -> int:
    return sum(1 for row in rows if text_value(row.get("status")).lower() == status)


def _count_provisional(rows: Sequence[Mapping[str, str]]) -> int:
    return sum(
        1
        for row in rows
        if "provisional" in text_value(row.get("gap_fill_state")).lower()
        or "provisional" in text_value(row.get("status")).lower()
    )


def _int_text(value: object) -> int:
    parsed = optional_float(value)
    return int(parsed) if parsed is not None else 0

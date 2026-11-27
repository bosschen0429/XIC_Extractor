from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable, Mapping, Sequence
from io import BytesIO
from pathlib import Path

from openpyxl import load_workbook

from xic_extractor.alignment.drift_evidence import (
    KnownIstdReference,
    mint_known_istd_reference_from_measurements,
)
from xic_extractor.configuration.models import Target
from xic_extractor.discovery.input_source import DiscoveryInputSource
from xic_extractor.discovery.models import DiscoveryPeak
from xic_extractor.mass_accuracy import within_ppm

ISTD_APEX_HALF_WINDOW_MIN = 2.0
"""How far a sample's ISTD apex may sit from its target's expected RT."""


def istd_apex_rts(
    targets: Sequence[Target],
    peaks: Iterable[DiscoveryPeak],
    *,
    neutral_loss_by_tag: Mapping[str, float],
) -> dict[tuple[str, str], float]:
    """Each sample's ISTD apex RT, read from its Discovery peaks.

    A candidate sits at the target's m/z, carries an event of the target's
    neutral loss and has its apex within ISTD_APEX_HALF_WINDOW_MIN of the
    expected RT. Status is not a condition: an apex measured without an attached
    event is still the standard's elution. The spiked standard triggers the most
    events; ties go to the apex closest to the expected RT. A sample whose
    standard DDA never sampled has no coordinate for that label.
    """
    chosen: dict[tuple[str, str], tuple[tuple[int, float, str], float]] = {}
    for peak in peaks:
        losses = {neutral_loss_by_tag[tag] for tag in peak.matched_tag_names}
        for target in targets:
            expected = (
                target.expected_rt_min
                if target.expected_rt_min is not None
                else (target.rt_min + target.rt_max) / 2.0
            )
            distance = abs(peak.ms1_apex_rt - expected)
            if (
                target.neutral_loss_da not in losses
                or distance > ISTD_APEX_HALF_WINDOW_MIN
                or not within_ppm(peak.peak_mz, target.mz, target.ppm_tol)
            ):
                continue
            key = target.label, peak.sample_stem
            rank = (-peak.attached_event_count, distance, peak.peak_id)
            if key not in chosen or rank < chosen[key][0]:
                chosen[key] = rank, peak.ms1_apex_rt
    return {key: rt for key, (_, rt) in chosen.items()}


def known_istd_reference_from_discovery(
    targets: Sequence[Target],
    source: DiscoveryInputSource,
    *,
    neutral_loss_by_tag: Mapping[str, float],
    source_identifier: str,
    source_sha256: str,
) -> KnownIstdReference:
    """The Alignment RT reference from the ISTD apexes Discovery measured."""
    candidate_ids = tuple(
        candidate_id
        for candidate_id, location in source.locations.items()
        if any(within_ppm(location.peak_mz, t.mz, t.ppm_tol) for t in targets)
    )
    peaks = (
        peak
        for sample_peaks in source.load_candidates(candidate_ids).values()
        for peak in sample_peaks
    )
    return mint_known_istd_reference_from_measurements(
        targets,
        istd_apex_rts(targets, peaks, neutral_loss_by_tag=neutral_loss_by_tag),
        source_identifier=source_identifier,
        source_sha256=source_sha256,
    )


def read_known_istd_reference(path: Path) -> KnownIstdReference:
    """Read the current report schema and validate declared targets at their owner."""
    payload = path.read_bytes()
    workbook = load_workbook(BytesIO(payload), read_only=True, data_only=True)

    def rows(sheet: str, required: tuple[str, ...]) -> Iterable[dict[str, object]]:
        if sheet not in workbook.sheetnames:
            raise ValueError(f"known ISTD reference requires sheet {sheet}")
        values = iter(workbook[sheet].values)
        headers = tuple(
            str(value).strip() if value is not None else ""
            for value in next(values, ())
        )
        if any(headers.count(key) != 1 for key in required):
            raise ValueError(
                f"{sheet}: missing or duplicate required headers {required}"
            )
        for row in values:
            if any(value is not None for value in row):
                yield dict(zip(headers, row, strict=False))

    def number(row: dict[str, object], key: str) -> float:
        value = row[key]
        parsed = (
            float(value)
            if isinstance(value, int | float) and not isinstance(value, bool)
            else None
        )
        if parsed is not None and not math.isfinite(parsed):
            parsed = None
        if isinstance(value, bool) or parsed is None:
            raise ValueError(f"known ISTD reference: invalid required {key}: {value!r}")
        return parsed

    def text(row: dict[str, object], key: str) -> str:
        value = row[key]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"known ISTD reference: invalid required {key}")
        return value.strip()

    try:
        targets = []
        declared: set[str] = set()
        for row in rows(
            "Targets",
            (
                "Label",
                "Role",
                "m/z",
                "RT min",
                "RT max",
                "ppm tol",
                "NL (Da)",
                "NL ppm warn",
                "NL ppm max",
            ),
        ):
            declared.add(text(row, "Label"))
            if str(row["Role"]).strip() != "ISTD":
                continue
            targets.append(
                Target(
                    label=text(row, "Label"),
                    mz=number(row, "m/z"),
                    rt_min=number(row, "RT min"),
                    rt_max=number(row, "RT max"),
                    ppm_tol=number(row, "ppm tol"),
                    neutral_loss_da=number(row, "NL (Da)"),
                    nl_ppm_warn=number(row, "NL ppm warn"),
                    nl_ppm_max=number(row, "NL ppm max"),
                    is_istd=True,
                    istd_pair="",
                )
            )
        if not targets or len({t.label for t in targets}) != len(targets):
            raise ValueError("known ISTD reference requires unique ISTD target labels")
        for target in targets:
            if not (
                target.mz > 0
                and target.ppm_tol > 0
                and 0 <= target.rt_min < target.rt_max
            ):
                raise ValueError(
                    f"known ISTD reference: invalid target window {target.label}"
                )
        labels = {target.label for target in targets}
        coordinates: dict[tuple[str, str], float] = {}
        seen: set[tuple[str, str]] = set()
        sample: str | None = None
        for row in rows("XIC Results", ("SampleName", "Target", "RT")):
            if row["SampleName"] is not None and str(row["SampleName"]).strip():
                sample = text(row, "SampleName")
            label = text(row, "Target")
            if label not in declared:
                raise ValueError(f"XIC Results: undeclared target {label}")
            if label not in labels:
                continue
            if sample is None:
                raise ValueError(
                    "XIC Results: ISTD reference has no preceding SampleName"
                )
            key = label, sample
            if key in seen:
                raise ValueError(f"XIC Results: duplicate ISTD reference {key}")
            seen.add(key)
            if row["RT"] is None or str(row["RT"]).strip() == "ND":
                continue
            rt = number(row, "RT")
            if rt < 0:
                raise ValueError("XIC Results: reference RT must be nonnegative")
            coordinates[key] = rt
        return mint_known_istd_reference_from_measurements(
            targets,
            coordinates,
            source_identifier=str(path.resolve()),
            source_sha256=hashlib.sha256(payload).hexdigest(),
        )
    finally:
        workbook.close()

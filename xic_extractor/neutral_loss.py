from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Literal, Protocol

import numpy as np

from xic_extractor.ms2_scan_models import Ms2Scan, Ms2ScanEvent
from xic_extractor.ms2_trace_evidence import (
    MS2TraceEvidence,
    MS2TracePoint,
    empty_ms2_trace_evidence,
    summarize_ms2_trace,
)

NL_DIAGNOSTIC_PPM_FLOOR = 500.0
CandidateMS2AlignmentSource = Literal[
    "region", "apex_fallback", "boundary_rescue", "none"
]
NLStatus = Literal["OK", "WARN", "NL_FAIL", "NO_MS2"]
_CANDIDATE_MS2_APEX_FALLBACK_MIN = 0.08
_CANDIDATE_MS2_BOUNDARY_RESCUE_MIN = 0.08
_CANDIDATE_MS2_BOUNDARY_RESCUE_MAX = 0.20
_CANDIDATE_MS2_BOUNDARY_RESCUE_WIDTH_FRACTION = 0.5


class MS2ScanSource(Protocol):
    def iter_ms2_scans(self, rt_min: float, rt_max: float) -> Iterator[Ms2ScanEvent]:
        """Yield MS2 scan events for the requested retention-time window."""
        ...


@dataclass(frozen=True)
class NLResult:
    status: NLStatus
    best_ppm: float | None
    best_scan_rt: float | None
    valid_ms2_scan_count: int
    parse_error_count: int
    matched_scan_count: int

    def to_token(self) -> str:
        if self.status == "WARN" and self.best_ppm is not None:
            return f"WARN_{self.best_ppm:.1f}ppm"
        return self.status


@dataclass(frozen=True)
class MS2ProductEvidence:
    scan_rt: float
    precursor_mz: float
    product_mz: float
    product_intensity: float
    product_base_ratio: float
    target_product_ppm: float
    observed_loss_da: float
    observed_loss_error_ppm: float
    scan_number: int | None = None
    acquisition_source_id: str = ""


@dataclass(frozen=True)
class MS2ProductProbe:
    evidence: MS2ProductEvidence | None
    absence_reason: str
    nearest_product_mz: float | None
    nearest_product_loss_ppm: float | None
    nearest_product_base_ratio: float | None


@dataclass(frozen=True)
class CandidateMS2Evidence:
    ms2_present: bool
    nl_match: bool
    nl_status: NLStatus
    trigger_scan_count: int
    strict_nl_scan_count: int
    best_loss_ppm: float | None
    best_scan_rt: float | None
    best_product_base_ratio: float | None
    alignment_source: CandidateMS2AlignmentSource
    diagnostic_product_absence_reason: str = ""
    nearest_product_loss_ppm: float | None = None
    nearest_product_base_ratio: float | None = None
    nearest_product_mz: float | None = None
    trace: MS2TraceEvidence = field(default_factory=empty_ms2_trace_evidence)
    ms1_peak_group_source: str = ""
    ms1_peak_group_rt_min: float | None = None
    ms1_peak_group_rt_max: float | None = None
    ms1_peak_group_trigger_scan_count: int | None = None
    ms1_peak_group_strict_nl_scan_count: int | None = None
    ms1_peak_group_strict_nl_event_count: int | None = None
    outside_ms1_peak_group_trigger_scan_count: int | None = None
    outside_ms1_peak_group_strict_nl_scan_count: int | None = None
    best_product_evidence: MS2ProductEvidence | None = None

    def to_token(self) -> str:
        if self.nl_status == "WARN" and self.best_loss_ppm is not None:
            return f"WARN_{self.best_loss_ppm:.1f}ppm"
        return self.nl_status


def find_nl_anchor_rt(
    raw: MS2ScanSource,
    *,
    precursor_mz: float,
    rt_center: float,
    search_margin_min: float,
    neutral_loss_da: float,
    nl_ppm_max: float,
    ms2_precursor_tol_da: float,
    nl_min_intensity_ratio: float,
    reference_rt: float | None = None,
) -> float | None:
    """在 rt_center ± search_margin_min 範圍內搜尋 NL 確認的 MS2 scan，回傳最合適的 RT。

    選擇邏輯依 reference_rt 而定：
    - reference_rt 為 None：選 base_peak 最高的 scan。
      適用於 ISTD（無同 m/z 異構體干擾）及無 ISTD pair 的 analyte（無外部 RT
      基準時，訊號強度最可靠）。
    - reference_rt 有值：選距 reference_rt 最近的 scan。
      適用於有 ISTD pair 的 analyte，傳入 ISTD 的 anchor_rt 以區分同 RT
      窗口內的異構體（如 5-medC vs N4-medC）。
    """
    rt_min = max(0.0, rt_center - search_margin_min)
    rt_max = rt_center + search_margin_min
    diagnostic_ppm = max(3.0 * nl_ppm_max, NL_DIAGNOSTIC_PPM_FLOOR)

    best_rt: float | None = None
    best_base_peak: float = -1.0
    best_distance: float = float("inf")

    for event in raw.iter_ms2_scans(rt_min, rt_max):
        if event.parse_error is not None or event.scan is None:
            continue
        if abs(event.scan.precursor_mz - precursor_mz) > ms2_precursor_tol_da:
            continue
        evidence = _best_product_evidence(
            event.scan,
            target_precursor_mz=precursor_mz,
            neutral_loss_da=neutral_loss_da,
            nl_ppm_max=nl_ppm_max,
            min_intensity_ratio=nl_min_intensity_ratio,
            diagnostic_ppm=diagnostic_ppm,
        )
        if evidence is None or evidence.observed_loss_error_ppm > nl_ppm_max:
            continue
        if reference_rt is None:
            if event.scan.base_peak > best_base_peak:
                best_base_peak = event.scan.base_peak
                best_rt = event.scan.rt
        else:
            distance = abs(event.scan.rt - reference_rt)
            distance_tied = np.isclose(distance, best_distance, rtol=0.0, atol=1e-12)
            if distance < best_distance or (
                distance_tied and event.scan.base_peak > best_base_peak
            ):
                best_distance = distance
                best_base_peak = event.scan.base_peak
                best_rt = event.scan.rt

    return best_rt


def check_nl(
    raw: MS2ScanSource,
    *,
    precursor_mz: float,
    rt_min: float,
    rt_max: float,
    neutral_loss_da: float,
    nl_ppm_warn: float,
    nl_ppm_max: float,
    ms2_precursor_tol_da: float,
    nl_min_intensity_ratio: float,
) -> NLResult:
    diagnostic_ppm = max(3.0 * nl_ppm_max, NL_DIAGNOSTIC_PPM_FLOOR)

    valid_count = 0
    parse_error_count = 0
    matched_count = 0
    best_ppm: float | None = None
    best_scan_rt: float | None = None

    for event in raw.iter_ms2_scans(rt_min, rt_max):
        if event.parse_error is not None:
            parse_error_count += 1
            continue
        if event.scan is None:
            continue

        valid_count += 1
        if abs(event.scan.precursor_mz - precursor_mz) > ms2_precursor_tol_da:
            continue

        matched_count += 1
        evidence = _best_product_evidence(
            event.scan,
            target_precursor_mz=precursor_mz,
            neutral_loss_da=neutral_loss_da,
            nl_ppm_max=nl_ppm_max,
            min_intensity_ratio=nl_min_intensity_ratio,
            diagnostic_ppm=diagnostic_ppm,
        )
        if evidence is not None and (
            best_ppm is None or evidence.observed_loss_error_ppm < best_ppm
        ):
            best_ppm = evidence.observed_loss_error_ppm
            best_scan_rt = event.scan.rt

    status = _classify_nl_result(
        matched_scan_count=matched_count,
        best_ppm=best_ppm,
        nl_ppm_warn=nl_ppm_warn,
        nl_ppm_max=nl_ppm_max,
    )
    return NLResult(
        status=status,
        best_ppm=best_ppm,
        best_scan_rt=best_scan_rt,
        valid_ms2_scan_count=valid_count,
        parse_error_count=parse_error_count,
        matched_scan_count=matched_count,
    )


def collect_candidate_ms2_evidence(
    raw: MS2ScanSource,
    *,
    candidate: object,
    precursor_mz: float,
    neutral_loss_da: float,
    nl_ppm_warn: float,
    nl_ppm_max: float,
    ms2_precursor_tol_da: float,
    nl_min_intensity_ratio: float,
    ms1_peak_group_rt_min: float | None = None,
    ms1_peak_group_rt_max: float | None = None,
    ms1_peak_group_source: str = "",
) -> CandidateMS2Evidence:
    peak = getattr(candidate, "peak")
    evidence_peak_start = getattr(candidate, "ms2_evidence_peak_start", None)
    evidence_peak_end = getattr(candidate, "ms2_evidence_peak_end", None)
    peak_start = float(
        evidence_peak_start
        if evidence_peak_start is not None
        else getattr(peak, "peak_start")
    )
    peak_end = float(
        evidence_peak_end
        if evidence_peak_end is not None
        else getattr(peak, "peak_end")
    )
    apex_rt = float(getattr(candidate, "selection_apex_rt"))
    scoped_peak_group = _valid_ms1_peak_group_scope(
        ms1_peak_group_rt_min,
        ms1_peak_group_rt_max,
    )
    primary_start = (
        scoped_peak_group[0] if scoped_peak_group is not None else peak_start
    )
    primary_end = scoped_peak_group[1] if scoped_peak_group is not None else peak_end
    boundary_rescue_margin = _candidate_ms2_boundary_rescue_margin(
        primary_start, primary_end
    )
    rt_min = max(0.0, min(primary_start, apex_rt) - boundary_rescue_margin)
    rt_max = max(primary_end, apex_rt) + boundary_rescue_margin
    diagnostic_ppm = max(3.0 * nl_ppm_max, NL_DIAGNOSTIC_PPM_FLOOR)

    trigger_scan_count = 0
    strict_nl_scan_count = 0
    best_evidence: MS2ProductEvidence | None = None
    best_evidence_source: CandidateMS2AlignmentSource | None = None
    best_probe: MS2ProductProbe | None = None
    product_trace_points: list[MS2TracePoint] = []
    region_trigger_seen = False
    fallback_trigger_seen = False
    outside_peak_group_trigger_scan_count = 0
    outside_peak_group_strict_nl_scan_count = 0

    for event in raw.iter_ms2_scans(rt_min, rt_max):
        if event.parse_error is not None or event.scan is None:
            continue
        scan = event.scan
        if abs(scan.precursor_mz - precursor_mz) > ms2_precursor_tol_da:
            continue

        inside_candidate_region = peak_start <= scan.rt <= peak_end
        inside_peak_group = (
            scoped_peak_group is not None
            and scoped_peak_group[0] <= scan.rt <= scoped_peak_group[1]
        )
        inside_region = (
            inside_peak_group
            if scoped_peak_group is not None
            else inside_candidate_region
        )
        near_apex = abs(scan.rt - apex_rt) <= _CANDIDATE_MS2_APEX_FALLBACK_MIN
        inside_boundary_rescue = (
            primary_start - boundary_rescue_margin
            <= scan.rt
            <= primary_end + boundary_rescue_margin
        )
        near_apex_aligned = near_apex and scoped_peak_group is None
        if not inside_region and not near_apex_aligned and not inside_boundary_rescue:
            continue

        primary_aligned = inside_region or near_apex_aligned
        source: CandidateMS2AlignmentSource
        if inside_region:
            source = "region"
        elif near_apex_aligned:
            source = "apex_fallback"
        else:
            source = "boundary_rescue"

        probe = _best_product_probe(
            scan,
            target_precursor_mz=precursor_mz,
            neutral_loss_da=neutral_loss_da,
            nl_ppm_max=nl_ppm_max,
            min_intensity_ratio=nl_min_intensity_ratio,
            diagnostic_ppm=diagnostic_ppm,
        )
        if _is_better_product_probe(probe, best_probe):
            best_probe = probe
        evidence = probe.evidence

        if scoped_peak_group is not None and not inside_peak_group:
            outside_peak_group_trigger_scan_count += 1
            if evidence is not None and evidence.observed_loss_error_ppm <= nl_ppm_max:
                outside_peak_group_strict_nl_scan_count += 1
            continue

        if primary_aligned:
            trigger_scan_count += 1
            region_trigger_seen = region_trigger_seen or inside_region
            fallback_trigger_seen = fallback_trigger_seen or (
                not inside_region and near_apex_aligned
            )

        if evidence is None:
            continue
        strict_nl_match = evidence.observed_loss_error_ppm <= nl_ppm_max
        if not primary_aligned and not strict_nl_match:
            continue
        if not primary_aligned:
            trigger_scan_count += 1
        if strict_nl_match:
            strict_nl_scan_count += 1
            product_trace_points.append(
                MS2TracePoint(
                    rt=evidence.scan_rt,
                    intensity=evidence.product_intensity,
                    base_ratio=evidence.product_base_ratio,
                    observed_loss_error_ppm=evidence.observed_loss_error_ppm,
                )
            )
        if (
            best_evidence is None
            or evidence.observed_loss_error_ppm < best_evidence.observed_loss_error_ppm
        ):
            best_evidence = evidence
            best_evidence_source = source

    best_loss_ppm = (
        best_evidence.observed_loss_error_ppm if best_evidence is not None else None
    )
    absence_reason = ""
    if best_evidence is None and best_probe is not None:
        absence_reason = best_probe.absence_reason
        if (
            not absence_reason
            and outside_peak_group_strict_nl_scan_count > 0
            and scoped_peak_group is not None
        ):
            absence_reason = "strict_nl_outside_ms1_peak_group"
    nl_status = _classify_nl_result(
        matched_scan_count=trigger_scan_count,
        best_ppm=best_loss_ppm,
        nl_ppm_warn=nl_ppm_warn,
        nl_ppm_max=nl_ppm_max,
    )
    alignment_source: CandidateMS2AlignmentSource
    if nl_status in {"OK", "WARN"} and best_evidence_source is not None:
        alignment_source = best_evidence_source
    elif region_trigger_seen:
        alignment_source = "region"
    elif fallback_trigger_seen:
        alignment_source = "apex_fallback"
    else:
        alignment_source = "none"
    ms1_peak_group_strict_nl_event_count = (
        None if scoped_peak_group is None else 1 if strict_nl_scan_count > 0 else 0
    )

    return CandidateMS2Evidence(
        ms2_present=trigger_scan_count > 0,
        nl_match=nl_status in {"OK", "WARN"},
        nl_status=nl_status,
        trigger_scan_count=trigger_scan_count,
        strict_nl_scan_count=strict_nl_scan_count,
        best_loss_ppm=best_loss_ppm,
        best_scan_rt=best_evidence.scan_rt if best_evidence is not None else None,
        best_product_base_ratio=(
            best_evidence.product_base_ratio if best_evidence is not None else None
        ),
        alignment_source=alignment_source,
        diagnostic_product_absence_reason=absence_reason,
        nearest_product_loss_ppm=(
            best_evidence.observed_loss_error_ppm
            if best_evidence is not None
            else best_probe.nearest_product_loss_ppm
            if best_probe is not None
            else None
        ),
        nearest_product_base_ratio=(
            best_evidence.product_base_ratio
            if best_evidence is not None
            else best_probe.nearest_product_base_ratio
            if best_probe is not None
            else None
        ),
        nearest_product_mz=(
            best_evidence.product_mz
            if best_evidence is not None
            else best_probe.nearest_product_mz
            if best_probe is not None
            else None
        ),
        trace=summarize_ms2_trace(
            product_trace_points,
            candidate_apex_rt=apex_rt,
            trigger_scan_count=trigger_scan_count,
        ),
        ms1_peak_group_source=(
            str(ms1_peak_group_source or "ms1_peak_group")
            if scoped_peak_group is not None
            else ""
        ),
        ms1_peak_group_rt_min=(
            scoped_peak_group[0] if scoped_peak_group is not None else None
        ),
        ms1_peak_group_rt_max=(
            scoped_peak_group[1] if scoped_peak_group is not None else None
        ),
        ms1_peak_group_trigger_scan_count=(
            trigger_scan_count if scoped_peak_group is not None else None
        ),
        ms1_peak_group_strict_nl_scan_count=(
            strict_nl_scan_count if scoped_peak_group is not None else None
        ),
        ms1_peak_group_strict_nl_event_count=ms1_peak_group_strict_nl_event_count,
        outside_ms1_peak_group_trigger_scan_count=(
            outside_peak_group_trigger_scan_count
            if scoped_peak_group is not None
            else None
        ),
        outside_ms1_peak_group_strict_nl_scan_count=(
            outside_peak_group_strict_nl_scan_count
            if scoped_peak_group is not None
            else None
        ),
        best_product_evidence=best_evidence,
    )


def _valid_ms1_peak_group_scope(
    rt_min: float | None,
    rt_max: float | None,
) -> tuple[float, float] | None:
    if rt_min is None or rt_max is None:
        return None
    start = float(rt_min)
    end = float(rt_max)
    if not (np.isfinite(start) and np.isfinite(end)):
        return None
    if start >= end:
        return None
    return start, end


def _candidate_ms2_boundary_rescue_margin(peak_start: float, peak_end: float) -> float:
    peak_width = max(0.0, peak_end - peak_start)
    return min(
        _CANDIDATE_MS2_BOUNDARY_RESCUE_MAX,
        max(
            _CANDIDATE_MS2_BOUNDARY_RESCUE_MIN,
            peak_width * _CANDIDATE_MS2_BOUNDARY_RESCUE_WIDTH_FRACTION,
        ),
    )


def _best_product_evidence(
    scan: Ms2Scan,
    *,
    target_precursor_mz: float,
    neutral_loss_da: float,
    nl_ppm_max: float,
    min_intensity_ratio: float,
    diagnostic_ppm: float,
) -> MS2ProductEvidence | None:
    return _best_product_probe(
        scan,
        target_precursor_mz=target_precursor_mz,
        neutral_loss_da=neutral_loss_da,
        nl_ppm_max=nl_ppm_max,
        min_intensity_ratio=min_intensity_ratio,
        diagnostic_ppm=diagnostic_ppm,
    ).evidence


def _best_product_probe(
    scan: Ms2Scan,
    *,
    target_precursor_mz: float,
    neutral_loss_da: float,
    nl_ppm_max: float,
    min_intensity_ratio: float,
    diagnostic_ppm: float,
) -> MS2ProductProbe:
    expected_product = scan.precursor_mz - neutral_loss_da
    if expected_product <= 0.0 or target_precursor_mz <= 0.0 or scan.base_peak <= 0.0:
        return MS2ProductProbe(
            evidence=None,
            absence_reason="invalid_product_context",
            nearest_product_mz=None,
            nearest_product_loss_ppm=None,
            nearest_product_base_ratio=None,
        )

    masses = np.asarray(scan.masses, dtype=float)
    intensities = np.asarray(scan.intensities, dtype=float)
    usable_count = min(len(masses), len(intensities))
    if usable_count == 0:
        return MS2ProductProbe(
            evidence=None,
            absence_reason="no_product_peak",
            nearest_product_mz=None,
            nearest_product_loss_ppm=None,
            nearest_product_base_ratio=None,
        )
    masses = masses[:usable_count]
    intensities = intensities[:usable_count]
    intensity_floor = scan.base_peak * min_intensity_ratio

    scan_product_ppm = (
        np.abs(masses - expected_product) / expected_product * 1_000_000.0
    )
    nearest_index = int(np.argmin(scan_product_ppm))
    nearest_product_mz = float(masses[nearest_index])
    nearest_observed_loss_da = scan.precursor_mz - nearest_product_mz
    nearest_product_loss_ppm = (
        abs(nearest_observed_loss_da - neutral_loss_da) / neutral_loss_da * 1_000_000.0
    )
    nearest_product_base_ratio = float(intensities[nearest_index] / scan.base_peak)
    mask = (intensities >= intensity_floor) & (scan_product_ppm <= diagnostic_ppm)
    if not mask.any():
        if float(scan_product_ppm[nearest_index]) > diagnostic_ppm:
            absence_reason = "product_outside_diagnostic_window"
        elif intensities[nearest_index] < intensity_floor:
            absence_reason = "product_below_intensity_floor"
        else:
            absence_reason = "no_product_peak"
        return MS2ProductProbe(
            evidence=None,
            absence_reason=absence_reason,
            nearest_product_mz=nearest_product_mz,
            nearest_product_loss_ppm=nearest_product_loss_ppm,
            nearest_product_base_ratio=nearest_product_base_ratio,
        )

    target_product = target_precursor_mz - neutral_loss_da
    if target_product <= 0.0:
        return MS2ProductProbe(
            evidence=None,
            absence_reason="invalid_product_context",
            nearest_product_mz=nearest_product_mz,
            nearest_product_loss_ppm=nearest_product_loss_ppm,
            nearest_product_base_ratio=nearest_product_base_ratio,
        )

    candidates: list[MS2ProductEvidence] = []
    for index in np.flatnonzero(mask):
        product_mz = float(masses[int(index)])
        observed_loss_da = scan.precursor_mz - product_mz
        observed_loss_error_ppm = (
            abs(observed_loss_da - neutral_loss_da) / neutral_loss_da * 1_000_000.0
        )
        if observed_loss_error_ppm > max(diagnostic_ppm, 3.0 * nl_ppm_max):
            continue
        candidates.append(
            MS2ProductEvidence(
                scan_rt=scan.rt,
                precursor_mz=scan.precursor_mz,
                product_mz=product_mz,
                product_intensity=float(intensities[int(index)]),
                product_base_ratio=float(intensities[int(index)] / scan.base_peak),
                target_product_ppm=float(
                    abs(product_mz - target_product) / target_product * 1_000_000.0
                ),
                observed_loss_da=observed_loss_da,
                observed_loss_error_ppm=observed_loss_error_ppm,
                scan_number=scan.scan_number,
            )
        )

    if not candidates:
        return MS2ProductProbe(
            evidence=None,
            absence_reason="loss_outside_diagnostic_window",
            nearest_product_mz=nearest_product_mz,
            nearest_product_loss_ppm=nearest_product_loss_ppm,
            nearest_product_base_ratio=nearest_product_base_ratio,
        )
    evidence = min(candidates, key=lambda item: item.observed_loss_error_ppm)
    return MS2ProductProbe(
        evidence=evidence,
        absence_reason="",
        nearest_product_mz=evidence.product_mz,
        nearest_product_loss_ppm=evidence.observed_loss_error_ppm,
        nearest_product_base_ratio=evidence.product_base_ratio,
    )


def _is_better_product_probe(
    candidate: MS2ProductProbe,
    current: MS2ProductProbe | None,
) -> bool:
    if current is None:
        return True
    if candidate.evidence is not None and current.evidence is None:
        return True
    if candidate.evidence is None and current.evidence is not None:
        return False
    candidate_ppm = (
        candidate.evidence.observed_loss_error_ppm
        if candidate.evidence is not None
        else candidate.nearest_product_loss_ppm
    )
    current_ppm = (
        current.evidence.observed_loss_error_ppm
        if current.evidence is not None
        else current.nearest_product_loss_ppm
    )
    if candidate_ppm is None:
        return False
    if current_ppm is None:
        return True
    if not np.isclose(candidate_ppm, current_ppm, rtol=0.0, atol=1e-12):
        return candidate_ppm < current_ppm
    candidate_ratio = candidate.nearest_product_base_ratio or 0.0
    current_ratio = current.nearest_product_base_ratio or 0.0
    return candidate_ratio > current_ratio


def _classify_nl_result(
    *,
    matched_scan_count: int,
    best_ppm: float | None,
    nl_ppm_warn: float,
    nl_ppm_max: float,
) -> NLStatus:
    if matched_scan_count == 0:
        return "NO_MS2"
    if best_ppm is None or best_ppm > nl_ppm_max:
        return "NL_FAIL"
    if best_ppm > nl_ppm_warn:
        return "WARN"
    return "OK"

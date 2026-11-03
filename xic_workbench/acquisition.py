"""Acquire bounded diagnostic XICs for the local HTML review workbench."""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from xic_extractor.alignment_explanation.reader import (
    read_alignment_explanation,
    read_group_candidate_ids,
)
from xic_extractor.alignment_explanation.storage import (
    ExplanationError,
)
from xic_extractor.configuration.mass_recalibration import (
    MassRecalibration,
    MassRecalibrationResult,
    deserialize_mass_recalibration_result,
)
from xic_extractor.configuration.signal_processing_profile import (
    resolve_signal_processing_profile,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    prepare_physical_signal,
)
from xic_extractor.processing_identity import (
    AcquisitionSourceExpectation,
    AcquisitionSourceIdentity,
    FingerprintCancelledError,
    IdentityContractError,
    fingerprint_file_content,
)
from xic_extractor.raw_reader import (
    PreparedRawMassRecalibration,
    open_raw,
    stat_identity,
)
from xic_inspector.sources.curve_bundle import (
    ChromatogramCurve,
    CurveBundle,
    CurveBundleTrace,
    TransitionCurves,
    write_curve_bundle,
)

_SOURCE_BINDING_BASIS = "report_sample_stem+report_acquisition_source_identity"


class CurveAcquisitionCancelled(Exception):
    """The caller cancelled diagnostic curve acquisition."""


def export_explanation_curves(
    report: Path,
    output: Path,
    *,
    raw_dir: Path,
    dll_dir: Path,
    candidate_id: str | None = None,
    group_id: str | None = None,
    padding_min: float = 0.5,
    ppm: float = 5.0,
    sample_ids: tuple[str, ...] = (),
    is_cancelled: Callable[[], bool] | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
    verified_raws: dict[Path, AcquisitionSourceExpectation] | None = None,
) -> dict[str, Any]:
    """Acquire diagnostic curves at saved apexes or requested group positions.

    ``verified_raws`` lets a caller reuse content verification across requests;
    an entry applies only while the RAW path keeps the same file state.
    """
    if (candidate_id is None) == (group_id is None):
        raise ExplanationError(
            "Choose exactly one candidate ID or group ID for curve export"
        )
    if not math.isfinite(padding_min + ppm) or padding_min <= 0 or ppm <= 0:
        raise ExplanationError("Curve padding and ppm must be positive and finite")
    if output.resolve() == report.resolve() or output.exists():
        raise FileExistsError(f"Curve output must be a new file: {output}")

    summary = read_alignment_explanation(report)
    mass_results = _review_mass_results(report, summary)
    sample_labels = {
        row["sample_key"]: row["sample_stem"] for row in summary["samples"]
    }
    requested_samples = set(sample_ids)
    unknown_samples = requested_samples - sample_labels.keys()
    if unknown_samples:
        raise ExplanationError(
            "Requested canonical sample IDs are not present in the report: "
            f"{sorted(unknown_samples)}"
        )

    candidate_ids = _selected_candidate_ids(report, candidate_id, group_id)
    selected: dict[tuple[str, str], dict[str, Any]] = {}
    for cid in candidate_ids:
        detail = read_alignment_explanation(report, candidate_id=cid)
        for candidate in detail["candidates"]:
            sid = candidate["sample_key"]
            terminals = [
                terminal
                for terminal in detail["records"].get("terminal_subjects", [])
                if terminal["sample_key"] == sid and terminal["candidate_id"] == cid
            ]
            allowed = None
            if group_id is not None:
                allowed = {
                    oid
                    for group in detail["records"].get("final_groups", [])
                    if group["feature_id"] == group_id
                    for member in group["member_subjects"]
                    if member["sample_key"] == sid
                    for oid in member["physical_peak_keys"]
                }
            for terminal in terminals:
                for observation in terminal.get("observations", []):
                    oid = observation["physical_peak_key"]
                    if allowed is not None and oid not in allowed:
                        continue
                    selected[sid, oid] = {
                        "candidate_id": cid,
                        "mz": candidate["precursor_mz"],
                        "rt": observation["rt_apex_min"],
                    }

    observed_samples = {sid for sid, _ in selected}
    unobserved_samples = requested_samples - observed_samples
    if unobserved_samples and (group_id is None or not selected):
        raise ExplanationError(
            "Requested samples have no retained apex coordinates for this selection: "
            f"{sorted(unobserved_samples)}. Diagnostic curves require an observed apex."
        )
    if unobserved_samples:
        # A requested group member without its own apex is read at the group's
        # observed position, so a missed peak can still be seen; the trace says so.
        position = {
            "candidate_id": None,
            "mz": statistics.median(o["mz"] for o in selected.values()),
            "rt": statistics.median(o["rt"] for o in selected.values()),
            "group_position": True,
        }
        for sid in unobserved_samples:
            selected[sid, "group-position"] = position
    if requested_samples:
        selected = {
            key: observation
            for key, observation in selected.items()
            if key[0] in requested_samples
        }
    if not selected:
        raise ExplanationError(
            "Selection has no retained apex coordinates for bounded curve acquisition"
        )

    by_sample: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for (sid, oid), observation in selected.items():
        if sid not in sample_labels:
            raise ExplanationError(
                f"Selection references an unknown report sample: {sid}"
            )
        by_sample[sid].append((oid, observation))

    sources = _report_acquisition_sources(summary)
    traces: list[CurveBundleTrace] = []
    # A window without signal leaves that sample out; the other samples remain
    # readable and the receipt names the omission.
    unusable_samples: set[str] = set()
    source_ids_by_sample: dict[str, str] = {}
    profile = resolve_signal_processing_profile()
    total = len(by_sample)
    for completed, (sid, observations) in enumerate(sorted(by_sample.items())):
        _check_cancel(is_cancelled)
        stem = sample_labels[sid]
        if Path(stem).name != stem or stem in (".", ".."):
            raise ExplanationError("Invalid sample stem in report")
        raw_path = raw_dir / f"{stem}.raw"
        if not raw_path.is_file():
            raise ExplanationError(f"RAW 資料夾裡找不到 {stem}.raw")
        known = verified_raws if verified_raws is not None else {}
        expectation = _resolve_report_source(
            raw_path, sources, known.get(raw_path), is_cancelled=is_cancelled
        )
        known[raw_path] = expectation
        source_ids_by_sample[sid] = expectation.source.acquisition_source_id
        _check_cancel(is_cancelled)
        # The run's own recalibration result is replayed instead of re-probing
        # every MS1 scan of the RAW for each review request.
        saved = mass_results.get(stem)
        with open_raw(
            raw_path,
            dll_dir,
            expected_acquisition_source=expectation.source,
            _expected_path_identity=expectation.path_identity,
            mass_recalibration=MassRecalibration.NONE
            if saved is None
            else saved.policy,
            prepared_mass_recalibration=None
            if saved is None or saved.policy is MassRecalibration.NONE
            else PreparedRawMassRecalibration(expectation, saved),
        ) as raw:
            acquired_before = len(traces)
            for oid, observation in observations:
                _check_cancel(is_cancelled)
                lower = max(0.0, observation["rt"] - padding_min)
                upper = observation["rt"] + padding_min
                rt, intensity = raw.extract_xic(observation["mz"], lower, upper, ppm)
                prepared = prepare_physical_signal(
                    rt, intensity, profile=profile
                ).prepared
                if prepared is None:
                    continue
                traces.append(
                    CurveBundleTrace(
                        trace_id=f"review:{sid}:{oid}",
                        sample_id=sid,
                        sample_label=stem,
                        role="diagnostic_reacquisition",
                        raw_identity=expectation.source.acquisition_source_id,
                        mz=observation["mz"],
                        ppm=ppm,
                        extraction_rt_window=(lower, upper),
                        producer="explanation_selected_xic",
                        producer_version="1",
                        raw=ChromatogramCurve(
                            tuple(map(float, rt)), tuple(map(float, intensity))
                        ),
                        gaussian15=ChromatogramCurve(prepared.rt, prepared.smoothed),
                        baseline=ChromatogramCurve(prepared.rt, prepared.baseline),
                        positive_signal=ChromatogramCurve(
                            prepared.rt, prepared.positive_signal
                        ),
                        quality_flags={
                            "candidate_id": observation["candidate_id"],
                            "acquired_at_group_position": observation.get(
                                "group_position", False
                            ),
                            "new_diagnostic_extraction": True,
                            "source_binding_basis": _SOURCE_BINDING_BASIS,
                            "processing_attempt_id": summary["processing_attempt_id"],
                        },
                    )
                )
        if len(traces) == acquired_before:
            unusable_samples.add(stem)
        if on_progress is not None:
            on_progress(completed + 1, total, sid)

    if not traces:
        raise ExplanationError("所選樣本在擷取範圍內都沒有可用訊號。")
    window = (
        min(trace.extraction_rt_window[0] for trace in traces),
        max(trace.extraction_rt_window[1] for trace in traces),
    )
    bundle = CurveBundle(
        (
            TransitionCurves(
                key=group_id or str(candidate_id),
                review_rt_window=window,
                quality_flags={
                    "new_diagnostic_extraction": True,
                    "processing_attempt_id": summary["processing_attempt_id"],
                },
                traces=tuple(traces),
            ),
        )
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    _check_cancel(is_cancelled)
    write_curve_bundle(bundle, output)
    return {
        "output": str(output.resolve()),
        "sample_count": len(by_sample),
        "curve_count": len(traces),
        "unusable_samples": sorted(unusable_samples),
        "bytes": output.stat().st_size,
        "acquisition_sources": dict(sorted(source_ids_by_sample.items())),
        "source_binding_basis": _SOURCE_BINDING_BASIS,
    }


def _selected_candidate_ids(
    report: Path, candidate_id: str | None, group_id: str | None
) -> list[str]:
    if group_id is None:
        assert candidate_id is not None
        return [candidate_id]
    candidate_ids = read_group_candidate_ids(report, group_id)
    if not candidate_ids:
        raise ExplanationError(
            f"Group has no retained candidate observations: {group_id}"
        )
    return candidate_ids


def _report_acquisition_sources(
    summary: dict[str, Any],
) -> tuple[AcquisitionSourceIdentity, ...]:
    rows = summary.get("acquisition_sources")
    if not isinstance(rows, list) or not rows:
        raise ExplanationError(
            "Report does not retain acquisition source identities; "
            "selected RAW files cannot be verified"
        )
    if any(not isinstance(row, dict) for row in rows):
        raise ExplanationError("Report acquisition source identities are malformed")
    try:
        sources = tuple(
            AcquisitionSourceIdentity(
                **{
                    **row,
                    "local_file_revision": (
                        tuple(row["local_file_revision"])
                        if row.get("local_file_revision") is not None
                        else None
                    ),
                }
            )
            for row in rows
        )
    except (TypeError, IdentityContractError) as exc:
        raise ExplanationError(
            "Report acquisition source identities are malformed"
        ) from exc
    if len({source.acquisition_source_id for source in sources}) != len(sources):
        raise ExplanationError(
            "Report contains duplicate acquisition source identities"
        )
    return sources


def _resolve_report_source(
    raw_path: Path,
    sources: tuple[AcquisitionSourceIdentity, ...],
    known: AcquisitionSourceExpectation | None,
    *,
    is_cancelled: Callable[[], bool] | None,
) -> AcquisitionSourceExpectation:
    try:
        before = raw_path.stat()
    except OSError as exc:
        raise ExplanationError(f"無法讀取 {raw_path.name} 來核對內容。") from exc
    if (
        known is not None
        and known.source in sources
        and stat_identity(before) == known.path_identity
    ):
        return known
    local_matches = tuple(
        source
        for source in sources
        if source.local_file_revision == stat_identity(before)
    )
    if len(local_matches) == 1:
        _check_cancel(is_cancelled)
        return AcquisitionSourceExpectation(local_matches[0], stat_identity(before))
    # Older result reports can retain explicit content identities. A local-only
    # report never falls back to reading RAW bytes when its revision differs.
    content_sources = tuple(
        source for source in sources if source.content_sha256 is not None
    )
    if not content_sources:
        raise ExplanationError(
            f"{raw_path.name} 與這份結果分析時使用的 RAW 內容不同，無法對應樣本。"
        )
    try:
        fingerprint = fingerprint_file_content(
            raw_path,
            should_cancel=is_cancelled,
        )
        after = raw_path.stat()
    except FingerprintCancelledError as exc:
        raise CurveAcquisitionCancelled("Curve acquisition cancelled") from exc
    except (IdentityContractError, OSError) as exc:
        raise ExplanationError(f"無法讀取 {raw_path.name} 來核對內容。") from exc
    if stat_identity(before) != stat_identity(after):
        raise ExplanationError(f"{raw_path.name} 在核對內容時被修改，請稍後再試。")
    matches = tuple(
        source
        for source in content_sources
        if source.content_sha256 == fingerprint.content_sha256
        and source.byte_size == fingerprint.byte_size
    )
    if len(matches) != 1:
        raise ExplanationError(
            f"{raw_path.name} 與這份結果分析時使用的 RAW 內容不同，無法對應樣本。"
        )
    return AcquisitionSourceExpectation(matches[0], stat_identity(after))


def _check_cancel(is_cancelled: Callable[[], bool] | None) -> None:
    if is_cancelled is not None and is_cancelled():
        raise CurveAcquisitionCancelled("Curve acquisition cancelled")


__all__ = [
    "CurveAcquisitionCancelled",
    "export_explanation_curves",
]


def _review_mass_results(
    report: Path, summary: dict[str, Any]
) -> dict[str, MassRecalibrationResult]:
    """Replay the run's co-published recalibration, never re-probe or re-default.

    Legacy results without policy metadata retain vendor coordinates. A policy
    sidecar belongs to this review only when both run identities agree.
    """
    path = report.parent / "alignment_run_metadata.json"
    if not path.is_file():
        return {}
    metadata = json.loads(path.read_text(encoding="utf-8"))
    payload = metadata.get("mass_recalibration_by_sample")
    if payload is None:
        return {}
    if any(metadata.get(key) != summary[key] for key in ("processing_attempt_id",)):
        raise ExplanationError(
            "Alignment metadata 不屬於這份檢閱結果，請使用同次執行的檔案。"
        )
    return {
        sample: deserialize_mass_recalibration_result(json.dumps(result))
        for sample, result in json.loads(payload).items()
    }

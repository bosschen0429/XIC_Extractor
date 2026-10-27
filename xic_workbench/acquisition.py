"""Acquire bounded diagnostic XICs for the local HTML review workbench."""

from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from xic_extractor.configuration.mass_recalibration import (
    MassRecalibration,
    resolve_mass_recalibration,
)
from xic_extractor.configuration.signal_processing_profile import (
    resolve_signal_processing_profile,
)
from xic_extractor.diagnostics.alignment_explanation import (
    ExplanationError,
    read_cid_nl_explanation,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    prepare_physical_signal,
)
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
    FingerprintCancelledError,
    IdentityContractError,
    fingerprint_file_content,
)
from xic_extractor.raw_reader import open_raw
from xic_inspector.sources.curve_bundle import (
    ChromatogramCurve,
    CurveBundle,
    CurveBundleTrace,
    TransitionCurves,
    write_curve_bundle,
)

_SOURCE_BINDING_BASIS = "report_sample_stem+report_acquisition_source_content"


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
) -> dict[str, Any]:
    """Acquire new review curves around retained apexes without deciding area."""
    if (candidate_id is None) == (group_id is None):
        raise ExplanationError(
            "Choose exactly one candidate ID or group ID for curve export"
        )
    if not math.isfinite(padding_min + ppm) or padding_min <= 0 or ppm <= 0:
        raise ExplanationError("Curve padding and ppm must be positive and finite")
    if output.resolve() == report.resolve() or output.exists():
        raise FileExistsError(f"Curve output must be a new file: {output}")

    summary = read_cid_nl_explanation(report)
    mass_policies = _review_mass_policies(report, summary)
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
        detail = read_cid_nl_explanation(report, candidate_id=cid)
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
    if unobserved_samples:
        raise ExplanationError(
            "Requested samples have no retained apex coordinates for this selection: "
            f"{sorted(unobserved_samples)}. Diagnostic curves require an observed apex."
        )
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
            raise ExplanationError(
                f"RAW is required for this selected curve: {raw_path}"
            )
        expected_source = _resolve_report_source(
            raw_path, sources, is_cancelled=is_cancelled
        )
        source_ids_by_sample[sid] = expected_source.acquisition_source_id
        _check_cancel(is_cancelled)
        with open_raw(
            raw_path,
            dll_dir,
            expected_acquisition_source=expected_source,
            mass_recalibration=mass_policies.get(stem, MassRecalibration.NONE),
        ) as raw:
            for oid, observation in observations:
                _check_cancel(is_cancelled)
                lower = max(0.0, observation["rt"] - padding_min)
                upper = observation["rt"] + padding_min
                rt, intensity = raw.extract_xic(observation["mz"], lower, upper, ppm)
                prepared = prepare_physical_signal(
                    rt, intensity, profile=profile
                ).prepared
                if prepared is None:
                    raise ExplanationError(
                        f"Selected window has no usable curve: {stem}, "
                        f"{observation['candidate_id']}"
                    )
                traces.append(
                    CurveBundleTrace(
                        trace_id=f"review:{sid}:{oid}",
                        sample_id=sid,
                        sample_label=stem,
                        role="diagnostic_reacquisition",
                        raw_identity=expected_source.acquisition_source_id,
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
                            "new_diagnostic_extraction": True,
                            "source_binding_basis": _SOURCE_BINDING_BASIS,
                            "processing_request_id": summary["processing_request_id"],
                            "processing_attempt_id": summary["processing_attempt_id"],
                        },
                    )
                )
        if on_progress is not None:
            on_progress(completed + 1, total, sid)

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
                    "processing_request_id": summary["processing_request_id"],
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
    with sqlite3.connect(report.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only = ON")
        candidate_ids = [
            row[0]
            for row in db.execute(
                "SELECT DISTINCT candidate_id FROM links "
                "WHERE kind='final_groups' AND record_id=? ORDER BY candidate_id",
                (group_id,),
            )
        ]
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
    try:
        sources = tuple(AcquisitionSourceIdentity(**row) for row in rows)
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
    *,
    is_cancelled: Callable[[], bool] | None,
) -> AcquisitionSourceIdentity:
    try:
        fingerprint = fingerprint_file_content(
            raw_path,
            should_cancel=is_cancelled,
        )
    except FingerprintCancelledError as exc:
        raise CurveAcquisitionCancelled("Curve acquisition cancelled") from exc
    except IdentityContractError as exc:
        raise ExplanationError(
            f"Unable to verify selected RAW identity: {raw_path}"
        ) from exc
    matches = tuple(
        source
        for source in sources
        if source.content_sha256 == fingerprint.content_sha256
        and source.byte_size == fingerprint.byte_size
    )
    if len(matches) != 1:
        raise ExplanationError(
            "Selected RAW does not match exactly one acquisition source retained "
            f"by the report: {raw_path}"
        )
    return matches[0]


def _check_cancel(is_cancelled: Callable[[], bool] | None) -> None:
    if is_cancelled is not None and is_cancelled():
        raise CurveAcquisitionCancelled("Curve acquisition cancelled")


__all__ = [
    "CurveAcquisitionCancelled",
    "export_explanation_curves",
]


def _review_mass_policies(
    report: Path, summary: dict[str, Any]
) -> dict[str, MassRecalibration]:
    """Replay co-published policy, never infer it from the current defaults.

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
    if any(
        metadata.get(key) != summary[key]
        for key in ("processing_request_id", "processing_attempt_id")
    ):
        raise ExplanationError(
            "Alignment metadata 不屬於這份檢閱結果，請使用同次執行的檔案。"
        )
    return {
        sample: resolve_mass_recalibration(result["policy"])
        for sample, result in json.loads(payload).items()
    }

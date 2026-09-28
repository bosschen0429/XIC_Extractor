"""Versioned, offline curve handoff and its Inspector source adapter.

The producer owns acquisition and Gaussian15 preparation. This module only
validates, stores, and projects already computed curves for review.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from xic_extractor.diagnostics.cid_nl_explanation import ExplanationError
from xic_inspector.selection.canonical import (
    CanonicalItem,
    Witness,
    build_canonical_ref,
)

BUNDLE_SCHEMA_VERSION = "xic_curve_bundle"
MAX_BUNDLE_SIZE_BYTES = 50 * 1024 * 1024


class CurveBundleError(ExplanationError):
    """The portable handoff is malformed, unsupported, or incomplete."""


@dataclass(frozen=True, slots=True)
class ChromatogramCurve:
    rt: tuple[float, ...]
    intensity: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class PeakBounds:
    start: float
    apex: float
    end: float


@dataclass(frozen=True, slots=True)
class CurveIntegration:
    """Saved integration metadata; the Inspector never integrates the series."""

    area_raw_counts_seconds: float
    raw_apex_rt_min: float
    integration_method: str


@dataclass(frozen=True, slots=True)
class SampleQuantification:
    """One formal value per sample, independent of its displayed curve count."""

    sample_id: str
    value: float | None
    unit: str


@dataclass(frozen=True, slots=True)
class CurveBundleTrace:
    trace_id: str
    sample_id: str
    sample_label: str
    role: str
    raw_identity: str
    mz: float
    ppm: float
    extraction_rt_window: tuple[float, float]
    producer: str
    producer_version: str
    raw: ChromatogramCurve | None
    gaussian15: ChromatogramCurve | None
    baseline: ChromatogramCurve | None = None
    peak_bounds: PeakBounds | None = None
    unavailable_reason: str | None = None
    quality_flags: dict[str, Any] = field(default_factory=dict)
    positive_signal: ChromatogramCurve | None = None
    integration: CurveIntegration | None = None


@dataclass(frozen=True, slots=True)
class TransitionCurves:
    key: str
    review_rt_window: tuple[float, float]
    quality_flags: dict[str, Any]
    traces: tuple[CurveBundleTrace, ...]
    quantifications: tuple[SampleQuantification, ...] = ()


@dataclass(frozen=True, slots=True)
class CurveBundle:
    transitions: tuple[TransitionCurves, ...]
    schema_version: str = BUNDLE_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class CurveBundleLocator:
    bundle_path: Path
    bundle_sha256: str
    schema_version: str


def _fail(where: str, message: str) -> CurveBundleError:
    return CurveBundleError(f"Invalid curve bundle {where}: {message}")


def _mapping(value: object, where: str, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _fail(where, "expected object")
    unknown = set(value) - fields
    if unknown:
        raise _fail(where, f"unknown fields: {sorted(unknown)}")
    return value


def _text(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail(where, "required nonempty text is missing")
    return value


def _number(value: object, where: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise _fail(where, "expected finite number")
    return float(value)


def _window(value: object, where: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise _fail(where, "expected [start, end]")
    start, end = (_number(part, where) for part in value)
    if start < 0 or start >= end:
        raise _fail(where, "start must be nonnegative and before end")
    return start, end


def _curve(value: object, where: str) -> ChromatogramCurve | None:
    if value is None:
        return None
    obj = _mapping(value, where, {"rt", "intensity"})
    rts = obj.get("rt")
    intensities = obj.get("intensity")
    if not isinstance(rts, (list, tuple)) or not isinstance(intensities, (list, tuple)):
        raise _fail(where, "rt and intensity must be arrays")
    if not rts or len(rts) != len(intensities):
        raise _fail(where, "rt and intensity must have the same nonzero length")
    rt = tuple(_number(value, f"{where}.rt") for value in rts)
    intensity = tuple(_number(value, f"{where}.intensity") for value in intensities)
    if any(a >= b for a, b in zip(rt, rt[1:])):
        raise _fail(where, "rt must increase strictly")
    return ChromatogramCurve(rt, intensity)


def _bounds(value: object, where: str) -> PeakBounds | None:
    if value is None:
        return None
    obj = _mapping(value, where, {"start", "apex", "end"})
    start = _number(obj.get("start"), f"{where}.start")
    apex = _number(obj.get("apex"), f"{where}.apex")
    end = _number(obj.get("end"), f"{where}.end")
    if start > apex or apex > end:
        raise _fail(where, "expected start <= apex <= end")
    return PeakBounds(start, apex, end)


def _integration(value: object, where: str) -> CurveIntegration | None:
    if value is None:
        return None
    obj = _mapping(
        value,
        where,
        {"area_raw_counts_seconds", "raw_apex_rt_min", "integration_method"},
    )
    return CurveIntegration(
        _number(obj.get("area_raw_counts_seconds"), f"{where}.area_raw_counts_seconds"),
        _number(obj.get("raw_apex_rt_min"), f"{where}.raw_apex_rt_min"),
        _text(obj.get("integration_method"), f"{where}.integration_method"),
    )


def _quantifications(
    value: object,
    where: str,
    sample_ids: set[str],
) -> tuple[SampleQuantification, ...]:
    if not isinstance(value, (list, tuple)):
        raise _fail(where, "expected array")
    result = []
    seen = set()
    for index, entry in enumerate(value):
        loc = f"{where}[{index}]"
        obj = _mapping(entry, loc, {"sample_id", "value", "unit"})
        sid = _text(obj.get("sample_id"), f"{loc}.sample_id")
        if sid not in sample_ids or sid in seen:
            raise _fail(loc, "quantification requires a unique represented sample")
        if "value" not in obj:
            raise _fail(loc, "value is required; use null for a saved empty result")
        seen.add(sid)
        number = obj["value"]
        result.append(
            SampleQuantification(
                sid,
                None if number is None else _number(number, f"{loc}.value"),
                _text(obj.get("unit"), f"{loc}.unit"),
            )
        )
    return tuple(result)


def _flags(value: object, where: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not key for key in value
    ):
        raise _fail(where, "quality_flags must be an object with named fields")
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise _fail(where, "quality_flags must contain finite JSON values") from exc
    return value


def _validate_differential_flags(flags: dict[str, Any], where: str) -> None:
    if flags.get("review_kind") != "cid_nl_differential":
        return
    for side in ("source", "successor"):
        loc = f"{where}.{side}"
        start, end = _window(flags.get(f"{side}_rt_window"), f"{loc}_rt_window")
        center = _number(flags.get(f"{side}_center_rt"), f"{loc}_center_rt")
        if not start <= center <= end:
            raise _fail(f"{loc}_center_rt", "must be inside its RT window")


def _decode_bundle(value: object) -> CurveBundle:
    root = _mapping(value, "$", {"schema_version", "transitions"})
    if root.get("schema_version") != BUNDLE_SCHEMA_VERSION:
        raise _fail("$.schema_version", f"expected {BUNDLE_SCHEMA_VERSION}")
    entries = root.get("transitions")
    if not isinstance(entries, (list, tuple)) or not entries:
        raise _fail("$.transitions", "expected nonempty array")
    transitions = []
    keys: set[str] = set()
    for index, entry in enumerate(entries):
        where = f"$.transitions[{index}]"
        obj = _mapping(
            entry,
            where,
            {"key", "review_rt_window", "quality_flags", "traces", "quantifications"},
        )
        key = _text(obj.get("key"), f"{where}.key")
        if key in keys:
            raise _fail(where, f"duplicate transition key {key!r}")
        keys.add(key)
        window = _window(obj.get("review_rt_window"), f"{where}.review_rt_window")
        flags = _flags(obj.get("quality_flags"), f"{where}.quality_flags")
        _validate_differential_flags(flags, f"{where}.quality_flags")
        traces_raw = obj.get("traces")
        if not isinstance(traces_raw, (list, tuple)) or not traces_raw:
            raise _fail(f"{where}.traces", "expected nonempty array")
        traces = []
        trace_ids: set[str] = set()
        for trace_index, trace_raw in enumerate(traces_raw):
            loc = f"{where}.traces[{trace_index}]"
            trace_obj = _mapping(
                trace_raw,
                loc,
                {
                    "trace_id",
                    "sample_id",
                    "sample_label",
                    "role",
                    "raw_identity",
                    "mz",
                    "ppm",
                    "extraction_rt_window",
                    "producer",
                    "producer_version",
                    "raw",
                    "gaussian15",
                    "baseline",
                    "positive_signal",
                    "integration",
                    "peak_bounds",
                    "unavailable_reason",
                    "quality_flags",
                },
            )
            trace_id = _text(trace_obj.get("trace_id"), f"{loc}.trace_id")
            if trace_id in trace_ids:
                raise _fail(loc, f"duplicate trace_id {trace_id!r}")
            trace_ids.add(trace_id)
            raw = _curve(trace_obj.get("raw"), f"{loc}.raw")
            gaussian15 = _curve(trace_obj.get("gaussian15"), f"{loc}.gaussian15")
            baseline = _curve(trace_obj.get("baseline"), f"{loc}.baseline")
            positive = _curve(
                trace_obj.get("positive_signal"), f"{loc}.positive_signal"
            )
            unavailable_reason = trace_obj.get("unavailable_reason")
            if (raw is None) != (gaussian15 is None):
                raise _fail(
                    loc, "raw and precomputed gaussian15 must both be present or absent"
                )
            if raw is None:
                _text(unavailable_reason, f"{loc}.unavailable_reason")
                if baseline is not None or positive is not None:
                    raise _fail(loc, "unavailable trace cannot carry processed series")
            elif unavailable_reason is not None:
                raise _fail(loc, "available trace cannot have unavailable_reason")
            if raw is not None:
                assert gaussian15 is not None
                if any(
                    curve is not None and curve.rt != raw.rt
                    for curve in (gaussian15, baseline, positive)
                ):
                    raise _fail(loc, "all series must share exact rt values")
            ppm = _number(trace_obj.get("ppm"), f"{loc}.ppm")
            if ppm <= 0:
                raise _fail(loc, "ppm must be positive")
            traces.append(
                CurveBundleTrace(
                    trace_id=trace_id,
                    sample_id=_text(trace_obj.get("sample_id"), f"{loc}.sample_id"),
                    sample_label=_text(
                        trace_obj.get("sample_label"), f"{loc}.sample_label"
                    ),
                    role=_text(trace_obj.get("role"), f"{loc}.role"),
                    raw_identity=_text(
                        trace_obj.get("raw_identity"), f"{loc}.raw_identity"
                    ),
                    mz=_number(trace_obj.get("mz"), f"{loc}.mz"),
                    ppm=ppm,
                    extraction_rt_window=_window(
                        trace_obj.get("extraction_rt_window"),
                        f"{loc}.extraction_rt_window",
                    ),
                    producer=_text(trace_obj.get("producer"), f"{loc}.producer"),
                    producer_version=_text(
                        trace_obj.get("producer_version"), f"{loc}.producer_version"
                    ),
                    raw=raw,
                    gaussian15=gaussian15,
                    baseline=baseline,
                    positive_signal=positive,
                    integration=_integration(
                        trace_obj.get("integration"), f"{loc}.integration"
                    ),
                    peak_bounds=_bounds(
                        trace_obj.get("peak_bounds"), f"{loc}.peak_bounds"
                    ),
                    unavailable_reason=unavailable_reason,
                    quality_flags=_flags(
                        trace_obj.get("quality_flags", {}), f"{loc}.quality_flags"
                    ),
                )
            )
        quantifications = _quantifications(
            obj.get("quantifications", ()),
            f"{where}.quantifications",
            {trace.sample_id for trace in traces},
        )
        transitions.append(
            TransitionCurves(key, window, flags, tuple(traces), quantifications)
        )
    return CurveBundle(tuple(transitions))


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise _fail("$", f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _load_bundle(path: str | Path) -> tuple[CurveBundle, str]:
    source = Path(path)
    if source.stat().st_size > MAX_BUNDLE_SIZE_BYTES:
        raise _fail("$", "source exceeds 50 MiB limit")
    data = source.read_bytes()
    if len(data) > MAX_BUNDLE_SIZE_BYTES:
        raise _fail("$", "source exceeds 50 MiB limit")
    try:
        body = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise _fail("$", "expected UTF-8 JSON") from exc
    return _decode_bundle(body), hashlib.sha256(data).hexdigest()


def read_curve_bundle(path: str | Path) -> CurveBundle:
    return _load_bundle(path)[0]


def write_curve_bundle(bundle: CurveBundle, path: str | Path) -> int:
    """Write a validated handoff atomically; return its UTF-8 byte count."""
    if not isinstance(bundle, CurveBundle):
        raise _fail("$", "expected CurveBundle")
    _decode_bundle(asdict(bundle))
    data = json.dumps(
        asdict(bundle), ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")
    if len(data) > MAX_BUNDLE_SIZE_BYTES:
        raise _fail("$", "source exceeds 50 MiB limit")
    output = Path(path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=output.parent, prefix=f"{output.name}.tmp.", delete=False
        ) as stream:
            temp_path = Path(stream.name)
            stream.write(data)
        os.replace(temp_path, output)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
    return len(data)


class CurveBundleSource:
    """Project portable transition curves into the shared Inspector selection model."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.bundle, source_sha256 = _load_bundle(path)
        self.locator = CurveBundleLocator(
            bundle_path=path,
            bundle_sha256=source_sha256,
            schema_version=self.bundle.schema_version,
        )
        labels: dict[str, str] = {}
        for transition in self.bundle.transitions:
            for trace in transition.traces:
                if (
                    trace.sample_id in labels
                    and labels[trace.sample_id] != trace.sample_label
                ):
                    raise _fail(
                        "$.transitions",
                        f"conflicting label for sample {trace.sample_id!r}",
                    )
                labels[trace.sample_id] = trace.sample_label
        self.summary = {
            "schema_version": self.bundle.schema_version,
            "plot_evidence_capability": "retained_curves",
            "processing_request_id": "curve-bundle",
            "processing_attempt_id": "v1",
            "samples": [
                {"canonical_sample_identity_id": sid, "sample_stem": label}
                for sid, label in sorted(labels.items())
            ],
        }

    def load_items(self, sample_ids: set[str]) -> list[CanonicalItem]:
        items = []
        for transition in self.bundle.transitions:
            scoped = tuple(t for t in transition.traces if t.sample_id in sample_ids)
            _, ref = build_canonical_ref("group", "curve-bundle", "v1", transition.key)
            witnesses = tuple(
                Witness(
                    canonical_sample_identity_id=trace.sample_id,
                    candidate_id=trace.trace_id,
                    group_hypothesis_id=transition.key,
                    precursor_mz=trace.mz,
                    rt_apex_min=trace.peak_bounds.apex if trace.peak_bounds else None,
                    sample_stem=trace.sample_label,
                )
                for trace in scoped
            )
            items.append(
                CanonicalItem(
                    kind="group",
                    request_id="curve-bundle",
                    attempt_id="v1",
                    identifier=transition.key,
                    serialized_ref=ref,
                    sample_ids=tuple(sorted({t.sample_id for t in scoped})),
                    witnesses=witnesses,
                    stratum="rest",
                    details={
                        "transition": transition,
                        "review_summary": {
                            "observed_sample_count": len(
                                {t.sample_id for t in scoped if t.raw is not None}
                            ),
                            "scope_sample_count": len({t.sample_id for t in scoped}),
                            "origin_kinds": [],
                        },
                    },
                )
            )
        return items

    def load_render_items(
        self, items: list[CanonicalItem], *, capability: str, check_cancel: Any
    ) -> list[dict[str, Any]]:
        rendered = []
        for item in items:
            check_cancel()
            transition: TransitionCurves = item.details["transition"]
            traces = [
                trace
                for trace in transition.traces
                if trace.sample_id in item.sample_ids
            ]
            quantifications = {q.sample_id: q for q in transition.quantifications}
            projected_curves = []
            for trace in traces:
                if trace.raw is None:
                    continue
                assert trace.gaussian15 is not None
                processed = [
                    {
                        "kind": "smoothed",
                        "rts": trace.gaussian15.rt,
                        "intensities": trace.gaussian15.intensity,
                    }
                ]
                if trace.baseline is not None:
                    processed.append(
                        {
                            "kind": "baseline",
                            "rts": trace.baseline.rt,
                            "intensities": trace.baseline.intensity,
                        }
                    )
                if trace.positive_signal is not None:
                    processed.append(
                        {
                            "kind": "positive_signal",
                            "rts": trace.positive_signal.rt,
                            "intensities": trace.positive_signal.intensity,
                        }
                    )
                projected_curves.append(
                    {
                        "sample_id": trace.sample_id,
                        "candidate_id": trace.trace_id,
                        "evidence_ref": trace.trace_id,
                        "kind": trace.role,
                        "stage_label": trace.role,
                        "raw_rts": trace.raw.rt,
                        "raw_intensities": trace.raw.intensity,
                        "processed_curves": processed,
                        "quality_flags": trace.quality_flags,
                        "area": trace.integration.area_raw_counts_seconds
                        if trace.integration
                        else None,
                        "integration": asdict(trace.integration)
                        if trace.integration
                        else None,
                        "bound_left_rt": trace.peak_bounds.start
                        if trace.peak_bounds
                        else None,
                        "apex_rt": trace.peak_bounds.apex
                        if trace.peak_bounds
                        else None,
                        "bound_right_rt": trace.peak_bounds.end
                        if trace.peak_bounds
                        else None,
                        "provenance": {
                            "raw_identity": trace.raw_identity,
                            "mz": trace.mz,
                            "ppm": trace.ppm,
                            "rt_window": trace.extraction_rt_window,
                            "producer": trace.producer,
                            "producer_version": trace.producer_version,
                        },
                    }
                )
            flag_ref = f"flags:{transition.key}"
            fields = [
                {
                    "label": key,
                    "value": value
                    if isinstance(value, str)
                    else json.dumps(value, ensure_ascii=False),
                }
                for key, value in sorted(transition.quality_flags.items())
            ]
            records = [
                {
                    "ref": flag_ref,
                    "title": "Transition quality flags",
                    "summary": transition.key,
                    "fields": fields,
                }
            ]
            for trace in traces:
                records.append(
                    {
                        "ref": trace.trace_id,
                        "title": f"{trace.role} curve provenance",
                        "summary": trace.unavailable_reason or trace.sample_label,
                        "fields": [
                            {"label": "RAW identity", "value": trace.raw_identity},
                            {"label": "m/z", "value": trace.mz},
                            {"label": "ppm", "value": trace.ppm},
                            {
                                "label": "RT window",
                                "value": str(trace.extraction_rt_window),
                            },
                            {"label": "producer", "value": trace.producer},
                            {
                                "label": "producer version",
                                "value": trace.producer_version,
                            },
                            *(
                                [
                                    {
                                        "label": "Integration method",
                                        "value": trace.integration.integration_method,
                                    },
                                    {
                                        "label": "Raw area (counts·s)",
                                        "value": (
                                            trace.integration.area_raw_counts_seconds
                                        ),
                                    },
                                    {
                                        "label": "Raw apex (min)",
                                        "value": trace.integration.raw_apex_rt_min,
                                    },
                                ]
                                if trace.integration
                                else []
                            ),
                            *[
                                {
                                    "label": key,
                                    "value": value
                                    if isinstance(value, str)
                                    else json.dumps(value, ensure_ascii=False),
                                }
                                for key, value in sorted(trace.quality_flags.items())
                            ],
                        ],
                    }
                )
            for sid, quantification in quantifications.items():
                if sid in item.sample_ids:
                    records.append(
                        {
                            "ref": f"quantity:{transition.key}:{sid}",
                            "title": "Saved formal result",
                            "summary": next(
                                t.sample_label for t in traces if t.sample_id == sid
                            ),
                            "fields": [
                                {"label": "Value", "value": quantification.value},
                                {"label": "Unit", "value": quantification.unit},
                            ],
                        }
                    )
            samples = [
                {
                    "sample_id": sid,
                    "sample_stem": next(
                        t.sample_label for t in traces if t.sample_id == sid
                    ),
                    "status_kind": "rest",
                    "status_label": "曲線可用"
                    if any(t.sample_id == sid and t.raw is not None for t in traces)
                    else "RAW 未提供",
                    "support_origin": {"kind": "unavailable"},
                    "decision_refs": [flag_ref]
                    + (
                        [f"quantity:{transition.key}:{sid}"]
                        if sid in quantifications
                        else []
                    ),
                    **(
                        {
                            "quantitative_value": quantifications[sid].value,
                            "quantity_unit": quantifications[sid].unit,
                        }
                        if sid in quantifications
                        else {}
                    ),
                    "evidence_status": "available"
                    if any(t.sample_id == sid and t.raw is not None for t in traces)
                    else "unavailable",
                }
                for sid in item.sample_ids
            ]
            rendered.append(
                {
                    "id": item.serialized_ref,
                    "kind": item.kind,
                    "stratum": item.stratum,
                    "title": transition.key,
                    "badge_label": "診斷曲線",
                    "method_label": "curve bundle",
                    "reason_label": "; ".join(
                        f"{key}: {value}"
                        for key, value in sorted(transition.quality_flags.items())
                    ),
                    "precursor_mz": item.witnesses[0].precursor_mz,
                    "rt_apex_min": item.witnesses[0].rt_apex_min,
                    "review_rt_window": transition.review_rt_window,
                    "transition_review": (
                        transition.quality_flags
                        if transition.quality_flags.get("review_kind")
                        == "cid_nl_differential"
                        else None
                    ),
                    "relationship_availability": "unavailable",
                    "samples": samples,
                    "witnesses": [asdict(w) for w in item.witnesses],
                    "details": {"readable_records": records},
                    "review_summary": item.details["review_summary"],
                    "curves": projected_curves,
                }
            )
        return rendered

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

InstrumentQCDecisionVerdict = Literal[
    "qc_review_ready",
    "metadata_incomplete",
    "sensitivity_review",
    "rt_drift_review",
    "blank_contamination_review",
    "identity_evidence_limited",
    "insufficient_evidence",
]


@dataclass(frozen=True)
class InstrumentQCDecision:
    verdict: InstrumentQCDecisionVerdict
    instrument_qc_dir: Path
    sdolek_row_count: int
    mixstds_row_count: int
    blank_row_count: int
    method_doc_status: str
    top_concerns: tuple[str, ...]
    ms2_readiness: str
    ms2_readiness_reason: str
    source_artifacts: tuple[Path, ...]


def build_instrument_qc_decision(instrument_qc_dir: Path) -> InstrumentQCDecision:
    sdolek_json = instrument_qc_dir / "instrument_qc_sdolek_trend.json"
    mixstds_json = instrument_qc_dir / "instrument_qc_mixstds_trend.json"
    blank_json = instrument_qc_dir / "instrument_qc_blank_tic.json"
    manifest_json = instrument_qc_dir / "instrument_qc_sequence_manifest.json"
    source_artifacts = tuple(
        path for path in (sdolek_json, mixstds_json, blank_json, manifest_json)
        if path.exists()
    )
    sdolek_payload = _read_json(sdolek_json)
    mixstds_payload = _read_json(mixstds_json)
    blank_payload = _read_json(blank_json)
    sdolek_rows = _rows(sdolek_payload)
    mixstds_rows = _rows(mixstds_payload)
    blank_rows = _rows(blank_payload)
    concerns: list[str] = []

    if not sdolek_payload:
        concerns.append("SDOLEK trend JSON is missing or unreadable.")
        verdict: InstrumentQCDecisionVerdict = "insufficient_evidence"
    elif not manifest_json.exists():
        concerns.append("method-doc sequence manifest is missing.")
        verdict = "metadata_incomplete"
    elif _has_blank_concern(blank_payload):
        concerns.append("Blank TIC/BPC evidence reports a contamination concern.")
        verdict = "blank_contamination_review"
    elif any(row.get("status") != "detected" for row in sdolek_rows):
        concerns.append("One or more SDOLEK rows are not detected.")
        verdict = "sensitivity_review"
    elif _has_flag(sdolek_rows, "RT_OUTLIER"):
        concerns.append("SDOLEK trend contains RT_OUTLIER rows.")
        verdict = "rt_drift_review"
    elif not sdolek_rows:
        concerns.append("SDOLEK trend has no rows.")
        verdict = "insufficient_evidence"
    else:
        concerns.append("No blocking instrument QC concerns were detected.")
        verdict = "qc_review_ready"

    ms2_readiness, ms2_reason = _ms2_readiness(sdolek_rows, source_artifacts)
    return InstrumentQCDecision(
        verdict=verdict,
        instrument_qc_dir=instrument_qc_dir,
        sdolek_row_count=len(sdolek_rows),
        mixstds_row_count=len(mixstds_rows),
        blank_row_count=len(blank_rows),
        method_doc_status="present" if manifest_json.exists() else "missing",
        top_concerns=tuple(concerns[:5]),
        ms2_readiness=ms2_readiness,
        ms2_readiness_reason=ms2_reason,
        source_artifacts=source_artifacts,
    )


def render_instrument_qc_decision_markdown(
    decision: InstrumentQCDecision,
) -> str:
    lines = [
        "# Instrument QC Decision Report",
        "",
        f"- Verdict: `{decision.verdict}`",
        f"- Method-doc status: `{decision.method_doc_status}`",
        f"- SDOLEK rows: {decision.sdolek_row_count}",
        f"- Mix STDs rows: {decision.mixstds_row_count}",
        f"- Blank rows: {decision.blank_row_count}",
        f"- MS2 readiness: `{decision.ms2_readiness}`",
        "",
        "## Top Concerns",
        "",
    ]
    lines.extend(f"- {concern}" for concern in decision.top_concerns)
    lines.extend(
        [
            "",
            "## Evidence Limits",
            "",
            "- SDO/LEK instrument-trend evidence is MS1-only.",
            "- MS1-only evidence can support RT/area/width trend review, but it "
            "does not prove chemical identity.",
            "",
            "## MS2 Readiness",
            "",
            decision.ms2_readiness_reason,
            "",
            "## Source Artifacts",
            "",
        ]
    )
    if decision.source_artifacts:
        lines.extend(f"- `{path}`" for path in decision.source_artifacts)
    else:
        lines.append("- No source artifacts found.")
    return "\n".join(lines) + "\n"


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _rows(payload: dict) -> list[dict]:
    rows = payload.get("rows", [])
    return rows if isinstance(rows, list) else []


def _has_flag(rows: list[dict], flag: str) -> bool:
    for row in rows:
        flags = str(row.get("trend_flags", ""))
        if any(item.strip() == flag for item in flags.split(";")):
            return True
    return False


def _has_blank_concern(payload: dict) -> bool:
    summary = payload.get("summary", {})
    if isinstance(summary, dict) and summary.get("contamination_concern"):
        return True
    return _has_flag(_rows(payload), "BLANK_CONTAMINATION")


def _ms2_readiness(
    sdolek_rows: list[dict],
    source_artifacts: tuple[Path, ...],
) -> tuple[str, str]:
    if not sdolek_rows:
        return (
            "not_recommended",
            "MS2 evidence is not recommended yet because SDOLEK MS1 rows are "
            "missing.",
        )
    if not source_artifacts:
        return (
            "not_recommended",
            "MS2 evidence is not recommended yet because source artifacts are "
            "missing.",
        )
    return (
        "not_recommended",
        "MS2 evidence is not recommended until a representative method, "
        "deterministic MS2 reader support, and manual ambiguity case are all "
        "available. The current report keeps SDO/LEK as MS1-only instrument "
        "trend evidence.",
    )

"""Helpers shared by test_explanation_review and other test modules."""

import hashlib
from contextlib import contextmanager

import numpy as np

from tests.alignment_explanation_fixtures import (
    explanation_summary,
)
from tests.inspector_fixtures import NEUTRAL_LOSS_BY_TAG, discovery
from xic_extractor.alignment_explanation.writer import (
    ExplanationReportWriter,
)


def _install_distinct_curve_raws(monkeypatch, opened, cancelled=None, empty=()):
    class Raw:
        def __init__(self, stem):
            self.stem = stem

        def extract_xic(self, mz, lower, upper, ppm):
            if self.stem in empty:
                return np.array([]), np.array([])
            rt = np.linspace(lower, upper, 51)
            amplitude = 1000 if self.stem == "S1" else 2000
            result = rt, amplitude * np.exp(-(((rt - 8) / 0.08) ** 2))
            if cancelled is not None:
                cancelled["value"] = True
            return result

    @contextmanager
    def raw(path, dll, *, expected_acquisition_source, mass_recalibration, **_kwargs):
        opened.append((path.name, expected_acquisition_source.acquisition_source_id))
        yield Raw(path.stem)

    monkeypatch.setattr("xic_workbench.acquisition.open_raw", raw)


def _group_with_missing_apex_review(tmp_path, *, extraction_by_sample=None):
    from dataclasses import asdict

    from xic_extractor.processing_identity import build_acquisition_source_identity

    sources = []
    for sid in ("S1", "S2", "S3"):
        raw = tmp_path / f"{sid}.raw"
        raw.write_bytes(sid.encode())
        sources.append(
            asdict(
                build_acquisition_source_identity(
                    raw,
                    acquisition_format="thermo_raw",
                    observation_namespace="fixture",
                )
            )
        )
    matrix = tmp_path / "matrix.tsv"
    matrix.write_text("saved matrix with an empty S3 cell", encoding="utf-8")
    report = tmp_path / "review.sqlite"
    keys = [("C1", "S1"), ("C2", "S2")]
    extraction = extraction_by_sample or {
        "S1": (499.0, 20.0),
        "S2": (501.0, 20.0),
        "S3": (500.0, 20.0),
    }
    samples = [{"sample_key": sid, "sample_stem": sid} for sid in ("S1", "S2", "S3")]
    with ExplanationReportWriter(report, review_only=True) as writer:
        writer.declare_samples(samples)
        for (cid, sid), mz, rt in zip(keys, (499.0, 501.0), (7.9, 8.1), strict=True):
            writer.add_peak(sid, cid, (extraction[sid][0], rt, extraction[sid][1]))
            writer.add_candidate(cid, sid, precursor_mz=mz, **discovery(cid))
            writer.add_terminal(
                cid, sid, variant="observed", hypothesis_id=cid, physical_peak_key=cid
            )
        writer.add_final_group(
            "G1",
            feature_id="G1",
            disposition="saved",
            members=[
                {
                    "sample_key": sid,
                    "subject_id": cid,
                    "subject_kind": "peak_hypothesis",
                    "physical_peak_keys": [cid],
                }
                for cid, sid in keys
            ],
            center_mz=extraction["S3"][0],
            ppm=extraction["S3"][1],
        )
        writer.set_row_destination("G1", "matrix")
        if extraction_by_sample:
            writer.add_peak(
                "S3",
                "backfilled-peak",
                (extraction["S3"][0], 8.0, extraction["S3"][1]),
            )
        writer.add_cells(
            "G1",
            [
                {
                    "sample_key": sid,
                    "variant": "numeric" if value else "unavailable",
                    "value": value,
                    "observation_ids": observations,
                }
                for sid, value, observations in (
                    ("S1", 100, ["C1"]),
                    ("S2", 200, ["C2"]),
                    (
                        "S3",
                        300 if extraction_by_sample else None,
                        ["backfilled-peak"] if extraction_by_sample else [],
                    ),
                )
            ],
        )
        if not extraction_by_sample:
            writer.set_cell_position("G1", "S3", 8.0)
        writer.finalize(
            explanation_summary(
                {
                    "samples": samples,
                    "neutral_loss_by_tag": NEUTRAL_LOSS_BY_TAG,
                    "acquisition_sources": sources,
                    "matrix_path": str(matrix),
                    "matrix_sha256": hashlib.sha256(matrix.read_bytes()).hexdigest(),
                },
                review_only=True,
            )
        )
    return report, matrix


def _quality_review_report(
    tmp_path, *, membership_risks=None, measured_mz_by_sample=None
):
    """Two samples in one feature: advisory facts traverse the actual review store."""
    from types import SimpleNamespace as NS

    from xic_extractor.alignment_explanation.review import review_cell
    from xic_extractor.peak_detection.signal_quality import SignalQuality

    matrix = tmp_path / "quality-matrix.tsv"
    matrix.write_text(
        "cluster_id\tprecursor_mz\trt_apex_min\tS1\tS2\nrow-1\t500\t1\t1000\t2000\n",
        encoding="utf-8",
    )
    report = tmp_path / "quality-review.sqlite"
    cells = []
    for sid, value, ratio in (("S1", 1000.0, 20.0), ("S2", 2000.0, 2.0)):
        quality = SignalQuality(
            "available",
            noise_sigma=10.0,
            signal_to_noise=ratio,
            low_signal_to_noise=ratio < 10.0 / 1.4826,
        )
        observation = NS(
            physical_peak_key=f"obs-{sid}",
            integration=NS(signal_quality=quality, rt_apex_min=1.0),
            capture_stage="initial_capture",
        )
        cell = NS(
            peak_observation=observation,
            method_result="SUPPORT",
            reason_codes=(),
            physical_peak_key=f"obs-{sid}",
        )
        decision = NS(
            subject=NS(canonical_sample=NS(sample_name=sid)),
            evidence=NS(effective_cells=(cell,), evidence_states=()),
            variant="quantified",
            value=value,
            source="raw_trapezoid",
            selected_integration=observation.integration,
        )
        cells.append(review_cell(decision))
        # Backfill admissions have no owning member; their risks stay on the cell.
        cells[-1]["membership_risks"] = [
            risk
            for risk in (membership_risks or {}).get(sid, [])
            if risk["phase"] == "backfill"
        ]
    # A grouping risk is accepted by the member that owns the peak.
    member_risks = {
        sid: risk
        for sid, risks in (membership_risks or {}).items()
        for risk in risks
        if risk["phase"] != "backfill"
    }
    members = [
        {
            "sample_key": sid,
            "subject_id": f"member-{sid}",
            "subject_kind": "peak_hypothesis",
            "physical_peak_keys": [f"obs-{sid}"],
        }
        for sid in ("S1", "S2")
    ]
    samples = [{"sample_stem": sid, "sample_key": sid} for sid in ("S1", "S2")]
    with ExplanationReportWriter(report, review_only=True) as writer:
        writer.declare_samples(samples)
        keys = [("C1", "S1"), ("C2", "S2")]
        # Each candidate's terminal is its member's hypothesis and peak.
        for cid, sid in keys:
            writer.add_peak(sid, f"obs-{sid}", (None, 1.0, None))
            writer.add_candidate(
                cid,
                sid,
                precursor_mz=500.0,
                ms1_apex_mz=(measured_mz_by_sample or {}).get(sid),
                **discovery(cid),
            )
            writer.add_terminal(
                cid,
                sid,
                variant="observed",
                hypothesis_id=f"member-{sid}",
                physical_peak_key=f"obs-{sid}",
            )
        writer.add_final_group(
            "G1",
            feature_id="G1",
            disposition="completed",
            members=members,
        )
        if member_risks:
            writer.add_initial_group(
                disposition="completed",
                members=members,
                inference_basis={},
                inference_decisions=[
                    {"owner_id": f"member-{sid}", "accepted_risk": risk}
                    for sid, risk in member_risks.items()
                ],
            )
        writer.add_cells("G1", cells)
        writer.finalize(
            explanation_summary(
                {
                    "processing_attempt_id": "quality-attempt",
                    "neutral_loss_by_tag": NEUTRAL_LOSS_BY_TAG,
                    "matrix_path": str(matrix),
                    "matrix_sha256": hashlib.sha256(matrix.read_bytes()).hexdigest(),
                    "samples": samples,
                },
                review_only=writer.review_only,
            )
        )
    return report, matrix


def _competition_review_report(
    tmp_path, *, phase="initial", flag="accepted_competition"
):
    risk = {
        "status": "evaluated_alternative",
        "phase": phase,
        "selected_owners": [["S1", 500.0, 1.0], ["S2", 500.0, 1.1]],
        "alternative_partition": [
            [["S1", 500.0, 1.0]],
            [["S2", 500.0, 1.1], ["S3", 500.0, 1.2]],
        ],
        "equivalent_alternatives": [],
        "selected_cost": 1.2,
        "alternative_cost": 1.23,
        "cost_gap": 0.03,
        "rt_basis": "raw",
        "evidence_basis": "bounded_assignment",
        "soft_contradictions": [],
        "risk_flags": [flag],
        "selected_peer_fit": 0.25,
        "alternative_peer_fit": 0.4,
        "peer_fit_gap": 0.15,
        "rt_estimate": {
            "rt_min": 8.0,
            "method": "median_shift",
            "anchor_count": 2,
            "anchor_range_min": [7.0, 9.0],
            "residual_spread_min": 0.02,
            "nearest_anchor_distance_min": 1.0,
            "outside_anchor_range_min": 0.0,
            "fit_status": "insufficient_anchors",
        },
    }
    return _quality_review_report(tmp_path, membership_risks={"S2": [risk]})

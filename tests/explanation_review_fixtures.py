"""Helpers shared by test_explanation_review and other test modules."""

import hashlib
from contextlib import contextmanager

import numpy as np

from tests.alignment_explanation_fixtures import (
    explanation_summary,
)
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


def _group_with_missing_apex_review(tmp_path):
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
    with ExplanationReportWriter(report, review_only=True) as writer:
        for (cid, sid), mz, rt in zip(keys, (499.0, 501.0), (7.9, 8.1), strict=True):
            writer.add_candidate(
                cid,
                sid,
                {
                    "candidate_id": cid,
                    "sample_key": sid,
                    "precursor_mz": mz,
                    "rt_apex_min": rt,
                },
            )
            writer.add_record(
                "terminal_subjects",
                cid,
                {
                    "candidate_id": cid,
                    "sample_key": sid,
                    "observations": [
                        {"physical_peak_key": cid, "rt_apex_min": rt},
                        {"physical_peak_key": "other-group", "rt_apex_min": 99},
                    ],
                },
                [(cid, sid)],
            )
        writer.add_record(
            "final_groups",
            "G1",
            {
                "feature_id": "G1",
                "member_subjects": [
                    {"sample_key": sid, "physical_peak_keys": [cid]}
                    for cid, sid in keys
                ],
            },
            keys,
        )
        writer.add_record(
            "rows",
            "G1",
            {
                "feature_id": "G1",
                "route": {"destination": "matrix", "reason": "saved"},
            },
            keys,
        )
        writer.add_row_cells(
            "G1",
            [
                {
                    "sample_key": sid,
                    "variant": "numeric" if value else "unavailable",
                    "value": value,
                }
                for sid, value in (("S1", 100), ("S2", 200), ("S3", None))
            ],
            {"S1": 0, "S2": 1, "S3": 2},
        )
        writer.finalize(
            explanation_summary(
                {
                    "samples": [{"sample_key": sid} for sid in ("S1", "S2", "S3")],
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
            content=NS(
                physical_peak_key=f"obs-{sid}",
                integration=NS(signal_quality=quality),
            ),
            capture_stage="initial_capture",
        )
        cell = NS(
            peak_observation=observation,
            content=NS(
                method_result="SUPPORT",
                reason_codes=(),
                physical_peak_key=f"obs-{sid}",
            ),
        )
        decision = NS(
            subject=NS(canonical_sample=NS(identity=NS(sample_key=sid))),
            evidence=NS(effective_cells=(cell,), evidence_states=()),
            variant="quantified",
            value=value,
            source="raw_trapezoid",
        )
        cells.append(review_cell(decision))
        if membership_risks is not None:
            cells[-1]["membership_risks"] = membership_risks.get(sid, [])
    with ExplanationReportWriter(report, review_only=True) as writer:
        keys = [("C1", "S1"), ("C2", "S2")]
        for cid, sid in keys:
            writer.add_candidate(
                cid,
                sid,
                {
                    "candidate_id": cid,
                    "sample_key": sid,
                    "precursor_mz": 500.0,
                    "ms1_apex_mz": (measured_mz_by_sample or {}).get(sid),
                    "rt_apex_min": 1.0,
                },
            )
        writer.add_record(
            "final_groups",
            "G1",
            {"feature_id": "G1", "disposition": "completed"},
            keys,
        )
        writer.add_record("rows", "G1", {"feature_id": "G1"}, keys)
        writer.add_row_cells("G1", cells, {"S1": 0, "S2": 1})
        writer.finalize(
            explanation_summary(
                {
                    "processing_attempt_id": "quality-attempt",
                    "matrix_path": str(matrix),
                    "matrix_sha256": hashlib.sha256(matrix.read_bytes()).hexdigest(),
                    "samples": [
                        {"sample_stem": sid, "sample_key": sid} for sid in ("S1", "S2")
                    ],
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

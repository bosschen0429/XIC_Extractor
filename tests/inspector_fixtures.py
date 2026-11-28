"""Persisted Inspector and review cases, independent of API test modules."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tests.alignment_explanation_fixtures import explanation_summary
from xic_extractor.alignment_explanation.storage import encode_review_curve


def create_review_batch(tmp_path, sample_count=2, *, keep_curves=False):
    """Saved review rows; ``keep_curves`` saves each value's curve as Alignment does."""
    from xic_extractor.alignment_explanation.writer import (
        ExplanationReportWriter,
    )

    sample_ids = tuple(f"S{i + 1}" for i in range(sample_count))
    samples = [{"sample_key": s, "sample_stem": s} for s in sample_ids]
    matrix = tmp_path / "matrix.tsv"
    matrix.write_text("recorded matrix fixture", encoding="utf-8")
    report = tmp_path / "review.sqlite"
    with ExplanationReportWriter(report) as writer:
        writer.declare_samples(samples)
        for rid, route, values in (
            ("a-complete", "matrix", tuple(100 * (i + 1) for i in range(sample_count))),
            ("b-missing", "matrix", (300,) + ("unavailable",) * (sample_count - 1)),
            ("z-priority", "matrix", (400,) + ("unresolved",) * (sample_count - 1)),
            ("archive", "archive", (500,) + ("unresolved",) * (sample_count - 1)),
        ):
            members = [
                add_witness(writer, f"{rid}-{sid}", sid, rt=1, precursor_mz=value)
                for sid, value in zip(sample_ids, values, strict=True)
                if isinstance(value, int)
            ]
            writer.add_final_group(
                rid, feature_id=rid, disposition="recorded", members=members
            )
            writer.set_row_destination(rid, route)
            peaks = {
                member["sample_key"]: member["physical_peak_keys"] for member in members
            }
            writer.add_cells(
                rid,
                [
                    {
                        "sample_key": sid,
                        "variant": "numeric" if isinstance(value, int) else value,
                        "value": value if isinstance(value, int) else None,
                        "observation_ids": peaks.get(sid, []),
                        "apex_rt_min": 1 if isinstance(value, int) else None,
                    }
                    for sid, value in zip(sample_ids, values, strict=True)
                ],
            )
            if keep_curves:
                for sid, value in zip(sample_ids, values, strict=True):
                    if isinstance(value, int):
                        rt = [0.5 + 0.02 * i for i in range(51)]
                        raw = [value * 2.718 ** (-(((x - 1) / 0.1) ** 2)) for x in rt]
                        writer.set_cell_curve(rid, sid, encode_review_curve(rt, raw))
        add_witness(writer, "orphan", "S1", rt=1, precursor_mz=600)
        writer.finalize(
            explanation_summary(
                {
                    "matrix_path": str(matrix),
                    "matrix_sha256": hashlib.sha256(matrix.read_bytes()).hexdigest(),
                    "samples": samples,
                    "neutral_loss_by_tag": NEUTRAL_LOSS_BY_TAG,
                },
            )
        )
    return report, matrix


def create_nearest_review_batch(tmp_path):
    """Saved matrix rows whose review order differs from numeric proximity."""
    from xic_extractor.alignment_explanation.writer import (
        ExplanationReportWriter,
    )

    matrix = tmp_path / "nearest-matrix.tsv"
    matrix.write_text("saved near-search fixture", encoding="utf-8")
    report = tmp_path / "nearest-review.sqlite"
    rows = [(f"far-{i:02}", ((200.0 + i, 1.0),), False) for i in range(28)] + [
        ("near-a", ((123.3980, None),), False),
        ("near-b", ((123.4200, 5.0),), False),
        ("near-c", ((123.4567, 10.0),), True),
        ("missing-mz", ((None, 1.0),), False),
        ("multi-witness", ((350.0, 2.0), (123.4, 2.5)), False),
    ]
    samples = [{"sample_key": "S1", "sample_stem": "S1"}]
    with ExplanationReportWriter(report) as writer:
        writer.declare_samples(samples)
        for rid, witnesses, unresolved in rows:
            members = [
                add_witness(writer, f"{rid}-{index}", "S1", rt=rt, precursor_mz=mz)
                for index, (mz, rt) in enumerate(witnesses)
            ]
            writer.add_final_group(
                rid, feature_id=rid, disposition="recorded", members=members
            )
            writer.set_row_destination(rid, "matrix")
            writer.add_cells(
                rid,
                [
                    {
                        "sample_key": "S1",
                        "variant": "unresolved" if unresolved else "numeric",
                        "value": None if unresolved else 100.0,
                    }
                ],
            )
        writer.finalize(
            explanation_summary(
                {
                    "matrix_path": str(matrix),
                    "matrix_sha256": hashlib.sha256(matrix.read_bytes()).hexdigest(),
                    "samples": samples,
                    "neutral_loss_by_tag": NEUTRAL_LOSS_BY_TAG,
                },
            )
        )
    return report, matrix


def discovery(candidate_id: str) -> dict[str, str]:
    """What Discovery records for every candidate: its loss and method subject."""
    return {"neutral_loss_tag": "dR", "method_subject_id": f"subject-{candidate_id}"}


NEUTRAL_LOSS_BY_TAG = {"dR": 116.0474}


def witness_member(sample_key: str, hypothesis_id: str, *peaks: str) -> dict:
    """A final-group member whose hypothesis is a candidate's terminal subject."""
    return {
        "sample_key": sample_key,
        "subject_id": hypothesis_id,
        "subject_kind": "peak_hypothesis",
        "physical_peak_keys": list(peaks),
    }


def add_witness(
    writer,
    candidate_id: str,
    sample_key: str,
    *,
    rt: float | None,
    peak_mz: float | None = None,
    **candidate: Any,
) -> dict:
    """A candidate observed at its terminal peak; returns the group member.

    The witness RT is the terminal peak's apex; ``rt=None`` keeps it unrecorded.
    """
    peak = f"peak-{candidate_id}-{sample_key}"
    hypothesis = f"hypothesis-{candidate_id}-{sample_key}"
    writer.add_peak(sample_key, peak, (peak_mz, rt, None))
    writer.add_candidate(
        candidate_id,
        sample_key,
        **{**discovery(candidate_id), **candidate},
    )
    writer.add_terminal(
        candidate_id,
        sample_key,
        hypothesis_id=hypothesis,
        physical_peak_key=peak,
    )
    return witness_member(sample_key, hypothesis, peak)


def read_report_payload(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    match = re.search(
        r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', text, re.S
    )
    assert match is not None
    return json.loads(match.group(1))


def create_inspector_report(
    directory: Path,
    *,
    attempt_id: str = "att-01",
    samples: list[dict[str, str]] | None = None,
    candidate: dict[str, Any] | None = None,
    observed: bool = True,
    hypothesis_id: str = "hypothesis-01",
    group: dict[str, Any] | None = None,
    cells: list[dict[str, Any]] | None = None,
    base_cell: dict[str, Any] | None = None,
    prepare: Callable[[Any], None] | None = None,
    extend: Callable[[Any], None] | None = None,
    summary: dict[str, Any] | None = None,
) -> tuple[Path, Path, str]:
    """One saved row ``G1`` whose candidate ``C1`` was observed as ``obs-01``.

    ``G1``'s default member is ``C1``'s terminal hypothesis ``hypothesis_id``.

    ``prepare`` adds candidates before ``G1`` is written (so ``group`` can name
    them); ``extend`` adds further evidence before finalization. Both receive
    the active writer.
    """
    from xic_extractor.alignment_explanation.writer import ExplanationReportWriter

    db_path = directory / "alignment_explanation.sqlite"
    matrix_path = directory / "alignment_matrix.tsv"

    matrix_content = (
        b"cluster_id\tprecursor_mz\trt_apex_min\tS1\tS2\nrow-01\t500.0\t1.0\t1000\t0\n"
    )
    matrix_path.write_bytes(matrix_content)
    with matrix_path.open("rb") as handle:
        matrix_hash = hashlib.file_digest(handle, "sha256").hexdigest()

    if samples is None:
        samples = [
            {"sample_stem": "s1", "sample_key": "S1"},
            {"sample_stem": "s2", "sample_key": "S2"},
        ]
    samples = [{"sample_stem": sample["sample_key"], **sample} for sample in samples]

    sid = samples[0]["sample_key"]
    with ExplanationReportWriter(db_path) as writer:
        writer.declare_samples(samples)
        writer.add_peak(sid, "obs-01", (None, 1.0, None))
        writer.add_candidate(
            "C1", sid, precursor_mz=500.0, **{**discovery("C1"), **(candidate or {})}
        )
        if observed:
            writer.add_terminal(
                "C1",
                sid,
                hypothesis_id=hypothesis_id,
                physical_peak_key="obs-01",
            )
        if prepare is not None:
            prepare(writer)
        writer.add_final_group(
            "G1",
            **{
                "feature_id": "G1",
                "disposition": "completed",
                "members": [witness_member(sid, hypothesis_id, "obs-01")],
                **(group or {}),
            },
        )
        writer.set_row_destination("G1", "matrix")
        writer.add_cells(
            "G1",
            cells
            if cells is not None
            else [
                {
                    "sample_key": sid,
                    "variant": "numeric",
                    "value": 1000.0,
                    **(base_cell or {}),
                }
            ],
        )
        if extend is not None:
            extend(writer)
        writer.finalize(
            explanation_summary(
                {
                    "processing_attempt_id": attempt_id,
                    "matrix_path": str(matrix_path),
                    "matrix_sha256": matrix_hash,
                    "samples": samples,
                    "neutral_loss_by_tag": {"dR": 116.0474},
                    **(summary or {}),
                },
            )
        )

    return db_path, matrix_path, matrix_hash

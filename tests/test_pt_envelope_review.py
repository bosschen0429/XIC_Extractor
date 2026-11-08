"""Analytical envelope arithmetic and review-only acquisition/publication."""

import json
from dataclasses import asdict, replace

import pytest

from xic_extractor.evidence.pt_envelope import (
    POSITION_LABELS,
    EnvelopeTrace,
    compare_pt_envelope,
)


def traces_for(ratios):
    rt = tuple(2 + sec / 60 for sec in range(-32, 33, 2))
    shape = tuple(max(0, 10 - abs((t - 2) * 60)) for t in rt)
    return tuple(
        EnvelopeTrace(label, rt, tuple(5 + 100 * ratio * y for y in shape))
        for label, ratio in zip(POSITION_LABELS, ratios, strict=True)
    )


def test_synchronous_core_ignores_a_partner_maximum_outside_the_base_peak():
    # Independent n=0 natural Pt ratios, not a snapshot from the implementation.
    ratios = (1, 0.3378 / 0.3286, 0.2521 / 0.3286, 0.07356 / 0.3286)
    traces = list(traces_for(ratios))
    values = list(traces[3].intensity)
    values[23] = 100_000  # +14 seconds, outside the shared core and flanks.
    traces[3] = replace(traces[3], intensity=tuple(values))
    result = compare_pt_envelope(tuple(traces), base_mz=200, rt_apex_min=2)
    assert result.status == "available"
    assert result.model == "Pt+C"
    assert result.carbon_count == 0
    assert result.spread == pytest.approx(1)
    assert result.core_scan_count == 5
    assert result.observed_relative == pytest.approx(ratios)


def test_sulfur_composition_is_compared_without_claiming_identity():
    # Pt*S probabilities at 0,1,2,4; explicit isotope combinations give the oracle.
    envelope = (
        0.3286 * 0.9499 + 0.00782 * 0.0425 + 0.00012 * 0.0001,
        0.3378 * 0.9499 + 0.3286 * 0.0075,
        0.2521 * 0.9499 + 0.3378 * 0.0075 + 0.3286 * 0.0425 + 0.00782 * 0.0001,
        0.07356 * 0.9499 + 0.2521 * 0.0425 + 0.3286 * 0.0001,
    )
    ratios = tuple(v / envelope[0] for v in envelope)
    result = compare_pt_envelope(traces_for(ratios), base_mz=230, rt_apex_min=2)
    assert result.status == "available"  # Never match/nonmatch.
    assert result.model == "Pt+C+S"
    assert result.carbon_count == 0
    assert result.spread == pytest.approx(1)


@pytest.mark.parametrize(
    "problem,reason",
    [
        ("missing", "missing_position:plus_4"),
        ("shifted", "rt_grid_mismatch"),
        ("zero", "nonpositive_core:plus_4"),
        ("short", "insufficient_background"),
    ],
)
def test_missing_evidence_does_not_become_a_bad_fit_score(problem, reason):
    traces = list(traces_for((1, 1, 1, 1)))
    if problem == "missing":
        traces.pop()
    elif problem == "shifted":
        traces[3] = replace(traces[3], rt=tuple(t + 0.0001 for t in traces[3].rt))
    elif problem == "zero":
        traces[3] = replace(traces[3], intensity=tuple(5.0 for _ in traces[3].rt))
    else:
        traces = [
            replace(t, rt=t.rt[12:21], intensity=t.intensity[12:21]) for t in traces
        ]
    result = compare_pt_envelope(tuple(traces), base_mz=400, rt_apex_min=2)
    assert result.status == "unavailable"
    assert result.reason == reason
    assert result.spread is None


def test_real_review_entry_adds_envelope_without_changing_saved_candidates(
    tmp_path, monkeypatch
):
    from tools.diagnostics.delta_mass_review import main
    from xic_extractor.configuration.delta_mass import (
        DeltaMassPatternSettings,
        DeltaMassPosition,
    )
    from xic_extractor.diagnostics import delta_mass_review_acquisition as acquisition
    from xic_extractor.diagnostics.delta_mass_review_data import (
        decode_delta_mass_review,
    )
    from xic_extractor.discovery.delta_mass_csv import write_delta_mass_outputs
    from xic_extractor.discovery.delta_mass_patterns import (
        DeltaMassCandidate,
        DeltaMassPositionResult,
    )
    from xic_extractor.xic_models import XICTrace

    deltas = (0.0, 1.002111, 2.002271, 4.005214)
    settings = DeltaMassPatternSettings(
        "pt",
        "Pt pattern",
        tuple(
            DeltaMassPosition(label, delta, 0.01, 10)
            for label, delta in zip(POSITION_LABELS, deltas, strict=True)
        ),
        ("base",),
        ("plus_4",),
    )
    candidates = tuple(
        DeltaMassCandidate(
            sample_stem=sample,
            method_label="Pt pattern",
            pattern_id="pt",
            candidate_id=sample,
            anchor_position="base",
            anchor_feature_id="base",
            base_mz=mass,
            base_rt_apex_min=2,
            base_height=1000,
            positions=tuple(
                DeltaMassPositionResult(
                    label,
                    delta,
                    mass + delta,
                    0.01,
                    10,
                    2,
                    partner_source="anchor" if i == 0 else "table",
                    apex_rt_min=2,
                    position_status="pass",
                )
                for i, (label, delta) in enumerate(
                    zip(POSITION_LABELS, deltas, strict=True)
                )
            ),
        )
        for sample, mass in (("pt-like", 200.0), ("organic-like", 400.0))
    )
    discovery = tmp_path / "discovery"
    write_delta_mass_outputs(
        discovery, candidates, {"method": asdict(settings)}, settings
    )
    before = {p.name: p.read_bytes() for p in discovery.iterdir()}
    raw = tmp_path / "raw"
    raw.mkdir()
    for c in candidates:
        (raw / f"{c.sample_stem}.raw").touch()
    calls = []

    class Reader:
        def __init__(self, path):
            self.sample = path.stem

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def acquisition_rt_bounds(self):
            return 0.0, 4.0

        def extract_xic_many(self, requests):
            calls.append((self.sample, len(requests)))
            ratios = (
                (1, 0.3378 / 0.3286, 0.2521 / 0.3286, 0.07356 / 0.3286)
                if self.sample == "pt-like"
                else (1, 0.2, 0.04, 0.001)
            )
            traces = traces_for(ratios)
            base = 200 if self.sample == "pt-like" else 400
            return tuple(
                XICTrace.from_arrays(
                    traces[0].rt,
                    traces[
                        min(range(4), key=lambda j: abs(r.mz - base - deltas[j]))
                    ].intensity,
                )
                for r in requests
            )

    monkeypatch.setattr(acquisition, "open_raw", lambda path, **kwargs: Reader(path))
    output = tmp_path / "review.json"
    assert (
        main(
            [
                "--discovery-dir",
                str(discovery),
                "--all-candidates",
                "--raw-dir",
                str(raw),
                "--dll-dir",
                str(tmp_path),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    review = decode_delta_mass_review(json.loads(output.read_text()))
    by_sample = {c.sample_stem: c for c in review.candidates}
    assert by_sample["pt-like"].envelope.spread == pytest.approx(1)
    assert by_sample["organic-like"].envelope.spread > 10
    assert all(
        c.status == "candidate" and not c.reject_reason for c in review.candidates
    )
    assert {p.name: p.read_bytes() for p in discovery.iterdir()} == before
    assert sorted(calls) == [("organic-like", 4), ("pt-like", 4)]

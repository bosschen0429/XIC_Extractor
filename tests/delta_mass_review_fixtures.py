"""Analytical candidate/scan examples, independent of the detection algorithm."""

import json
from dataclasses import asdict, replace
from pathlib import Path

from xic_extractor.configuration.delta_mass import (
    DeltaMassPatternSettings,
    DeltaMassPosition,
)
from xic_extractor.diagnostics.delta_mass_review_data import (
    DeltaMassReview,
    ReviewCandidate,
    ReviewNlMatch,
    ReviewNlPosition,
    ReviewPosition,
    ReviewSpectrum,
    write_delta_mass_review,
)
from xic_extractor.discovery.delta_mass_csv import write_delta_mass_outputs
from xic_extractor.discovery.delta_mass_patterns import (
    DeltaMassCandidate,
    DeltaMassPositionResult,
)
from xic_extractor.discovery.detected_features import (
    DetectedFeature,
    write_detected_features_csv,
)
from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata
from xic_inspector.rendering.html_builder import build_report_html
from xic_inspector.sources.delta_mass import DeltaMassReviewSource


def mount_review_html(source: Path, output: Path, *, limit=30, strategy="sample"):
    """Mount the production source adapter and renderer with a bounded UI fixture.

    File order selects the fixture's items; API tests own query/sampling policy.
    Browser assertions protect the resulting DOM and SVG, not source dispatch.
    """
    adapter = DeltaMassReviewSource(
        source, (json.loads(source.read_text(encoding="utf-8")), "browser-fixture")
    )
    samples = adapter.summary["samples"]
    items = adapter.load_items({s["sample_key"] for s in samples})
    rendered = adapter.load_render_items(
        items[:limit], capability="retained_curves", check_cancel=lambda: None
    )
    payload = {
        "summary": adapter.summary,
        "selection": {
            "strategy": strategy,
            "limit": limit,
            "offset": 0,
            "review_scope": "all",
            "case_counts": {},
        },
        "samples": [
            {"id": s["sample_key"], "label": s["sample_stem"]} for s in samples
        ],
        "items": rendered,
    }
    output.write_text(
        build_report_html(
            title="Inspector",
            evidence_capability="retained_curves",
            strategy=strategy,
            run_total=len(items),
            eligible_count=len(items),
            selected_count=len(rendered),
            source_db_name=source.name,
            payload_data=payload,
        ),
        encoding="utf-8",
    )


def write_discovery(directory: Path) -> tuple[str, ...]:
    settings = DeltaMassPatternSettings(
        pattern_id="three-position",
        method_label="Three positions",
        positions=tuple(
            # Sidecar/feature precision exceeds the display-oriented position CSV.
            DeltaMassPosition(
                label, float(i * 3) + (0.0000001234 if i else 0), 0.8, 1.25
            )
            for i, label in enumerate(("M", "partner", "third"))
        ),
        anchor_positions=("M",),
        discriminating_positions=("partner",),
        ratio_reference_position="partner",
        mz_tolerance_ppm=7.0,
    )
    candidates = []
    for sample, apex, base in (
        ("sample-a", 2.0, 100.0),
        ("sample-a", 2.2, 110.0),
        ("sample-b", 2.0, 200.0),
    ):
        candidate_id = f"{sample}|{base}"
        candidates.append(
            DeltaMassCandidate(
                sample_stem=sample,
                method_label=settings.method_label,
                pattern_id=settings.pattern_id,
                candidate_id=candidate_id,
                anchor_position="M",
                anchor_feature_id="saved-anchor",
                base_mz=base,
                base_rt_apex_min=apex,
                base_height=100.0,
                positions=tuple(
                    DeltaMassPositionResult(
                        position_label=p.label,
                        delta_da=p.delta_da,
                        expected_mz=base + p.delta_da,
                        observed_mz=base + p.delta_da,
                        feature_id=f"{candidate_id}|{p.label}",
                        ratio_min=p.ratio_min,
                        ratio_max=p.ratio_max,
                        apex_threshold_sec=1.0,
                        apex_rt_min=apex,
                        height=100.0 * (i + 1),
                        height_ratio=2.5 if i == 0 else 1.0,
                        ratio_status="outside" if i == 0 else "within",
                        shape_r=0.95,
                        position_status="pass",
                    )
                    for i, p in enumerate(settings.positions)
                ),
            )
        )
    write_delta_mass_outputs(
        directory, candidates, {"method": asdict(settings)}, settings
    )
    for sample in {c.sample_stem for c in candidates}:
        sample_dir = directory / sample
        sample_dir.mkdir()
        write_detected_features_csv(
            sample_dir / "detected_features.csv",
            tuple(
                DetectedFeature(
                    sample,
                    p.feature_id,
                    None,
                    p.expected_mz,
                    c.base_rt_apex_min,
                    c.base_rt_apex_min - 0.2,
                    c.base_rt_apex_min + 0.2,
                    100.0,
                    None,
                    "OK",
                    None,
                    "external",
                )
                for c in candidates
                if c.sample_stem == sample
                for p in c.positions
            ),
        )
    return tuple(c.candidate_id for c in candidates)


def review_fixture() -> DeltaMassReview:
    candidates = []
    for sample, base in (("sample-a", 100.0), ("sample-b", 200.0)):
        candidates.append(
            ReviewCandidate(
                candidate_id=f"{sample}|{base}",
                sample_stem=sample,
                method_label="Three positions",
                pattern_id="three-position",
                status="candidate",
                reject_reason="",
                base_mz=base,
                base_rt_apex_min=2.0,
                positions=tuple(
                    ReviewPosition(
                        label=label,
                        delta_da=float(i * 3),
                        expected_mz=base + i * 3,
                        observed_mz=base + i * 3,
                        feature_mz=base + i * 3 + (0.000321 if i else 0),
                        position_status="pass",
                        raw_rt=(1.0, 1.9, 2.0, 2.1, 3.0),
                        raw_intensity=tuple(
                            v * (i + 1) for v in (0.0, 20.0, 100.0, 30.0, 0.0)
                        ),
                    )
                    for i, label in enumerate(("M", "partner", "third"))
                ),
                raw_file=f"{sample}.raw",
                xic_ppm=7.0,
                rt_window=(1.0, 3.0),
                spectra=(
                    ReviewSpectrum(
                        scan_number=11,
                        rt_min=1.9,
                        precursor_mz=999.0,
                        precursor_metadata=Ms2PrecursorMetadata(
                            charge=2, isolation_width_da=1.5
                        ),
                        mz=(50.0, 80.0, 140.0),
                        intensity=(20.0, 100.0, 10.0),
                    ),
                    ReviewSpectrum(
                        scan_number=22,
                        rt_min=2.1,
                        precursor_mz=base,
                        precursor_metadata=None,
                        mz=(60.0, 90.0),
                        intensity=(30.0, 80.0),
                    ),
                )
                if sample == "sample-a"
                else (),
            )
        )
    return DeltaMassReview("saved-discovery", tuple(candidates))


def write_review(directory: Path) -> Path:
    path = directory / "delta-review.json"
    write_delta_mass_review(review_fixture(), path)
    return path


def write_nl_review(directory: Path) -> Path:
    review = review_fixture()
    a, b = review.candidates
    a = replace(
        a,
        positions=tuple(replace(p, peak_rt_window=(1.8, 2.2)) for p in a.positions),
        spectra=(
            replace(
                a.spectra[0],
                precursor_mz=100.0,
                precursor_metadata=None,
                mz=(60.0, 90.0),
                intensity=(20.0, 100.0),
            ),
            a.spectra[1],
        ),
        neutral_losses=(
            ReviewNlPosition(
                "M",
                "test-NL",
                40.0,
                "supported",
                (11, 22),
                (
                    ReviewNlMatch(
                        11, 100.0, 60.0, 0.2, 40.0, 0.0, "scan_precursor", "supported"
                    ),
                    ReviewNlMatch(
                        22, 100.0, 60.0, 0.375, 40.0, 0.0, "scan_precursor", "supported"
                    ),
                ),
            ),
            ReviewNlPosition("partner", "test-NL", 40.0, "no_ms2", (), ()),
            ReviewNlPosition("third", "test-NL", 40.0, "no_match", (11,), ()),
        ),
    )
    b = replace(
        b, neutral_losses=(ReviewNlPosition("M", "test-NL", 40.0, "no_ms2", (), ()),)
    )
    path = directory / "nl-review.json"
    write_delta_mass_review(replace(review, candidates=(a, b)), path)
    return path

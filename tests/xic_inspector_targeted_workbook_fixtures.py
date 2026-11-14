"""Helpers shared by test_xic_inspector_targeted_workbook and other test modules."""

from __future__ import annotations

from openpyxl import Workbook


def install_targeted_raws(
    monkeypatch, raw_dir, scales, *, pair_scale=1.0, impulse_samples=()
):
    from contextlib import contextmanager

    import numpy as np

    from xic_extractor.acquisition import review_xics

    raw_dir.mkdir(exist_ok=True)
    for sample in scales:
        (raw_dir / f"{sample}.raw").write_bytes(sample.encode())
    reads = []

    from xic_extractor.configuration.mass_recalibration import (
        VENDOR_MASS_RECALIBRATION_RESULT,
    )

    class Raw:
        mass_recalibration_result = VENDOR_MASS_RECALIBRATION_RESULT

        def __init__(self, name):
            self.name = name

        def acquisition_rt_bounds(self):
            return 0.0, 12.0

        def extract_xic(self, mz, lower, upper, ppm):
            reads.append((self.name, mz, lower, upper, ppm))
            rt = np.linspace(lower, upper, 1201)
            scale = scales[self.name] * (pair_scale if mz > 260 else 1.0)
            if mz < 260 and self.name in impulse_samples:
                intensity = np.zeros_like(rt)
                intensity[np.argmin(abs(rt - 9.0))] = scale
                return rt, intensity
            return rt, scale * np.exp(-(((rt - 9.0) / 0.1) ** 2))

    @contextmanager
    def open_raw(path, dll_dir, **kwargs):
        yield Raw(path.stem)

    monkeypatch.setattr(review_xics, "open_raw", open_raw)
    return reads


def write_rare_detection_workbook(path):
    """Five samples: one counted Analyte peak among shared absences."""
    book = Workbook()
    targets = book.active
    targets.title = "Targets"
    targets.append(["Label", "Role", "ISTD Pair", "m/z", "RT min", "RT max"])
    targets.append(["Analyte", "Analyte", "ISTD", 258.1085, 8, 10])
    targets.append(["ISTD", "ISTD", None, 261.127, 8, 10])
    results = book.create_sheet("XIC Results")
    results.append(
        [
            "SampleName",
            "Target",
            "RT",
            "Area",
            "PeakStart",
            "PeakEnd",
            "Review",
            "Review Reason",
            "Reason",
            "Counted Detection",
        ]
    )
    for index, sample in enumerate(("S1", "S2", "S3", "S4", "S5")):
        hit = sample == "S3"
        results.append(
            [
                sample,
                "Analyte",
                9.4 if hit else "ND",
                55.5 if hit else "ND",
                None,
                None,
                None if hit else "Review",
                None if hit else "NL missing",
                None,
                "TRUE" if hit else "FALSE",
            ]
        )
        results.append(
            [
                sample,
                "ISTD",
                9.0 + index / 10,
                1000 + index,
                None,
                None,
                None,
                None,
                None,
                "TRUE",
            ]
        )
    metadata = book.create_sheet("Run Metadata")
    metadata.append(["Key", "Value"])
    metadata.append(["targeted_output_schema_version", "targeted_output"])
    book.save(path)
    book.close()
    return path


def acquire_targeted_bundle(
    workbook,
    *,
    raw_dir,
    dll_dir,
    target_id,
    sample_ids,
    verified_raws=None,
    is_cancelled=None,
):
    """Exercise source interpretation, real processing and Inspector projection."""
    from contextlib import closing

    from xic_extractor.acquisition.review_xics import acquire_review_xics
    from xic_inspector.sources.targeted_workbook import TargetedWorkbookSource
    from xic_inspector.targeted_curves import targeted_curve_bundle

    source = TargetedWorkbookSource(workbook)
    samples = tuple(dict.fromkeys(sample_ids))
    coordinates = source.curve_targets(target_id, samples)
    mass_policy, profile = source.curve_processing_settings()
    with closing(
        acquire_review_xics(
            raw_dir=raw_dir,
            dll_dir=dll_dir,
            sample_ids=samples,
            coordinates=coordinates,
            mass_policy=mass_policy,
            profile=profile,
            support_reference_rts=source.support_reference_rts(
                samples, tuple(coordinates)
            ),
            verified_raws=verified_raws,
            is_cancelled=is_cancelled,
        )
    ) as signals:
        return targeted_curve_bundle(source, target_id, signals)

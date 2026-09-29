from __future__ import annotations

import csv
import json
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from xic_extractor.configuration.models import Target
from xic_extractor.diagnostics.reference_consistency import (
    ClusterObservation,
    ReferencePolicy,
    check_reference_consistency,
    trace_cluster_apices,
    trace_clusters,
)
from xic_extractor.xic_models import XICTrace


def target(label: str, rt: float) -> Target:
    return Target(label, 300.119, rt - 1, rt + 1, 20, None, None, None, False, "")


def test_leave_one_out_warns_without_changing_reference() -> None:
    # Independent oracle: other anchors obey y = 2*x; held-out x=3 predicts 6.
    groups = {
        "a": [target("left", 2), target("held", 3), target("right", 5)],
        "b": [target("left", 4), target("held", 9), target("right", 10)],
    }
    before = deepcopy(groups)
    report = check_reference_consistency(groups)
    row = next(r for r in report["rows"] if r["group"] == "b" and r["label"] == "held")
    assert row["predicted_rt_min"] == 6
    assert row["residual_min"] == 3
    assert "mapping_residual" in row["warnings"]
    assert row["severity"] == "info"  # Mapping alone never proves wrong reference.
    assert groups == before


def test_cluster_ordinal_and_between_cluster_warnings() -> None:
    groups = {
        g: [target("early", 8 if g == "c" else 2), target("gap", 5)] for g in "abc"
    }
    observations = [ClusterObservation(g, g, "early", (2, 8)) for g in "abc"]
    observations += [ClusterObservation("a", "a", "gap", (4, 6))]
    report = check_reference_consistency(groups, observations)
    rows = {(r["group"], r["label"]): r for r in report["rows"]}
    assert rows["c", "early"]["severity"] == "high"
    assert "cluster_ordinal_mismatch" in rows["c", "early"]["warnings"]
    assert "reference_between_clusters" in rows["a", "gap"]["warnings"]
    assert rows["a", "early"]["severity"] == "none"


def test_broad_peak_containing_reference_is_not_between_clusters() -> None:
    report = check_reference_consistency(
        {"one": [target("m5C", 10.70)]},
        [
            ClusterObservation(
                "one",
                "sample",
                "m5C",
                (8.79, 11.05, 12.58),
                extent_rt_min=((8.65, 8.95), (10.45, 11.55), (12.4, 12.8)),
            )
        ],
    )
    row = report["rows"][0]
    assert row["nearest_distance_min"] == pytest.approx(0.35)
    assert row["warnings"] == []
    assert row["severity"] == "none"


def test_half_prominence_extents_from_trace_protect_broad_peak() -> None:
    rt = np.linspace(7, 14, 1401)
    intensity = (
        100 * np.exp(-(((rt - 8.79) / 0.12) ** 2))
        + 100 * np.exp(-(((rt - 11.05) / 0.8) ** 2))
        + 100 * np.exp(-(((rt - 12.58) / 0.12) ** 2))
    )
    clusters = trace_clusters(XICTrace.from_arrays(rt, intensity))
    assert len(clusters) == 3
    assert clusters[1][1] < 10.70 < clusters[1][2]
    observation = ClusterObservation(
        "one",
        "sample",
        "m5C",
        tuple(apex for apex, _, _ in clusters),
        tuple((left, right) for _, left, right in clusters),
    )
    row = check_reference_consistency(
        {"one": [target("m5C", 10.70)]}, [observation]
    )["rows"][0]
    assert row["warnings"] == []


def test_wrong_anchor_does_not_distort_neighbor_mapping() -> None:
    groups = {
        "a": [target(str(i), float(i)) for i in range(1, 8)],
        "b": [target(str(i), 10.0 if i == 4 else float(i)) for i in range(1, 8)],
    }
    report = check_reference_consistency(groups)
    rows = {(r["group"], r["label"]): r for r in report["rows"]}
    assert rows["b", "5"]["predicted_rt_min"] == pytest.approx(5.0)
    assert rows["b", "5"]["residual_min"] == pytest.approx(0.0)
    assert rows["b", "4"]["predicted_rt_min"] == pytest.approx(4.0)
    assert "mapping_residual" in rows["b", "4"]["warnings"]
    excluded = report["summary"]["mapping_excluded_anchors"]
    assert isinstance(excluded, list)
    assert {
        (item["source_group"], item["group"], item["label"])
        for item in excluded
    } >= {("a", "b", "4")}


def test_single_method_checks_clusters_but_never_maps() -> None:
    groups = {"one": [target("gap", 5)]}
    report = check_reference_consistency(
        groups, [ClusterObservation("one", "s", "gap", (4, 6))]
    )
    assert report["mapping_errors"] == []
    assert report["rows"][0]["severity"] == "high"
    assert report["rows"][0]["predicted_rt_min"] is None


def test_single_peak_support_downgrades_mobile_phase_residual() -> None:
    groups = {
        "a": [target("left", 2), target("held", 3), target("right", 5)],
        "b": [target("left", 4), target("held", 9), target("right", 10)],
    }
    report = check_reference_consistency(
        groups, [ClusterObservation("b", "s", "held", (9,))]
    )
    row = next(r for r in report["rows"] if r["group"] == "b" and r["label"] == "held")
    assert row["severity"] == "info"
    assert row["raw_status"] == "single_peak_matches_reference"


def test_unsupported_edges_and_mismatched_identity_are_not_predictions() -> None:
    groups = {
        "a": [target("left", 2), target("right", 5)],
        "b": [target("left", 4), target("right", 10)],
    }
    report = check_reference_consistency(groups)
    assert report["mapping_errors"] == []
    assert report["summary"]["loo_absolute_error_median_min"] is None
    assert all(row["predicted_rt_min"] is None for row in report["rows"])
    groups["a"].append(target("middle", 3))
    groups["b"].append(replace(target("middle", 6), mz=400))
    assert check_reference_consistency(groups)["mapping_errors"] == []


def test_monotonic_mapping_collapses_duplicate_knots_and_ignores_input_order() -> None:
    groups = {
        "a": [
            target("p", 2),
            target("q", 2),
            target("held", 3),
            target("r", 4),
            target("s", 5),
        ],
        "b": [
            target("p", 4),
            target("q", 4),
            target("held", 8),
            target("r", 3),
            target("s", 10),
        ],
    }
    # Keep the robust screen inactive here to isolate knot/PAVA behavior.
    policy = ReferencePolicy(mapping_residual_min=100)
    report = check_reference_consistency(groups, policy=policy)
    held = next(
        error
        for error in report["mapping_errors"]
        if error["group"] == "b" and error["label"] == "held"
    )
    # PAVA pools the inverted 4,3 anchors to 3.5; duplicate x=2 is one knot.
    assert held["predicted_rt_min"] == 3.5
    assert (
        report["mapping_errors"]
        == check_reference_consistency(
            {g: list(reversed(ts)) for g, ts in groups.items()}, policy=policy
        )["mapping_errors"]
    )


def test_sample_count_does_not_overrule_cross_group_ordinal() -> None:
    groups = {g: [target("x", 8 if g == "c" else 2)] for g in "abc"}
    observations = [ClusterObservation(g, g, "x", (2, 8)) for g in "ab"]
    observations += [ClusterObservation("c", str(i), "x", (2, 8)) for i in range(8)]
    report = check_reference_consistency(groups, observations)
    assert all(
        row["severity"] == "high" for row in report["rows"] if row["group"] == "c"
    )
    assert all(
        row["severity"] == "none" for row in report["rows"] if row["group"] != "c"
    )


def test_trace_peaks_preserve_late_ordinal_even_when_early_peak_is_taller() -> None:
    rt = np.linspace(1, 10, 1801)
    intensity = 100 * np.exp(-(((rt - 4) / 0.08) ** 2)) + 90 * np.exp(
        -(((rt - 4.8) / 0.08) ** 2)
    )
    # Low-amplitude distant spikes must not renumber prominent peak clusters.
    intensity += 15 * np.exp(-(((rt - 7) / 0.01) ** 2))
    # A one-scan spike can be as tall as a real peak after smoothing.
    intensity[np.argmin(abs(rt - 8))] = 500
    assert trace_cluster_apices(XICTrace.from_arrays(rt, intensity)) == pytest.approx(
        (4, 4.8), abs=0.01
    )
    assert trace_cluster_apices(XICTrace.from_arrays(rt, np.zeros_like(rt))) == ()


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_invalid_policy_is_rejected(value: float) -> None:
    with pytest.raises(ValueError):
        ReferencePolicy(mapping_residual_min=value)


def test_cli_writes_review_rows_and_distribution_without_mutating_inputs(
    tmp_path: Path,
) -> None:
    from tools.diagnostics.check_reference_consistency import main

    entries = []
    for group, refs in (("a", [2, 3, 5]), ("b", [4, 9, 10])):
        path = tmp_path / f"{group}.csv"
        path.write_text(
            "label,mz,rt_min,rt_max,ppm_tol,neutral_loss_da,nl_ppm_warn,"
            "nl_ppm_max,is_istd,istd_pair\n"
            + "".join(
                f"{label},300.119,{rt - 1},{rt + 1},20,,,,false,\n"
                for label, rt in zip(("left", "held", "right"), refs, strict=True)
            ),
            encoding="utf-8",
        )
        entries.append({"name": group, "targets_csv": path.name})
    manifest = tmp_path / "input.json"
    manifest.write_text(json.dumps({"groups": entries}), encoding="utf-8")
    before = {p: p.read_bytes() for p in tmp_path.iterdir()}
    args = ["--manifest", str(manifest), "--output-dir", str(tmp_path / "review")]
    assert main([*args, "--preflight-only"]) == 0
    assert not (tmp_path / "review").exists()
    assert main(args) == 0
    report = json.loads((tmp_path / "review/reference_consistency.json").read_text())
    assert report["summary"]["loo_absolute_error_median_min"] == 2.25
    with (tmp_path / "review/reference_review.tsv").open(
        newline="", encoding="utf-8"
    ) as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    held = next(r for r in rows if r["group"] == "b" and r["label"] == "held")
    assert float(held["predicted_rt_min"]) == 6
    assert held["severity"] == "info"
    assert {p: p.read_bytes() for p in before} == before


def test_raw_adapter_preserves_sample_identity_and_batches_same_mz(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor.diagnostics import reference_consistency_io as io

    targets = (target("x", 4), target("same_mass", 4))
    target_path = tmp_path / "targets.csv"
    target_path.write_text(
        "input identity for direct typed adapter test", encoding="utf-8"
    )
    raw_paths = (tmp_path / "early.raw", tmp_path / "late.raw")
    for path in raw_paths:
        path.write_bytes(b"external RAW boundary")
    calls = []

    class Reader:
        def __init__(self, path: Path) -> None:
            self.path = path

        def extract_xic_many(self, requests):
            calls.append((self.path, len(requests)))
            rt = np.linspace(1, 10, 1801)
            center = 3 if self.path == raw_paths[0] else 7
            signal = np.exp(-(((rt - center) / 0.08) ** 2))
            return [XICTrace.from_arrays(rt, signal) for _ in requests]

    @contextmanager
    def open_reader(path: Path, dll: Path):
        yield Reader(path)

    monkeypatch.setattr(io, "open_raw", open_reader)
    report = io.run_reference_diagnostic(
        [io.ReferenceGroup("g", target_path, targets, raw_paths)],
        dll_dir=tmp_path,
        output_dir=tmp_path / "review",
    )
    assert calls == [(raw_paths[0], 1), (raw_paths[1], 1)]
    persisted = json.loads((tmp_path / "review/reference_consistency.json").read_text())
    assert persisted["rows"] == report["rows"]
    assert [(r["sample"], r["cluster_apices_min"]) for r in report["rows"]] == [
        (str(raw_paths[0]), [3]),
        (str(raw_paths[1]), [7]),
        (str(raw_paths[0]), [3]),
        (str(raw_paths[1]), [7]),
    ]
    assert report["mapping_errors"] == []


def test_single_method_detects_ordinal_drift_across_samples() -> None:
    report = check_reference_consistency(
        {"g": [target("x", 4)]},
        [
            ClusterObservation("g", "a", "x", (4, 7)),
            ClusterObservation("g", "b", "x", (4, 7)),
            ClusterObservation("g", "c", "x", (1, 4)),
        ],
    )
    row = next(r for r in report["rows"] if r["sample"] == "c")
    assert row["warnings"] == ["cluster_ordinal_mismatch"]
    assert row["severity"] == "high"


def test_unequal_cluster_counts_do_not_claim_ordinal_disagreement() -> None:
    report = check_reference_consistency(
        {g: [target("x", 4)] for g in "ab"},
        [
            ClusterObservation("a", "s", "x", (4, 7)),
            ClusterObservation("b", "s", "x", (1, 4, 7)),
        ],
    )
    assert all(row["warnings"] == [] for row in report["rows"])


def test_incomparable_groups_cannot_make_one_peer_an_ordinal_majority() -> None:
    groups = {g: [target("x", 4)] for g in "abcd"}
    observations = [
        ClusterObservation("a", "a", "x", (4, 7, 60, 65)),
        ClusterObservation("b", "b", "x", (4, 7)),
        ClusterObservation("c", "c", "x", (2, 3, 4, 7)),
        ClusterObservation("d", "d", "x", (4, 7, 70)),
    ]
    rows = check_reference_consistency(groups, observations)["rows"]
    assert all(row["severity"] == "none" for row in rows)


def test_mapping_to_a_different_observed_cluster_is_high_even_with_extra_peaks() -> (
    None
):
    groups = {
        "a": [target("left", 2), target("held", 3), target("right", 5)],
        "b": [target("left", 4), target("held", 9), target("right", 10)],
    }
    report = check_reference_consistency(
        groups,
        [
            ClusterObservation("b", "s", "held", (1, 6, 9, 70)),
        ],
    )
    row = next(r for r in report["rows"] if r["group"] == "b" and r["label"] == "held")
    assert row["severity"] == "high"
    assert "mapping_points_to_different_cluster" in row["warnings"]

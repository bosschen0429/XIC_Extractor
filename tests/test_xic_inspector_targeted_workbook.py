"""Saved Targeted facts remain attributable and read-only through Inspector."""

from __future__ import annotations

import hashlib
import json
import re

import pytest
from openpyxl import Workbook, load_workbook

from tests.workbench_http import workbench_client
from tests.workbench_review_fixtures import review_workspace
from tests.xic_inspector_targeted_workbook_fixtures import (
    install_targeted_raws,
    write_rare_detection_workbook,
)
from xic_inspector.api import (
    InspectorError,
    SelectionRequest,
    prepare_report,
    resolve_source,
)


@pytest.fixture
def workbook(tmp_path):
    path = tmp_path / "targeted.xlsx"
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
            "Group",
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
    results.append(
        [
            "Sample A",
            "A",
            "Analyte",
            "ND",
            "ND",
            "ND",
            "ND",
            "Review",
            "Peak not found",
            "No positive peak",
            "FALSE",
        ]
    )
    results.append(
        ["Sample A", "A", "ISTD", 8.8, 0, 8.7, 8.9, None, None, None, "FALSE"]
    )
    results.append(
        [
            "Sample B",
            None,
            "Analyte",
            9.1,
            123.456,
            9.0,
            9.2,
            "Check",
            "MS2 trigger missing",
            '<script>alert("unsafe")</script>',
            "TRUE",
        ]
    )
    results.append(
        ["Sample B", None, "ISTD", 9.0, None, 8.9, 9.1, None, None, None, "FALSE"]
    )
    metadata = book.create_sheet("Run Metadata")
    metadata.append(["Key", "Value"])
    metadata.append(["targeted_output_schema_version", "targeted_output"])
    metadata.append(["signal_processing_method", "gaussian"])
    book.save(path)
    book.close()
    return path


def _payload(html):
    return json.loads(
        re.search(
            rb'<script id="inspector-data" type="application/json">(.*?)</script>',
            html,
            re.S,
        )[1]
    )


def test_targeted_xic_reuses_raw_identity_while_the_file_is_unchanged(
    tmp_path, monkeypatch
):
    from xic_extractor.acquisition import review_xics

    raw_dir = tmp_path / "raw"
    install_targeted_raws(monkeypatch, raw_dir, {"Sample A": 100, "Sample B": 300})
    identified = []
    real = review_xics.build_acquisition_source_identity
    monkeypatch.setattr(
        review_xics,
        "build_acquisition_source_identity",
        lambda path, **kwargs: identified.append(path.name) or real(path, **kwargs),
    )
    from xic_extractor.configuration.mass_recalibration import MassRecalibration
    from xic_extractor.configuration.signal_processing_profile import (
        DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )

    inputs = dict(
        raw_dir=raw_dir,
        dll_dir=tmp_path,
        sample_ids=("Sample A",),
        coordinates={"Analyte": (258.1085, 5.0)},
        mass_policy=MassRecalibration.NONE,
        profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
        support_reference_rts={},
        verified_raws={},
    )
    for _ in range(2):
        tuple(review_xics.acquire_review_xics(**inputs))
    assert identified == ["Sample A.raw"]
    (raw_dir / "Sample A.raw").write_bytes(b"replaced acquisition")
    tuple(review_xics.acquire_review_xics(**inputs))
    assert identified == ["Sample A.raw", "Sample A.raw"]


@pytest.mark.parametrize(
    ("policy", "nested"),
    [
        (None, False),
        ("none", True),
        ("easy_ic_probe", False),
        ("easy_ic_restore", False),
    ],
)
def test_targeted_http_loads_saved_values_and_diagnostic_pair_without_writes(
    workbook, tmp_path, monkeypatch, policy, nested
):
    import time
    from dataclasses import replace

    from tests.output_metadata_fixtures import _config
    from xic_extractor.configuration.mass_recalibration import MassRecalibration
    from xic_extractor.output.metadata import build_metadata_rows
    from xic_inspector.sources import curve_bundle

    def unexpected_file_handoff(*args, **kwargs):
        raise AssertionError("Interactive curves must not use a bundle file")

    monkeypatch.setattr(curve_bundle, "write_curve_bundle", unexpected_file_handoff)
    monkeypatch.setattr(curve_bundle, "read_curve_bundle", unexpected_file_handoff)
    expected = MassRecalibration(policy or "none")
    book = load_workbook(workbook)
    if policy is not None:
        metadata = dict(
            build_metadata_rows(replace(_config(tmp_path), mass_recalibration=expected))
        )
        book["Run Metadata"].append(
            ["mass_recalibration", metadata["mass_recalibration"]]
        )
    book.save(workbook)
    book.close()
    before = workbook.read_bytes()
    raw_dir = tmp_path / "raw"
    reads = install_targeted_raws(
        monkeypatch,
        raw_dir,
        {"Sample A": 100, "Sample B": 300, "Unselected": 99999},
        pair_scale=0.5,
        expected_mass_policy=expected,
    )
    if nested:
        for sample in ("Sample A", "Sample B"):
            batch = raw_dir / sample / "batch"
            batch.mkdir(parents=True)
            (raw_dir / f"{sample}.raw").rename(batch / f"{sample}.RAW")
    with workbench_client(review_workspace()) as request:
        _, page, _ = request("/api/open", {"source": str(workbook)})
        item = next(item for item in page["items"] if item["title"] == "Analyte")
        status, job, _ = request(
            "/api/curves",
            {
                "item_id": item["id"],
                "sample_ids": ["Sample A", "Sample B"],
                "raw_dir": str(raw_dir),
                "dll_dir": str(tmp_path),
            },
        )
        assert status == 202
        deadline = time.monotonic() + 10
        while job["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.02)
            _, job, _ = request(f"/api/jobs/{job['id']}")
        assert job["status"] == "succeeded", job
        _, html, _ = request(f"/reports/{job['view_id']}.html")
        curves = _payload(html)["items"][0]["curves"]
    assert [(c["sample_id"], c["quality_flags"]["target_label"]) for c in curves] == [
        ("Sample A", "Analyte"),
        ("Sample A", "ISTD"),
        ("Sample B", "Analyte"),
        ("Sample B", "ISTD"),
    ]
    assert [max(c["raw_intensities"]) for c in curves] == pytest.approx(
        [100, 50, 300, 150]
    )
    assert [c["quality_flags"]["saved_area"] for c in curves] == [
        "ND",
        0,
        123.456,
        None,
    ]
    assert curves[0]["bound_left_rt"] is None
    assert (
        curves[2]["bound_left_rt"],
        curves[2]["apex_rt"],
        curves[2]["bound_right_rt"],
    ) == (9.0, 9.1, 9.2)
    assert [
        c["quality_flags"]["support_reference_rt"] for c in (curves[0], curves[2])
    ] == [8.8, 9.0]
    assert all(
        c["quality_flags"]["source_binding_basis"] == "workbook_sample_name_unverified"
        for c in curves
    )
    assert all(not c["quality_flags"]["original_acquisition_verified"] for c in curves)
    assert len(reads) == 4 and all(read[2:4] == (0.0, 12.0) for read in reads)
    assert workbook.read_bytes() == before


def test_targeted_curve_rejects_ambiguous_raw_before_acquisition(tmp_path, monkeypatch):
    from xic_extractor.alignment_explanation.errors import ExplanationError

    raw_dir = tmp_path / "raw"
    reads = install_targeted_raws(
        monkeypatch, raw_dir, {"Sample A": 100, "Sample B": 300}
    )
    batch = raw_dir / "other-batch"
    batch.mkdir()
    (batch / "Sample A.RAW").write_bytes(b"different injection with the same stem")
    from xic_extractor.acquisition.review_xics import acquire_review_xics
    from xic_extractor.configuration.mass_recalibration import MassRecalibration
    from xic_extractor.configuration.signal_processing_profile import (
        DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )

    with pytest.raises(ExplanationError, match="Sample A.*多個 RAW"):
        tuple(
            acquire_review_xics(
                raw_dir=raw_dir,
                dll_dir=tmp_path,
                sample_ids=("Sample B", "Sample A"),
                coordinates={"Analyte": (258.1085, 5.0)},
                mass_policy=MassRecalibration.NONE,
                profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
                support_reference_rts={},
            )
        )
    assert not reads


def test_nd_support_uses_saved_istd_rt_through_http(tmp_path, monkeypatch):
    import time

    workbook = write_rare_detection_workbook(tmp_path / "nd.xlsx")
    book = load_workbook(workbook)
    book["Targets"]["E2"], book["Targets"]["F2"] = 6, 8
    book.save(workbook)
    book.close()
    before = workbook.read_bytes()
    raw_dir = tmp_path / "raw"
    install_targeted_raws(
        monkeypatch, raw_dir, {"S1": 13000, "S2": 1000}, impulse_samples=("S1",)
    )
    with workbench_client(review_workspace()) as request:
        _, page, _ = request("/api/open", {"source": str(workbook)})
        item = next(item for item in page["items"] if item["title"] == "Analyte")
        _, job, _ = request(
            "/api/curves",
            {
                "item_id": item["id"],
                "sample_ids": ["S1", "S2"],
                "raw_dir": str(raw_dir),
                "dll_dir": str(tmp_path),
            },
        )
        deadline = time.monotonic() + 10
        while job["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.02)
            _, job, _ = request(f"/api/jobs/{job['id']}")
        assert job["status"] == "succeeded", job
        _, html, _ = request(f"/reports/{job['view_id']}.html")
        curves = {
            c["sample_id"]: c
            for c in _payload(html)["items"][0]["curves"]
            if c["quality_flags"]["target_label"] == "Analyte"
        }
    assert sum(value != 0 for value in curves["S1"]["raw_intensities"]) == 1
    assert (
        "insufficient_original_xic_support"
        in curves["S1"]["quality_flags"]["reference_support_failures"]
    )
    assert not curves["S2"]["quality_flags"]["reference_support_failures"]
    assert [
        curves[s]["quality_flags"]["support_reference_rt"] for s in ("S1", "S2")
    ] == [9.0, 9.1]
    assert curves["S1"]["quality_flags"]["saved_area"] == "ND"
    assert workbook.read_bytes() == before


@pytest.mark.parametrize("failure", ["missing_raw", "cancelled", "unknown_sample"])
def test_targeted_curve_failure_does_not_publish_or_modify_results(
    workbook, tmp_path, monkeypatch, failure
):
    import threading
    import time
    from contextlib import contextmanager

    from xic_extractor.acquisition import review_xics

    entered, release = threading.Event(), threading.Event()
    closed = []
    if failure == "cancelled":
        install_targeted_raws(monkeypatch, tmp_path, {"Sample A": 100})
        open_raw = review_xics.open_raw

        @contextmanager
        def blocked_raw(*args, **kwargs):
            with open_raw(*args, **kwargs) as raw:
                extract = raw.extract_xic

                def blocked_extract(*args):
                    entered.set()
                    assert release.wait(10), "cancel handoff timed out"
                    return extract(*args)

                raw.extract_xic = blocked_extract
                try:
                    yield raw
                finally:
                    closed.append(True)

        monkeypatch.setattr(review_xics, "open_raw", blocked_raw)
    before = workbook.read_bytes()
    with workbench_client(review_workspace()) as request:
        _, page, _ = request("/api/open", {"source": str(workbook)})
        item = next(item for item in page["items"] if item["title"] == "Analyte")
        status, job, _ = request(
            "/api/curves",
            {
                "item_id": item["id"],
                "sample_ids": [
                    "Unknown" if failure == "unknown_sample" else "Sample A"
                ],
                "raw_dir": str(tmp_path),
                "dll_dir": str(tmp_path),
            },
        )
        if failure == "unknown_sample":
            assert status == 400
        else:
            assert status == 202
            if failure == "cancelled":
                try:
                    assert entered.wait(10)
                    status, job, _ = request(f"/api/jobs/{job['id']}/cancel", {})
                    assert status == 200
                    assert job["status"] == "cancelling"
                finally:
                    release.set()
            deadline = time.monotonic() + 10
            while (
                job["status"] in {"running", "cancelling"}
                and time.monotonic() < deadline
            ):
                time.sleep(0.02)
                _, job, _ = request(f"/api/jobs/{job['id']}")
            assert job["status"] == (
                "cancelled" if failure == "cancelled" else "failed"
            ), job
            assert "view_id" not in job
    if failure == "cancelled":
        assert closed == [True]
    assert workbook.read_bytes() == before


def test_targeted_http_review_preserves_values_scope_and_source(workbook):
    before = hashlib.sha256(workbook.read_bytes()).hexdigest()
    files = set(workbook.parent.iterdir())
    with workbench_client(review_workspace()) as request:
        status, page, _ = request("/api/open", {"source": str(workbook.parent)})
        assert status == 200
        assert page["source_kind"] == "targeted_workbook"
        overview = page["overview"]
        assert (
            overview["result_count"],
            overview["counted_count"],
            overview["review_count"],
        ) == (4, 1, 2)
        assert overview["targets"][0]["median_area"] == 123.456
        assert [item["title"] for item in page["items"]] == ["Analyte", "ISTD"]
        assert all(not item["observed_sample_ids"] for item in page["items"])
        _, detail, _ = request("/api/detail", {"item_id": page["items"][0]["id"]})
        status, html, _ = request(f"/reports/{detail['view_id']}.html?embed=1")
        assert status == 200
        records = _payload(html)["items"][0]["details"]["records"]
        assert [(r["SampleName"], r["Area"], r["Group"]) for r in records] == [
            ("Sample A", "ND", "A"),
            ("Sample B", 123.456, None),
        ]
        assert records[1]["PeakStart"] == 9.0
        assert records[1]["Reason"] == '<script>alert("unsafe")</script>'
        assert b'<script>alert("unsafe")</script>' not in html
        assert ["signal_processing_method", "gaussian"] in _payload(html)["summary"][
            "method_context"
        ]
        _, scoped, _ = request(
            "/api/query",
            {"sample_id": "Sample B", "target_id": "Analyte", "attention_only": True},
        )
        assert (scoped["run_total"], scoped["matched_count"]) == (2, 1)
        assert scoped["overview"] == overview  # The landing summary is always full-run.
        assert [(s["id"], s["label"], s["group"]) for s in scoped["samples"]] == [
            ("Sample B", "Sample B", "")
        ]
        assert len(scoped["all_samples"]) == 2
        _, detail, _ = request("/api/detail", {"item_id": scoped["items"][0]["id"]})
        _, html, _ = request(f"/reports/{detail['view_id']}.html")
        details = _payload(html)["items"][0]["details"]
        assert [r["SampleName"] for r in details["records"]] == ["Sample B"]
        assert details["review_queue"] == [
            {
                "Sample": "Sample B",
                "Target": "Analyte",
                "Status": "Check",
                "Why": "MS2 trigger missing",
                "Evidence": None,
            }
        ]
        _, zero, _ = request("/api/query", {"target_id": "ISTD"})
        _, detail, _ = request("/api/detail", {"item_id": zero["items"][0]["id"]})
        _, html, _ = request(f"/reports/{detail['view_id']}.html")
        assert [
            r["Area"] for r in _payload(html)["items"][0]["details"]["records"]
        ] == [0, None]
        assert (
            request("/api/query", {"attention_only": True, "target_id": "ISTD"})[1][
                "matched_count"
            ]
            == 0
        )
        assert (
            request("/api/query", {"rt_min": 9.05, "rt_max": 9.15})[1]["matched_count"]
            == 1
        )
        assert (
            request("/api/query", {"limit": 1, "offset": 1})[1]["items"][0]["title"]
            == "ISTD"
        )
        assert request("/api/query", {"sample_id": "absent"})[1]["matched_count"] == 0
        invalid = workbook.parent / "invalid.xlsx"
        invalid.write_text("not a workbook")
        assert request("/api/open", {"source": str(invalid)})[0] == 400
        assert request("/api/query", {"target_id": "Analyte"})[1]["matched_count"] == 1
        invalid.unlink()
    assert hashlib.sha256(workbook.read_bytes()).hexdigest() == before
    assert set(workbook.parent.iterdir()) == files


@pytest.mark.parametrize("damage", ["sheet", "header", "schema", "layout", "duplicate"])
def test_malformed_targeted_workbook_is_actionable(workbook, damage):
    book = load_workbook(workbook)
    if damage == "sheet":
        del book["Targets"]
    elif damage == "header":
        book["XIC Results"]["E1"] = "unrecognized area"
    elif damage == "schema":
        book["Run Metadata"]["B2"] = "unsupported"
    elif damage == "layout":
        book["Run Metadata"].append(["targeted_workbook_layout_version", 2])
    else:
        book["XIC Results"]["C3"] = "Analyte"
    book.save(workbook)
    book.close()
    with pytest.raises(InspectorError, match="Targeted XLSX"):
        prepare_report(workbook, SelectionRequest(strategy="sample"))


def test_workbook_binding_and_ambiguous_directory(workbook):
    report = prepare_report(workbook, SelectionRequest(strategy="attention"))
    assert [item["title"] for item in report.payload["items"]] == ["Analyte"]
    # Excel's lock file beside an open workbook is not a second result.
    workbook.with_name(f"~${workbook.name}").write_bytes(b"lock")
    assert resolve_source(workbook.parent) == workbook
    other = workbook.with_name("another.xlsx")
    other.write_bytes(workbook.read_bytes())
    with pytest.raises(ValueError, match="多份 XLSX"):
        resolve_source(workbook.parent)
    book = load_workbook(workbook)
    book["XIC Results"]["E4"] = 999
    book.save(workbook)
    book.close()
    with pytest.raises(InspectorError, match="expected_source"):
        prepare_report(
            workbook, SelectionRequest(strategy="sample"), expected_source=report.source
        )


def test_review_filter_lists_targets_and_keeps_every_sample(workbook):
    book = load_workbook(workbook)
    book["XIC Results"]["H4"] = None
    book.save(workbook)
    book.close()
    report = prepare_report(
        workbook, SelectionRequest(strategy="query", attention_only=True)
    )
    [item] = report.payload["items"]
    # The filter picks the target to read first; its rows are read against
    # the samples they depart from, so none are dropped.
    assert [(r["SampleName"], r["Area"]) for r in item["details"]["records"]] == [
        ("Sample A", "ND"),
        ("Sample B", 123.456),
    ]
    assert item["details"]["focus"]["samples"] == ["Sample A"]
    report = prepare_report(
        workbook, SelectionRequest(strategy="query", attention_only=True, rt_min=9)
    )
    assert report.matched_count == 1


def test_review_filter_opens_the_rare_detection_not_the_shared_absence(tmp_path):
    path = write_rare_detection_workbook(tmp_path / "rare.xlsx")
    report = prepare_report(path, SelectionRequest(strategy="attention"))
    [item] = report.payload["items"]
    details = item["details"]
    assert item["title"] == "Analyte"
    assert [r["SampleName"] for r in details["records"]] == [
        "S1",
        "S2",
        "S3",
        "S4",
        "S5",
    ]
    assert details["focus"] == {
        "kind": "minority_counted",
        "samples": ["S3"],
        "counted": 1,
        "decided": 5,
    }
    # The paired ISTD of the same sample is the anchor for judging that peak.
    assert details["istd_records"]["S3"] == {
        "Target": "ISTD",
        "RT": 9.2,
        "Area": 1002,
        "Counted Detection": "TRUE",
    }


def test_nearest_mz_does_not_extend_targeted_workbook_search(workbook):
    with pytest.raises(InspectorError, match="indexed Untargeted matrix") as exc:
        prepare_report(
            workbook,
            SelectionRequest(strategy="query", review_scope="matrix", nearest_mz=258),
        )
    assert exc.value.code == 2


@pytest.fixture
def role_workbook(workbook):
    book = load_workbook(workbook)
    targets, results = book["Targets"], book["XIC Results"]
    targets.delete_rows(2, targets.max_row)
    results.delete_rows(2, results.max_row)
    for index, (label, role) in enumerate(
        (
            ("ISTD-looking analyte", " Analyte "),
            ("Compound B", "analyte"),
            ("Analyte-looking standard", "ISTD"),
            ("Standard B", " istd "),
            ("Legacy blank", None),
            ("Legacy custom", "QC"),
        )
    ):
        targets.append([label, role, None, 100 + index, 8, 10])
        for sample in ("Sample A", "Sample B"):
            results.append(
                [
                    sample,
                    None,
                    label,
                    8 + index / 10,
                    10 + index,
                    8,
                    9,
                    "Review" if index in (0, 2) and sample == "Sample A" else None,
                    "Inspect" if index in (0, 2) else None,
                    None,
                    "FALSE" if index in (0, 2) and sample == "Sample A" else "TRUE",
                ]
            )
    book.save(workbook)
    book.close()
    return workbook


@pytest.mark.parametrize(
    "role, expected",
    [
        ("Analyte", ["ISTD-looking analyte", "Compound B"]),
        ("ISTD", ["Analyte-looking standard", "Standard B"]),
    ],
)
def test_role_query_uses_target_role_before_pagination_and_keeps_scope(
    role_workbook, role, expected
):
    before = role_workbook.read_bytes()
    baseline = prepare_report(role_workbook, SelectionRequest())
    pages = [
        prepare_report(
            role_workbook,
            SelectionRequest(strategy="query", target_role=role, offset=i, limit=1),
        )
        for i in (0, 1)
    ]
    assert [page.payload["items"][0]["title"] for page in pages] == expected
    for page in pages:
        assert page.run_total == page.eligible_count == 6
        assert page.matched_count == 2 and page.selected_count == 1
        assert page.payload["summary"]["target_names"] == expected
        assert page.payload["selection"]["target_role"] == role
        assert (
            page.payload["summary"]["overview"]
            == baseline.payload["summary"]["overview"]
        )
        assert [
            row["SampleName"] for row in page.payload["items"][0]["details"]["records"]
        ] == ["Sample A", "Sample B"]
    assert role_workbook.read_bytes() == before


def test_role_query_combines_filters_and_retains_role_target_options(role_workbook):
    selected = prepare_report(
        role_workbook,
        SelectionRequest(
            strategy="query",
            target_role="Analyte",
            sample_ids=("Sample A",),
            mz_min=101,
            mz_max=101,
            rt_min=8.05,
            rt_max=8.2,
        ),
    )
    assert [item["title"] for item in selected.payload["items"]] == ["Compound B"]
    assert [
        row["SampleName"] for row in selected.payload["items"][0]["details"]["records"]
    ] == ["Sample A"]
    assert selected.payload["summary"]["target_names"] == [
        "ISTD-looking analyte",
        "Compound B",
    ]
    attention = prepare_report(
        role_workbook,
        SelectionRequest(strategy="query", target_role="ISTD", attention_only=True),
    )
    assert [item["title"] for item in attention.payload["items"]] == [
        "Analyte-looking standard"
    ]
    conflict = prepare_report(
        role_workbook,
        SelectionRequest(
            strategy="query", target_role="ISTD", target_ids=("Compound B",)
        ),
    )
    assert conflict.matched_count == 0 and conflict.payload["items"] == []
    assert conflict.payload["summary"]["target_names"] == [
        "Analyte-looking standard",
        "Standard B",
    ]


def test_workspace_role_query_roundtrips_and_all_restores_legacy_targets(
    role_workbook, tmp_path
):
    before = role_workbook.read_bytes()
    workspace = review_workspace(config_dir=tmp_path / "config")
    try:
        initial = workspace.open(str(role_workbook))
        page = workspace.query({"target_role": "ISTD", "target_id": "Compound B"})
        assert page["target_role"] == "ISTD"
        assert page["target_names"] == ["Analyte-looking standard", "Standard B"]
        assert page["matched_count"] == 0
        restored = workspace.query({"target_role": ""})
        assert restored["target_role"] is None
        assert restored["target_names"] == initial["target_names"]
        assert restored["matched_count"] == 6
        assert restored["overview"] == initial["overview"]
        with pytest.raises(InspectorError, match="target_role"):
            workspace.query({"target_role": "QC"})
        assert workspace.state()["page"] == restored
    finally:
        workspace.close()
    assert role_workbook.read_bytes() == before


def test_group_colors_follow_names_not_order_or_case() -> None:
    from xic_inspector.rendering.group_colors import PALETTE_SIZE, group_colors

    names = [f"Group {index}" for index in range(PALETTE_SIZE)]
    forward = group_colors(names)
    backward = group_colors(reversed(names))

    assert forward == backward
    assert len(set(forward.values())) == PALETTE_SIZE
    # A colour comes from the group's own name: without a collision, adding
    # another group to the batch leaves it unchanged.
    alone = {name: group_colors([name])[name] for name in ("Tumor", "Normal")}
    assert alone["Tumor"] != alone["Normal"]
    assert group_colors(["Normal", "Tumor"]) == alone
    assert (
        group_colors(["Tumor", "TUMOR "])["TUMOR"] == group_colors(["Tumor"])["Tumor"]
    )
    assert group_colors(["qc", "Other", ""]) == {
        "qc": "var(--group-qc)",
        "Other": "var(--group-other)",
        "": "var(--group-other)",
    }


def test_samples_carry_their_recorded_group_colour(workbook):
    from xic_inspector.rendering.group_colors import group_colors

    report = prepare_report(
        workbook, SelectionRequest(strategy="sample", sample_ids=("Sample B",))
    )

    # Colours span the whole workbook, so a one-sample selection keeps them.
    colors = group_colors(["A", ""])
    assert report.payload["samples"] == [
        {
            "id": "Sample B",
            "label": "Sample B",
            "group": "",
            "group_color": colors[""],
        }
    ]

import csv
from dataclasses import replace

import numpy as np
import pytest

from tests.alignment_pipeline_helpers import (
    render,
    written_cells,
    written_groups,
)
from tests.cid_nl_raw_fixture import (
    TEST_PROFILE,
    _current_candidates,
    _FakeRaw,
    _multi_tag_settings,
)
from tests.cid_nl_runtime_fixtures import install_runtime_raws
from tests.untargeted_alignment_entry_fixtures import (
    raw_paths,
    read_tsv,
    sample_metadata_for,
)
from tests.untargeted_partition_fixtures import (
    build_dense_partition,
    complete_dense_partition,
    execute_dense_partition,
    fixture_assessment,
)
from xic_extractor import raw_reader
from xic_extractor.alignment.cid_nl_grouping import CID_NL_GROUPING_RULES
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.edge_scoring import (
    evaluate_owner_edge as _evaluate_owner_edge,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.applications.untargeted.application import run_untargeted_application
from xic_extractor.applications.untargeted.publication import (
    UntargetedPublicationPolicy,
)
from xic_extractor.applications.untargeted.request import UntargetedRequest
from xic_extractor.configuration.execution_profile import (
    ExecutionProfile,
    UntargetedExecutionPolicy,
)
from xic_extractor.configuration.mass_recalibration import MassRecalibration
from xic_extractor.discovery.csv_reader import read_discovery_batch_index
from xic_extractor.discovery.input_source import DiscoveryInputSource
from xic_extractor.discovery.pipeline import run_discovery_batch
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec,
)
from xic_extractor.presets.apply import apply_to_discovery
from xic_extractor.presets.loader import load_preset
from xic_extractor.sample_metadata import SampleMetadata


def evaluate_owner_edge(left, right, *, config, **kwargs):
    return _evaluate_owner_edge(
        left,
        right,
        config=config,
        method_evidence=CID_NL_GROUPING_RULES.pair_evidence(left, right, config),
        **kwargs,
    )


@pytest.mark.parametrize(
    ("mode", "scenario"),
    (
        ("union", "different_tags"),
        ("intersection", "complete"),
        ("intersection", "one_scan"),
        ("intersection", "missing"),
        ("intersection", "disjoint"),
        ("intersection", "pooled"),
    ),
)
def test_configured_composition_runs_discovery_to_owner_matrix(
    tmp_path, monkeypatch, mode, scenario
):
    preset_file = tmp_path / "method.toml"
    preset_file.write_text(
        'name = "two losses"\ndescription = "Composition fixture"\n'
        f'combine_mode = "{mode}"\n'
        '[[tag]]\nstrategy = "neutral_loss"\nname = "A"\nvalue = 116.0474\n'
        '[[tag]]\nstrategy = "neutral_loss"\nname = "B"\nvalue = 132.0423\n'
        "[discovery]\nrt_min = 7.5\nrt_max = 8.6\n"
        'ms1_search_padding_min = 0.2\nresolver_mode = "local_minimum"\n',
        encoding="utf-8",
    )
    settings = apply_to_discovery(load_preset(str(preset_file)))
    paths = tuple(
        tmp_path / f"S{i}.synthetic"
        for i in ((1, 2, 3, 4) if scenario in {"missing", "disjoint"} else (1, 2))
    )
    sources = {}
    candidates = {}
    for index, path in enumerate(paths):
        path.write_bytes(path.stem.encode())
        losses = (
            (116.0474, 132.0423)
            if mode == "intersection"
            else ((116.0474,) if index == 0 else (132.0423,))
        )
        if scenario == "pooled":
            losses = (116.0474,) if index == 0 else (132.0423,)
        if scenario == "missing" and index == 3:
            losses = (116.0474,)
        rts = (7.98, 8.02) if len(losses) == 2 else (8.0,)
        if scenario == "disjoint" and index == 3:
            rts = (7.8, 8.3)
        source = _FakeRaw(rts, neutral_loss_das=losses)
        if scenario == "one_scan":
            source = _FakeRaw((8.0,))
            source.scans = (
                replace(
                    source.scans[0],
                    masses=np.asarray((500.0 - 116.0474, 500.0 - 132.0423)),
                    intensities=np.asarray((800.0, 700.0)),
                ),
            )
        sources[path] = source
        candidates[path.stem] = _current_candidates(
            source,
            raw_file=path,
            settings=settings,
        )
    batch = build_dense_partition(
        raw_paths=paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=p.stem,
                raw_stem=p.stem,
                injection_order=i + 1,
                sample_role="sample",
            )
            for i, p in enumerate(paths)
        ),
        settings=settings,
        peaks_by_sample=candidates,
        dll_dir=tmp_path,
        signal_processing_profile=TEST_PROFILE,
    )
    assert len(batch.subject_batch.subjects) == 1
    install_runtime_raws(monkeypatch, sources_by_path=sources, tmp_path=tmp_path)
    alignment_config = AlignmentConfig(anchor_min_seed_events=1)
    runtime = execute_dense_partition(
        batch,
        alignment_config=alignment_config,
    )
    by_sample = {
        result.job.sample.raw_stem: fixture_assessment(result)
        for result in runtime.sample_results
    }
    if scenario in {"missing", "disjoint"}:
        assert by_sample["S4"].method_result == "HOLD"
        assert "incomplete_composite_requirement" in by_sample["S4"].reason_codes
    if scenario == "one_scan":
        assert all(
            len(assessment.evidence_observations) == 2
            and len({ref.scan_number for ref in assessment.evidence_observations}) == 1
            for assessment in by_sample.values()
        )
    completed = complete_dense_partition(
        batch, runtime, alignment_config=alignment_config
    )
    writer = completed.application_result
    if scenario == "pooled":
        assert all(item.method_result == "HOLD" for item in by_sample.values())
        assert not written_groups(writer)
        _assert_unsupported_exclusion_closes_exact_coverage(completed)
        destination = tmp_path / "empty-matrix.tsv"
        render("matrix", destination, writer)
        with destination.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            assert {"S1", "S2"}.issubset(reader.fieldnames)
            assert list(reader) == []
        return
    rows = written_groups(writer)
    assert sum(row.destination == "matrix" for row in rows) == 1
    cells = tuple(written_cells(writer))
    assert len(cells) == len(paths) * len(rows)
    selected_cells = tuple(
        (sample_stem, cell)
        for row, sample_stem, cell in cells
        if row.destination == "matrix"
    )
    if scenario in {"missing", "disjoint"}:
        assert all(
            cell.value is None for _, sample_stem, cell in cells if sample_stem == "S4"
        )
        assert all(
            cell.value is not None
            for sample_stem, cell in selected_cells
            if sample_stem != "S4"
        )
        s4_observed_apexes = {
            peak.integration.rt_apex_min
            for sample_stem, cell in selected_cells
            if sample_stem == "S4"
            for peak in cell.evidence.ms1_only_group_observations
        }
        if scenario == "missing":
            assert s4_observed_apexes == {8.0}
        else:
            observed_apexes = sorted(
                peak.integration.rt_apex_min
                for _, sample_stem, cell in cells
                if sample_stem == "S4"
                for peak in cell.evidence.ms1_only_group_observations
            )
            assert observed_apexes == pytest.approx([7.8, 8.3])
    else:
        assert all(cell.value is not None for _, cell in selected_cells)
    destination = tmp_path / "matrix.tsv"
    render("matrix", destination, writer)
    with destination.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    assert len(rows) == 1
    assert float(rows[0]["S1"]) > 0
    if scenario in {"missing", "disjoint"}:
        assert rows[0]["S4"] == ""


def _assert_unsupported_exclusion_closes_exact_coverage(completed):

    result = completed.application_result
    assert not result.child_outputs
    exclusions = result.excluded_candidates
    assert exclusions
    assert all(
        item.reason_code == "composite_requirement_not_supported"
        and len(item.composite_assessments) == 2
        for item in exclusions
    )


def _assert_composite_labels_cannot_grant_membership(batch, runtime):
    from xic_extractor.alignment.feature_membership_inference import (
        infer_feature_memberships,
    )
    from xic_extractor.alignment.hypothesis_owner_adapter import (
        compose_cid_nl_peak_hypotheses_from_assessment,
        owner_from_cid_nl_assessment_support,
    )

    owners = []
    for result in runtime.sample_results:
        assessment = fixture_assessment(result)
        (hypothesis,) = compose_cid_nl_peak_hypotheses_from_assessment(
            sample=result.job.sample,
            assessment=assessment,
        )
        owners.append(
            owner_from_cid_nl_assessment_support(
                sample=result.job.sample,
                assessment=assessment,
                hypothesis=hypothesis,
            )
        )
    assert (
        evaluate_owner_edge(
            *owners, config=AlignmentConfig(), rt_frame=SampleRtFrame()
        ).failure_reason
        == ""
    )
    unbound = tuple(
        replace(owner, cid_nl_composite_assessment=None) for owner in owners
    )
    assert unbound[0].neutral_loss_tag == unbound[1].neutral_loss_tag
    assert (
        evaluate_owner_edge(
            *unbound, config=AlignmentConfig(), rt_frame=SampleRtFrame()
        ).failure_reason
        == "product_mz_out_of_tolerance"
    )
    crossed = replace(
        owners[1], cid_nl_composite_assessment=owners[0].cid_nl_composite_assessment
    )
    assert (
        evaluate_owner_edge(
            owners[0], crossed, config=AlignmentConfig(), rt_frame=SampleRtFrame()
        ).failure_reason
        == "product_mz_out_of_tolerance"
    )

    for population, expected_groups in (
        (tuple(owners), 1),
        ((owners[0], crossed), 2),
    ):
        inferred = infer_feature_memberships(
            population,
            config=AlignmentConfig(anchor_min_seed_events=1),
            rt_frame=SampleRtFrame(),
            rules=CID_NL_GROUPING_RULES,
        )
        assert len(inferred.groups) == expected_groups


def _assert_duplicate_loss_evidence_rejected(batch, runtime):
    from xic_extractor.peak_detection.cid_nl_evidence import cid_nl_evidence_observation
    from xic_extractor.peak_detection.cid_nl_method_assessment import (
        CidNlMethodAssessmentContractError,
        mint_cid_nl_method_assessment,
    )

    result = runtime.sample_results[0]
    assessment = fixture_assessment(result)
    measured = assessment.evidence_observations[0]
    duplicate = cid_nl_evidence_observation(
        sample=result.job.sample,
        acquisition_source=result.job.acquisition_source_expectation.source,
        scan_number=measured.scan_number,
        precursor_mz=measured.precursor_mz,
        product_mz=measured.product_mz,
        configured_neutral_loss_da=measured.configured_neutral_loss_da,
        observed_neutral_loss_da=measured.observed_neutral_loss_da,
        observed_loss_error_ppm=measured.observed_loss_error_ppm,
        scan_rt_min=measured.scan_rt_min,
        product_intensity=measured.product_intensity + 1,
    )
    with pytest.raises(
        CidNlMethodAssessmentContractError, match="duplicate acquisition scan"
    ):
        mint_cid_nl_method_assessment(
            sample=result.job.sample,
            acquisition_source=result.job.acquisition_source_expectation.source,
            method_subject=assessment.method_subject,
            detector_invocation_id=assessment.detector_invocation_id,
            evaluation_receipt=assessment.evaluation_receipt,
            candidate_peaks=assessment.candidate_peaks,
            evidence_refs=(*assessment.evidence_observations, duplicate),
            individual_support_refs=assessment.individual_support_refs,
            set_relation_refs=assessment.set_relation_refs,
        )


def test_composite_requirement_identity_distinguishes_each_composition():
    from xic_extractor.peak_detection.cid_nl_method_subject import (
        CidNlComposition,
        mint_untargeted_cid_nl_method_subject,
    )

    first = CidNlComposition(
        tags=(("A", 116.0474), ("B", 132.0423)), combine_mode="union"
    )
    variants = (
        first,
        replace(first, tags=tuple(reversed(first.tags))),
        replace(first, combine_mode="intersection"),
        replace(first, tags=(("A", 116.0474), ("B", 130.0))),
    )
    subjects = tuple(
        mint_untargeted_cid_nl_method_subject(
            precursor_mz=500.0, precursor_tolerance_ppm=5.0, composition=composition
        )
        for composition in variants
    )
    assert len({subject.method_subject_id for subject in subjects}) == len(subjects)
    for subject in subjects:
        assert subject.neutral_loss_da is None


@pytest.mark.parametrize(
    ("local_losses", "role", "excluded", "seed_count"),
    [
        pytest.param((116.0474, 132.0423), "sample", False, 3, id="a-and-b"),
        pytest.param((116.0474,), "sample", False, 3, id="a-only"),
        pytest.param((), "sample", False, 3, id="no-cid"),
        pytest.param((116.0474, 132.0423), "blank", False, 3, id="blank"),
        pytest.param((116.0474, 132.0423), "sample", True, 3, id="excluded"),
        pytest.param((116.0474, 132.0423), "sample", False, 1, id="one-anchor"),
    ],
)
def test_intersection_rechecks_an_observed_peak_outside_the_discovery_window(
    tmp_path, monkeypatch, local_losses, role, excluded, seed_count
):
    stems = (*("S1", "S2", "S3")[:seed_count], "Late")
    paths = raw_paths(tmp_path, stems)
    settings = replace(
        _multi_tag_settings(),
        tag_combine_mode="intersection",
        rt_min=7.5,
        rt_max=8.49,
        mass_recalibration=MassRecalibration.NONE,
    )
    late = _FakeRaw(
        (8.52, 8.56) if len(local_losses) == 2 else (8.54,),
        neutral_loss_das=local_losses or (116.0474,),
    )
    if not local_losses:
        late.scans = ()
    data = {
        paths[stem]: _FakeRaw((8.40, 8.44), neutral_loss_das=(116.0474, 132.0423))
        for stem in stems[:-1]
    }
    data[paths["Late"]] = late
    install_runtime_raws(monkeypatch, sources_by_path=data, tmp_path=tmp_path)
    profile = ExecutionProfile(alignment_workers=1, backend="thread")
    spec = compile_discovery_processing_run_spec(
        tuple(paths.values()),
        discovery_settings=settings,
        signal_processing_profile=TEST_PROFILE,
    )
    sources = {
        item.raw_path: item.acquisition_source for item in spec.acquisition_inputs
    }
    outputs = run_discovery_batch(
        spec,
        output_dir=tmp_path / "discovery",
        dll_dir=tmp_path,
        raw_opener=lambda path, dll: raw_reader.open_raw(
            path, dll, expected_acquisition_source=sources[path]
        ),
        execution=profile,
    )
    batch = read_discovery_batch_index(outputs.batch_index_csv)
    source = DiscoveryInputSource(batch)
    assert all(len(source.feature_candidate_ids(stem)) == 1 for stem in stems[:-1])
    assert source.candidate_ids("Late") == ()
    with batch.event_csvs["Late"].open(encoding="utf-8", newline="") as stream:
        assert not list(csv.DictReader(stream))
    assert local_losses == tuple(
        round(scan.precursor_mz - float(scan.masses[0]), 4) for scan in late.scans
    )
    from xic_extractor.raw_reader import RawFileHandle

    method_windows = []
    acquire_ms2 = RawFileHandle.iter_ms2_scans

    def trace_method_window(raw, rt_min, rt_max):
        method_windows.append((raw.acquisition_source, rt_min, rt_max))
        yield from acquire_ms2(raw, rt_min, rt_max)

    monkeypatch.setattr(RawFileHandle, "iter_ms2_scans", trace_method_window)
    out = tmp_path / "alignment"
    run_untargeted_application(
        UntargetedRequest(
            discovery_batch_index=batch,
            discovery_settings=settings,
            sample_metadata=sample_metadata_for(
                stems,
                Late=SampleMetadata(
                    sample_name="Late",
                    raw_stem="Late",
                    injection_order=seed_count + 1,
                    sample_role=role,
                    excluded=excluded,
                    exclusion_reason="operator exclusion" if excluded else "",
                ),
            ),
            output_dir=out,
            raw_dir=tmp_path / "raw",
            dll_dir=tmp_path,
            execution=UntargetedExecutionPolicy(profile=profile),
            alignment_config=AlignmentConfig(anchor_min_seed_events=1),
            publication=UntargetedPublicationPolicy(emit_alignment_cells=True),
        )
    )

    matrix_rows = read_tsv(out / "alignment_matrix.tsv")
    (cell,) = [
        item
        for item in read_tsv(out / "alignment_cells.tsv")
        if item["sample_stem"] == "Late"
    ]
    assert cell["source_candidate_id"] == ""
    if role == "blank" or excluded:
        (matrix,) = matrix_rows
        assert not method_windows
        assert matrix["Late"] == cell["area"] == ""
        assert cell["status"] == "unresolved"
        return

    if seed_count == 1:
        assert matrix_rows == []
        assert cell["area"] == ""
        assert cell["status"] == "unresolved"
    else:
        (matrix,) = matrix_rows
        assert bool(matrix["Late"]) is (len(local_losses) != 1)
        assert (
            cell["status"]
            == {0: "backfilled", 1: "unresolved", 2: "detected"}[len(local_losses)]
        )
        if matrix["Late"]:
            assert float(cell["apex_rt"]) == pytest.approx(8.54)
    ((local_source, lower_rt, upper_rt),) = method_windows
    assert local_source == sources[paths["Late"]]
    assert lower_rt == pytest.approx(8.46, abs=0.011)
    assert upper_rt == pytest.approx(8.62, abs=0.011)

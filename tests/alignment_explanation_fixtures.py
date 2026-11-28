"""Explanation metadata defaults and opt-in completed Alignment scenarios."""


def explanation_summary(values):
    """Complete persisted metadata for small diagnostic writer fixtures."""
    summary = {
        "processing_attempt_id": "fixture",
        "samples": [],
        "matrix_path": "",
        "matrix_sha256": "",
        "neutral_loss_by_tag": {},
        "sample_rt_calibration": None,
        **values,
    }
    summary["samples"] = [
        {"sample_stem": sample["sample_key"], **sample} for sample in summary["samples"]
    ]
    return summary


def curve_provenance():
    return {
        "profile_version": None,
        "acquisition_source_id": None,
        "processing_attempt_id": None,
        "selected_trace_indices": None,
        "proposal_sources": None,
        "method_subject_id": None,
        "profile": None,
        "physical_state": None,
        "physical_reason": None,
    }


def run_alignment_fixture(tmp_path, monkeypatch, scenario):
    """Run a two-sample synthetic Alignment; metadata-only users load no runtime."""
    from tests.cid_nl_raw_fixture import (
        TEST_PROFILE,
        _current_candidates,
        _FakeRaw,
    )
    from tests.cid_nl_runtime_fixtures import (
        install_runtime_raws,
    )
    from tests.untargeted_partition_fixtures import (
        build_dense_partition,
        complete_dense_partition,
        execute_dense_partition,
    )
    from xic_extractor.alignment.config import AlignmentConfig
    from xic_extractor.output.alignment.tsv_writer import write_alignment_tsvs
    from xic_extractor.presets.apply import apply_to_discovery
    from xic_extractor.presets.loader import load_preset
    from xic_extractor.sample_metadata import SampleMetadata

    preset = tmp_path / "method.toml"
    mode = "intersection" if scenario == "all_hold" else "union"
    preset.write_text(
        'name = "two losses"\ndescription = "Explanation fixture"\n'
        f'combine_mode = "{mode}"\n'
        '[[tag]]\nstrategy = "neutral_loss"\nname = "A"\nvalue = 116.0474\n'
        '[[tag]]\nstrategy = "neutral_loss"\nname = "B"\nvalue = 132.0423\n'
        "[discovery]\nrt_min = 7.5\nrt_max = 8.6\n"
        "ms1_search_padding_min = 0.2\n",
        encoding="utf-8",
    )
    settings = apply_to_discovery(load_preset(str(preset)))
    paths = tuple(tmp_path / f"S{i}.raw" for i in (1, 2))
    sources, candidates = {}, {}
    for path, loss in zip(paths, (116.0474, 132.0423), strict=True):
        path.write_bytes(path.stem.encode())
        center = 8.48 if scenario == "review_only" and path.stem == "S2" else 8.0
        source = _FakeRaw((center,), neutral_loss_das=(loss,))
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
    install_runtime_raws(monkeypatch, sources_by_path=sources, tmp_path=tmp_path)
    config = AlignmentConfig(
        preferred_rt_sec=2.0 if scenario == "review_only" else 60.0,
    )
    runtime = execute_dense_partition(
        batch,
        alignment_config=config,
    )
    completed = complete_dense_partition(batch, runtime, alignment_config=config)
    matrix = tmp_path / "matrix.tsv"
    write_alignment_tsvs({"matrix": matrix}, completed.application_result)
    return completed, candidates, matrix, config


def explanation_discovery_facts(completed) -> dict[str, object]:
    """The Discovery facts a completed Alignment binds for its explanation."""
    inputs = completed.publication_inputs
    return {
        "precursor_mz_by_candidate": inputs.precursor_mz_by_candidate,
        "neutral_loss_by_tag": inputs.neutral_loss_by_tag,
    }


def plan_discovery_facts(plan) -> dict[str, object]:
    """The same Discovery facts, from the plan's persisted Discovery tables."""
    from tests.cid_nl_runtime_fixtures import (
        load_plan_inputs,
        representative_precursors,
    )

    return {
        "precursor_mz_by_candidate": representative_precursors(load_plan_inputs(plan)),
        "neutral_loss_by_tag": plan.context.settings.neutral_loss_by_tag,
    }


def write_alignment_explanation(
    path,
    *,
    completed,
    children,
    peaks_by_sample,
    matrix_path,
    precursor_mz_by_candidate,
    neutral_loss_by_tag,
    ms1_apex_mz_by_candidate=None,
    progress_callback=None,
):
    """Write a finalized batch's report in one call, as publication does per child."""
    from xic_extractor.applications.untargeted.alignment_explanation_projection import (
        prepare_alignment_explanation,
        project_alignment_explanation_child,
    )
    from xic_extractor.output.alignment.tsv_writer import UntargetChildOutputs

    with prepare_alignment_explanation(
        path,
        completed=completed,
        peaks_by_sample=peaks_by_sample,
        precursor_mz_by_candidate=precursor_mz_by_candidate,
        neutral_loss_by_tag=neutral_loss_by_tag,
        ms1_apex_mz_by_candidate=ms1_apex_mz_by_candidate,
        progress_callback=progress_callback,
    ) as explanation:
        first_group = 0
        for child in children:
            explanation.consume_child(
                UntargetChildOutputs(
                    groups=(),
                    explanation=project_alignment_explanation_child(
                        child,
                        sample_rt_frame=completed.sample_rt_frame,
                    ),
                ),
                first_group,
            )
            first_group += len(child.group_cells)
        return explanation.finalize(matrix_path)

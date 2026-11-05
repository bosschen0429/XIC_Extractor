"""Explanation metadata defaults and opt-in completed Alignment scenarios."""


def explanation_summary(values, *, review_only=False):
    """Complete persisted metadata for small diagnostic writer fixtures."""
    summary = {
        "scientific_input_schema_version": "fixture",
        "processing_algorithm_version": "fixture",
        "processing_contract_version": "fixture",
        "processing_attempt_id": "fixture",
        "samples": [],
        "acquisition_sources": [],
        "candidate_count": 0,
        "matrix_row_count": 0,
        "matrix_path": "",
        "matrix_sha256": "",
        "alignment_config": {},
        "reason_counts": {},
        "record_counts": {},
        "retention": "review" if review_only else "full_audit",
        "selection_evidence_capability": "recorded_decisions",
        "curve_retention": "none",
        "curve_mz_ranges": [],
        "initial_edge_coverage": {},
        "audit_availability": {},
        "known_istd_reference": None,
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
        "resolver_mode": None,
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
        _peak_config,
    )
    from tests.cid_nl_runtime_fixtures import (
        build_discovery_receipts,
        install_runtime_raws,
    )
    from tests.untargeted_partition_fixtures import (
        build_dense_partition,
        complete_dense_partition,
        execute_dense_partition,
    )
    from xic_extractor.alignment.config import AlignmentConfig
    from xic_extractor.alignment.untargeted_finalized_matrix import (
        project_completed_untarget_batch_writer_inputs,
    )
    from xic_extractor.output.alignment.tsv_writer import write_alignment_matrix_tsv
    from xic_extractor.presets.apply import apply_to_discovery
    from xic_extractor.presets.loader import load_preset
    from xic_extractor.sample_metadata import SampleMetadata

    physical_records = {}
    preset = tmp_path / "method.toml"
    mode = "intersection" if scenario == "all_hold" else "union"
    preset.write_text(
        'name = "two losses"\ndescription = "Explanation fixture"\n'
        f'combine_mode = "{mode}"\n'
        '[[tag]]\nstrategy = "neutral_loss"\nname = "A"\nvalue = 116.0474\n'
        '[[tag]]\nstrategy = "neutral_loss"\nname = "B"\nvalue = 132.0423\n'
        "[discovery]\nrt_min = 7.5\nrt_max = 8.6\nseed_rt_gap_min = 0.2\n"
        'ms1_search_padding_min = 0.2\nresolver_mode = "local_minimum"\n',
        encoding="utf-8",
    )
    settings = apply_to_discovery(load_preset(str(preset)))
    peak_config = _peak_config(tmp_path)
    paths = tuple(tmp_path / f"S{i}.raw" for i in (1, 2))
    sources, candidates = {}, {}
    for path, loss in zip(paths, (116.0474, 132.0423), strict=True):
        path.write_bytes(path.stem.encode())
        center = 8.12 if scenario == "review_only" and path.stem == "S2" else 8.0
        source = _FakeRaw((center,), neutral_loss_das=(loss,))
        sources[path] = source
        candidates[path.stem] = _current_candidates(
            source,
            raw_file=path,
            settings=settings,
            physical_records=physical_records,
        )
    batch = build_dense_partition(
        raw_paths=paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=p.stem,
                raw_stem=p.stem,
                injection_order=i + 1,
                sample_role="study_sample",
            )
            for i, p in enumerate(paths)
        ),
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates,
        discovery_receipts_by_sample=build_discovery_receipts(
            tmp_path, paths, candidates
        ),
        dll_dir=tmp_path,
        sample_identity_namespace="study:explanation",
        signal_processing_profile=TEST_PROFILE,
        physical_records_by_candidate={
            c.candidate_id: physical_records[c.candidate_id]
            for values in (candidates).values()
            for c in values
        },
    )
    install_runtime_raws(monkeypatch, sources_by_path=sources, tmp_path=tmp_path)
    config = AlignmentConfig(
        anchor_min_seed_events=3 if scenario == "review_only" else 1,
        # Moderate support must also lie outside the preferred RT band;
        # aligned moderate owners are eligible through batch continuity.
        preferred_rt_sec=2.0 if scenario == "review_only" else 60.0,
    )
    runtime = execute_dense_partition(
        batch,
        alignment_config=config,
    )
    completed = complete_dense_partition(batch, runtime, alignment_config=config)
    writer = project_completed_untarget_batch_writer_inputs(
        completed.application_result
    )
    matrix = tmp_path / "matrix.tsv"
    write_alignment_matrix_tsv(
        matrix,
        writer.matrix,
        production_decisions=writer.production_decisions,
    )
    return completed, writer, candidates, matrix, config

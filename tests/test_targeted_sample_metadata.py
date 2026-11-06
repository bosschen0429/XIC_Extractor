import csv
from dataclasses import replace
from pathlib import Path

import openpyxl

from tests.raw_header_fakes import install_fake_raw_headers
from tests.targeted_production_workflow_fixtures import production_case
from xic_extractor.extraction import pipeline, serial_backend
from xic_extractor.extraction.result_models import RawFileExtractionResult
from xic_extractor.sample_metadata import SAMPLE_METADATA_COLUMNS


def write_sample_metadata(path: Path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=SAMPLE_METADATA_COLUMNS, delimiter="\t"
        )
        writer.writeheader()
        writer.writerows({"schema_version": "sample_metadata", **row} for row in rows)


def test_targeted_excluded_raw_is_never_extracted_or_rendered(tmp_path, monkeypatch):
    from xic_extractor.output.excel_pipeline import _run_output_to_long_rows

    install_fake_raw_headers(monkeypatch)
    config, target, fixture_output = production_case(
        tmp_path, sample_names=("Case_A", "CaseA", "Control")
    )
    metadata = tmp_path / "samples.tsv"
    write_sample_metadata(
        metadata,
        [
            {"sample_name": "Case_A", "raw_stem": "Case_A", "group": "Case"},
            {
                "sample_name": "CaseA",
                "raw_stem": "CaseA",
                "group": "Case",
                "excluded": "true",
                "exclusion_reason": "bad injection",
            },
            {"sample_name": "Control", "raw_stem": "Control", "group": "Control"},
        ],
    )
    config = replace(config, injection_order_source=metadata)
    spectra = {f.sample_name: f for f in fixture_output.file_results}
    extracted = []

    def extract(index, _config, _targets, raw_path, **kwargs):
        extracted.append(raw_path.stem)
        return RawFileExtractionResult(index, raw_path.stem, spectra[raw_path.stem], [])

    monkeypatch.setattr(pipeline, "preflight_raw_reader", lambda _dll: [])
    monkeypatch.setattr(serial_backend, "extract_raw_file_result", extract)
    output = pipeline.run_pipeline(config, [target])
    assert extracted == ["Case_A", "Control"]
    assert {f.sample_name for f in output.file_results} == {"Case_A", "Control"}
    assert {
        row["SampleName"] for row in _run_output_to_long_rows(output, [target])
    } == {"Case_A", "Control"}


def test_targeted_publication_keeps_metadata_group_and_sample_attribution(
    tmp_path, monkeypatch
):
    from xic_extractor.applications.targeted.application import (
        build_targeted_application_request,
    )
    from xic_extractor.applications.targeted.output_boundary import (
        publish_targeted_outputs,
    )
    from xic_extractor.applications.targeted.production_workflow import (
        execute_targeted_production_workflow,
    )

    install_fake_raw_headers(monkeypatch)
    config, target, acquired = production_case(
        tmp_path,
        sample_names=("Tumor_A", "QC_1"),
        sample_peak_scales={"Tumor_A": 1.0, "QC_1": 2.0},
    )
    metadata = tmp_path / "samples.tsv"
    write_sample_metadata(
        metadata,
        [
            {"sample_name": "Tumor_A", "raw_stem": "Tumor_A", "group": "Control"},
            {"sample_name": "QC_1", "raw_stem": "QC_1", "group": " Case  A "},
        ],
    )
    config = replace(config, injection_order_source=metadata)
    request = build_targeted_application_request(
        config, [target], excel_output_path=tmp_path / "results.xlsx"
    )
    completed = execute_targeted_production_workflow(request, acquired)
    publication = publish_targeted_outputs(request, completed, acquired)
    assert [row.sample_metadata.group for row in publication.output.file_results] == [
        "Control",
        "Case A",
    ]
    workbook = openpyxl.load_workbook(
        publication.paths[0], read_only=True, data_only=True
    )
    try:
        values = list(workbook["XIC Results"].values)
    finally:
        workbook.close()
    rows = [dict(zip(values[0], row, strict=True)) for row in values[1:]]
    by_sample = {row["SampleName"]: row for row in rows}
    assert {name: row["Group"] for name, row in by_sample.items()} == {
        "Tumor_A": "Control",
        "QC_1": "Case A",
    }
    assert by_sample["QC_1"]["Area"] > by_sample["Tumor_A"]["Area"] > 0


def test_cli_flows_share_tsv_groups_and_excluded_population(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import numpy as np

    from scripts import run_alignment, run_extraction
    from scripts.run_discovery import _raw_paths_from_dir
    from tests.cid_nl_raw_fixture import _FakeRaw
    from tests.cid_nl_runtime_fixtures import install_runtime_raws
    from tests.discovery_pipeline_fixtures import (
        _OwnerBoundFakeRawHandle,
        _peak_config,
        _scan_event,
        _settings,
    )
    from xic_extractor.alignment import AlignmentConfig
    from xic_extractor.configuration.loader import PreparedTargetedConfig
    from xic_extractor.discovery.pipeline import run_discovery_batch
    from xic_extractor.discovery.processing_run_spec_adapter import (
        compile_discovery_processing_run_spec,
    )

    install_fake_raw_headers(monkeypatch)
    config, target, acquired = production_case(
        tmp_path,
        sample_names=("Case_A", "CaseA", "Control"),
        sample_peak_scales={"Case_A": 1.0, "Control": 2.0},
    )
    metadata = tmp_path / "samples.tsv"
    write_sample_metadata(
        metadata,
        [
            {"sample_name": "Case_A", "raw_stem": "Case_A", "group": "Case"},
            {
                "sample_name": "CaseA",
                "raw_stem": "CaseA",
                "group": "Case",
                "excluded": "true",
                "exclusion_reason": "bad injection",
            },
            {"sample_name": "Control", "raw_stem": "Control", "group": "Control"},
        ],
    )

    # Replace machine configuration and vendor acquisition, not membership,
    # finalization, CLI input binding, or either publication path.
    def prepare(config_dir, *, settings_overrides, **kwargs):
        return PreparedTargetedConfig(
            replace(
                config,
                injection_order_source=Path(
                    settings_overrides["injection_order_source"]
                ),
            ),
            (target,),
            config_dir,
        )

    spectra = {f.sample_name: f for f in acquired.file_results}
    extracted = []

    def extract(index, _config, _targets, raw_path, **kwargs):
        extracted.append(raw_path.stem)
        return RawFileExtractionResult(index, raw_path.stem, spectra[raw_path.stem], [])

    monkeypatch.setattr(run_extraction, "prepare_targeted_config", prepare)
    monkeypatch.setattr(pipeline, "preflight_raw_reader", lambda _dll: [])
    monkeypatch.setattr(serial_backend, "extract_raw_file_result", extract)
    assert (
        run_extraction.main(
            ["--base-dir", str(tmp_path), "--injection-order-source", str(metadata)]
        )
        == 0
    )
    workbook_path = next(config.output_csv.parent.glob("xic_results_*.xlsx"))
    workbook = openpyxl.load_workbook(workbook_path, read_only=True, data_only=True)
    try:
        values = list(workbook["XIC Results"].values)
    finally:
        workbook.close()
    targeted_rows = [dict(zip(values[0], row, strict=True)) for row in values[1:]]
    assert {row["SampleName"]: row["Group"] for row in targeted_rows} == {
        "Case_A": "Case",
        "Control": "Control",
    }
    assert extracted == ["Case_A", "Control"]

    # The complete Untargeted flow selects Discovery inputs through its CLI's
    # AnalysisSet entry, then passes the same TSV to the Alignment CLI.
    raw_paths = tuple(_raw_paths_from_dir(config.data_dir, tmp_path, metadata))
    assert {path.stem for path in raw_paths} == {"Case_A", "Control"}
    settings = _settings()
    spec = compile_discovery_processing_run_spec(raw_paths, discovery_settings=settings)
    raw_spectra = {path: _FakeRaw((7.8,)) for path in raw_paths}
    for index, raw in enumerate(raw_spectra.values()):
        raw.rt = np.linspace(7.2, 8.4, 25)
        raw.intensity = 100.0 + (index + 1) * 500.0 * np.exp(
            -0.5 * ((raw.rt - 7.8) / 0.08) ** 2
        )
        raw.scans = tuple(replace(scan, scan_number=13) for scan in raw.scans)
    handles = {
        path: _OwnerBoundFakeRawHandle(
            spec.acquisition_inputs[index].acquisition_source,
            [
                _scan_event(
                    scan_number=13,
                    rt=7.8,
                    precursor_mz=500.0,
                    masses=[383.9526],
                    intensities=[10_000.0],
                )
            ],
            rt=raw_spectra[path].rt,
            intensity=raw_spectra[path].intensity,
        )
        for index, path in enumerate(raw_paths)
    }
    discovery = run_discovery_batch(
        raw_paths,
        output_dir=tmp_path / "discovery",
        settings=settings,
        dll_dir=tmp_path,
        raw_opener=lambda path, _dll: handles[path],
        run_spec=spec,
    )
    opened = install_runtime_raws(
        monkeypatch, sources_by_path=raw_spectra, tmp_path=tmp_path
    )
    monkeypatch.setattr(
        run_alignment,
        "_resolve_preset",
        lambda _args: (
            SimpleNamespace(name="DNA dR"),
            SimpleNamespace(
                discovery_settings=settings,
                alignment_config=AlignmentConfig(),
            ),
        ),
    )
    monkeypatch.setattr(
        run_alignment, "_peak_config", lambda *_a, **_k: _peak_config(tmp_path)
    )
    destination = tmp_path / "alignment"
    assert (
        run_alignment.main(
            [
                "--discovery-batch-index",
                str(discovery.batch_index_csv),
                "--raw-dir",
                str(config.data_dir),
                "--dll-dir",
                str(tmp_path),
                "--output-dir",
                str(destination),
                "--sample-metadata",
                str(metadata),
                "--preset",
                "dna_dr",
                "--allow-uncorrected",
                "--output-level",
                "machine",
                "--raw-workers",
                "1",
                "--raw-worker-backend",
                "thread",
            ]
        )
        == 0
    )
    with (destination / "alignment_matrix.tsv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        matrix = csv.DictReader(handle, delimiter="\t")
        assert set(matrix.fieldnames[2:]) == {
            row["SampleName"] for row in targeted_rows
        }
        assert list(matrix)
    assert {path.stem for path in opened} == {"Case_A", "Control"}

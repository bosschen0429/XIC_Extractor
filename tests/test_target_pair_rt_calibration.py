import csv
from pathlib import Path
from typing import Any

import pytest

from xic_extractor.configuration.hashing import compute_target_config_hash
from xic_extractor.configuration.models import ConfigError, Target
from xic_extractor.extraction.rt_prior_library import (
    LibraryEntry,
    calibration_rows_from_rt_prior_library,
    rt_prior_library_from_target_pair_calibration,
)
from xic_extractor.target_pair_rt_calibration import (
    TARGET_PAIR_RT_CALIBRATION_FIELDS,
    TARGET_PAIR_RT_CALIBRATION_SCHEMA_VERSION,
    TargetPairRTCalibrationRow,
    load_target_pair_rt_calibration,
    write_target_pair_rt_calibration_tsv,
)


def test_compute_target_config_hash_changes_with_target_metadata_only(
    tmp_path: Path,
) -> None:
    targets = tmp_path / "targets.csv"
    settings = tmp_path / "settings.csv"
    targets.write_text(
        "label,mz,rt_min,rt_max,ppm_tol,neutral_loss_da,nl_ppm_warn,"
        "nl_ppm_max,is_istd,istd_pair,isotope_label_type,"
        "paired_rt_relation\n"
        "A,100,1,2,20,,,,false,ISTD,,istd_not_later_than_pair\n"
        "ISTD,103,1,2,20,,,,true,,deuterated,\n",
        encoding="utf-8-sig",
    )
    settings.write_text(
        "key,value,description\n"
        "target_pair_rt_calibration_path,C:/calibration/a.tsv,path\n",
        encoding="utf-8-sig",
    )

    first = compute_target_config_hash(targets)
    settings.write_text(
        "key,value,description\n"
        "target_pair_rt_calibration_path,C:/calibration/b.tsv,path\n",
        encoding="utf-8-sig",
    )
    second = compute_target_config_hash(targets)
    targets.write_text(
        targets.read_text(encoding="utf-8-sig").replace(
            "deuterated", "heavy_non_deuterium"
        ),
        encoding="utf-8-sig",
    )

    assert second == first
    assert compute_target_config_hash(targets) != first


def test_calibration_loader_validates_schema_and_blocks_hash_mismatch(
    tmp_path: Path,
) -> None:
    path = tmp_path / "target_pair_rt_calibration.tsv"
    write_target_pair_rt_calibration_tsv(
        path,
        [
            _row(
                target_config_hash="oldhash",
                source_hash_status="mismatch",
                source_hash="abc123",
            )
        ],
    )

    rows = load_target_pair_rt_calibration(
        path,
        expected_target_config_hash="newhash",
    )

    assert rows[0].target_hash_status == "mismatch"
    assert "target_config_hash_mismatch" in rows[0].activation_block_reason
    assert "source_hash_mismatch" in rows[0].activation_block_reason


def test_calibration_loader_rejects_missing_required_column(
    tmp_path: Path,
) -> None:
    path = tmp_path / "target_pair_rt_calibration.tsv"
    path.write_text("schema_version\ttarget_label\nv\tA\n", encoding="utf-8-sig")

    with pytest.raises(ConfigError) as exc_info:
        load_target_pair_rt_calibration(path)

    assert "missing required column target_config_hash" in str(exc_info.value)


def test_calibration_loader_rejects_duplicate_target_pair(
    tmp_path: Path,
) -> None:
    path = tmp_path / "target_pair_rt_calibration.tsv"
    write_target_pair_rt_calibration_tsv(path, [_row(), _row()])

    with pytest.raises(ConfigError) as exc_info:
        load_target_pair_rt_calibration(path)

    assert "duplicate (target_label, paired_istd_label)" in str(exc_info.value)


def test_calibration_loader_allows_missing_source_hash_as_blocked_row(
    tmp_path: Path,
) -> None:
    path = tmp_path / "target_pair_rt_calibration.tsv"
    write_target_pair_rt_calibration_tsv(
        path,
        [_row(source_hash="", source_hash_status="missing")],
    )

    rows = load_target_pair_rt_calibration(path)

    assert rows[0].source_hash_status == "missing"
    assert rows[0].activation_block_reason.startswith("missing_source_hash")


def test_rt_prior_library_adapter_writes_calibration_schema(
    tmp_path: Path,
) -> None:
    entries = {
        ("Analyte", "analyte"): LibraryEntry(
            config_hash="runhash",
            target_label="Analyte",
            role="analyte",
            istd_pair="ISTD",
            median_delta_rt=0.25,
            sigma_delta_rt=0.03,
            median_abs_rt=None,
            sigma_abs_rt=None,
            n_samples=8,
            updated_at="2026-06-03",
        )
    }
    rows = calibration_rows_from_rt_prior_library(
        entries,
        targets=(
            _target("Analyte", is_istd=False, istd_pair="ISTD"),
            _target("ISTD", is_istd=True, isotope_label_type="deuterated"),
        ),
        target_config_hash="targethash",
        source_artifact="rt_prior_library.csv",
        source_hash="sourcehash",
    )
    path = tmp_path / "target_pair_rt_calibration.tsv"

    write_target_pair_rt_calibration_tsv(path, rows)

    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        assert reader.fieldnames == list(TARGET_PAIR_RT_CALIBRATION_FIELDS)
        read_back = list(reader)
    assert read_back[0]["schema_version"] == TARGET_PAIR_RT_CALIBRATION_SCHEMA_VERSION
    assert read_back[0]["target_label"] == "Analyte"
    assert read_back[0]["paired_istd_label"] == "ISTD"
    assert read_back[0]["pair_rt_delta_min"] == "0.250000"
    assert read_back[0]["rt_delta_direction"] == "target_later"


def test_write_target_pair_rt_calibration_tsv_preserves_utf8_sig_header(
    tmp_path: Path,
) -> None:
    path = tmp_path / "nested" / "target_pair_rt_calibration.tsv"

    write_target_pair_rt_calibration_tsv(path, [])

    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert path.read_text(encoding="utf-8-sig").splitlines() == [
        "\t".join(TARGET_PAIR_RT_CALIBRATION_FIELDS)
    ]


def test_rt_prior_library_from_calibration_uses_only_activated_rows() -> None:
    library = rt_prior_library_from_target_pair_calibration(
        (
            _row(
                target_label="Active",
                product_transfer_status="row_approved",
            ),
            _row(
                target_label="Blocked",
                product_transfer_status="not_assessed",
                activation_block_reason="product_transfer_status:not_assessed",
            ),
        )
    )

    assert set(library) == {("Active", "analyte")}
    active = library[("Active", "analyte")]
    assert active.istd_pair == "ISTD"
    assert active.median_delta_rt == pytest.approx(0.25)
    assert active.sigma_delta_rt == pytest.approx(0.02)


def _row(**overrides: Any) -> TargetPairRTCalibrationRow:
    values: dict[str, Any] = {
        "schema_version": TARGET_PAIR_RT_CALIBRATION_SCHEMA_VERSION,
        "target_config_hash": "targethash",
        "source_artifact": "mixstds.tsv",
        "source_hash": "sourcehash",
        "source_hash_status": "present",
        "target_label": "Analyte",
        "paired_istd_label": "ISTD",
        "pair_rt_delta_min": 0.25,
        "delta_source": "mixstds_clean_standard",
        "point_count": 6,
        "rt_delta_median_min": 0.25,
        "rt_delta_mad_min": 0.02,
        "rt_delta_direction": "target_later",
        "isotope_label_type": "deuterated",
        "paired_rt_relation": "istd_not_later_than_pair",
        "calibration_status": "usable",
        "calibration_level": "clean_standard_only",
        "product_transfer_status": "not_assessed",
    }
    values.update(overrides)
    return TargetPairRTCalibrationRow(**values)


def _target(
    label: str,
    *,
    is_istd: bool,
    istd_pair: str = "",
    isotope_label_type: str = "unknown",
) -> Target:
    return Target(
        label=label,
        mz=100.0,
        rt_min=1.0,
        rt_max=2.0,
        ppm_tol=20.0,
        neutral_loss_da=None,
        nl_ppm_warn=None,
        nl_ppm_max=None,
        is_istd=is_istd,
        istd_pair=istd_pair,
        isotope_label_type=isotope_label_type,
        paired_rt_relation=(
            "istd_not_later_than_pair" if istd_pair else "none"
        ),
    )

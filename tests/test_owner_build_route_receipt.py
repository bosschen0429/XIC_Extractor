from pathlib import Path

import pytest

import xic_extractor.alignment.pipeline as pipeline_module
from tests.alignment_pipeline_helpers import peak_config as _peak_config
from tests.test_targeted_real_data_adapter import _settings
from xic_extractor.alignment import AlignmentConfig
from xic_extractor.alignment.owner_build_route_receipt import (
    OwnerBuildRouteReceipt,
    _mint_owner_build_route_receipt,
    _mint_retained_untargeted_cid_nl_route_receipt,
)


def test_receipt_requires_alignment_owner_mint() -> None:
    with pytest.raises(ValueError, match="must be minted by the Alignment owner"):
        OwnerBuildRouteReceipt(
            configured_owner_build_xic_backend="raw",
            effective_owner_build_route="configured_backend",
            effective_owner_build_xic_backend="raw",
            processing_run_spec_id=None,
            processing_run_spec_hash=None,
            _mint_token=object(),
        )


def test_receipt_rejects_incomplete_run_spec_identity() -> None:
    with pytest.raises(ValueError, match="id and hash must be provided together"):
        _mint_owner_build_route_receipt(
            configured_backend="raw",
            processing_run_spec_id="run-spec-id",
        )


def test_retained_cid_nl_receipt_separates_configured_legacy_backend() -> None:
    receipt = _mint_retained_untargeted_cid_nl_route_receipt(
        configured_backend="raw_superwindow"
    )

    assert receipt.as_dict() == {
        "configured_owner_build_xic_backend": "raw_superwindow",
        "effective_owner_build_route": "retained_untargeted_cid_nl_batch",
        "effective_owner_build_xic_backend": None,
        "effective_route_validation": "owner_validated",
        "processing_run_spec_id": None,
        "processing_run_spec_hash": None,
    }


def test_pipeline_rejects_invalid_backend_before_input_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_io(*_args, **_kwargs):
        raise AssertionError("invalid backend reached Alignment input I/O")

    monkeypatch.setattr(pipeline_module, "read_discovery_batch_index", fail_io)

    with pytest.raises(ValueError, match="unsupported owner build XIC backend"):
        pipeline_module.run_alignment(
            discovery_batch_index=tmp_path / "discovery_batch_index.csv",
            raw_dir=tmp_path / "raw",
            dll_dir=tmp_path / "dll",
            output_dir=tmp_path / "out",
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            owner_build_xic_backend="invalid",  # type: ignore[arg-type]
            discovery_settings=_settings(),
        )

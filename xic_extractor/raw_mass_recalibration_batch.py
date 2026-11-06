"""Prepare source-bound mass results once per RAW through the native adapter."""

from collections.abc import Callable, Sequence
from functools import partial
from pathlib import Path

from xic_extractor.configuration.mass_recalibration import MassRecalibration
from xic_extractor.processing_identity import (
    AcquisitionSourceExpectation,
    AcquisitionSourceIdentity,
)
from xic_extractor.raw_mass_recalibration import PreparedRawMassRecalibration
from xic_extractor.raw_reader import open_raw
from xic_extractor.raw_source_binding import _verify_expected_acquisition_source


def prepare_batch_mass_recalibration(
    inputs: Sequence[tuple[Path, AcquisitionSourceIdentity]],
    dll_dir: Path,
    policy: MassRecalibration,
    *,
    workers: int = 1,
    backend: str = "thread",
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, PreparedRawMassRecalibration]:
    """Prepare once per sample, close every handle, and share only small results."""
    if policy is MassRecalibration.NONE:
        return {}

    from xic_extractor.execution.workers import WorkerAdmissionPool

    results: dict[str, PreparedRawMassRecalibration] = {}

    def collect(_completed, _total, _item, result):
        stem, prepared = result
        results[stem] = prepared

    with WorkerAdmissionPool(max_workers=workers, backend=backend) as pool:
        pool.run_windowed(
            inputs,
            partial(_prepare_raw_mass_recalibration, dll_dir=dll_dir, policy=policy),
            on_completed=collect,
            should_cancel=should_cancel,
        )
    if pool.first_error is not None:
        raise pool.first_error
    return results


def _prepare_raw_mass_recalibration(
    item: tuple[Path, AcquisitionSourceIdentity],
    *,
    dll_dir: Path,
    policy: MassRecalibration,
) -> tuple[str, PreparedRawMassRecalibration]:
    path, source = item
    binding = _verify_expected_acquisition_source(path, source)
    assert binding is not None
    with open_raw(
        path,
        dll_dir,
        mass_recalibration=policy,
        expected_acquisition_source=source,
        _expected_path_identity=binding.path_identity,
    ) as raw:
        return path.stem, PreparedRawMassRecalibration(
            AcquisitionSourceExpectation(source, binding.path_identity),
            raw.mass_recalibration_result,
        )

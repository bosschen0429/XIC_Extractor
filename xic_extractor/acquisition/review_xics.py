"""On-demand RAW XIC acquisition and physical-signal diagnostics.

No saved-result parsing or report models enter this boundary. The caller owns
selection and presentation; this adapter owns RAW lifetime and recalibration.
"""

from __future__ import annotations

from collections.abc import Callable, Generator, Mapping
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.alignment_explanation.errors import ExplanationError
from xic_extractor.analysis_set import discover_raw_files
from xic_extractor.configuration.mass_recalibration import (
    MassRecalibration,
)
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    enumerate_physical_segment_universe,
    physical_peak_support_failures_at_reference,
    prepare_physical_signal,
)
from xic_extractor.peak_detection.physical_peak_models import PreparedSignal
from xic_extractor.processing_identity import (
    AcquisitionSourceExpectation,
    FingerprintCancelledError,
    build_acquisition_source_identity,
)
from xic_extractor.raw_mass_recalibration import PreparedRawMassRecalibration
from xic_extractor.raw_reader import open_raw
from xic_extractor.raw_source_binding import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
    stat_identity,
)
from xic_extractor.xic_models import XICTrace


class ReviewAcquisitionCancelled(Exception):
    """The caller cancelled on-demand XIC acquisition."""


@dataclass(frozen=True, slots=True)
class AcquiredReviewXIC:
    """One acquired observation, retaining the existing trace and signal models."""

    sample: str
    target: str
    mz: float
    ppm: float
    acquisition_source_id: str
    rt_window: tuple[float, float]
    trace: XICTrace
    prepared: PreparedSignal
    support_failures: tuple[str, ...]


def acquire_review_xics(
    *,
    raw_dir: Path,
    dll_dir: Path,
    sample_ids: tuple[str, ...],
    coordinates: Mapping[str, tuple[float, float]],
    mass_policy: MassRecalibration,
    profile: SignalProcessingProfile,
    support_reference_rts: Mapping[tuple[str, str], float],
    is_cancelled: Callable[[], bool] | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
    verified_raws: dict[Path, PreparedRawMassRecalibration] | None = None,
) -> Generator[AcquiredReviewXIC, None, None]:
    """Read full traces before display cropping; never select or integrate peaks.

    Identity/calibration reuse is scoped to the current RAW file state and policy.
    Support checks use only the supplied diagnostic reference RT, with no formal
    Targeted reference-RT adjustment or reinterpretation of saved ND decisions.
    """

    def check_cancel() -> None:
        if is_cancelled is not None and is_cancelled():
            raise ReviewAcquisitionCancelled("已取消曲線載入。")

    check_cancel()
    paths_by_stem: dict[str, list[Path]] = {}
    for sample in sample_ids:
        if Path(sample).name != sample or sample in {".", ".."}:
            raise ExplanationError("工作簿樣本名稱不可含路徑。")
        paths_by_stem[sample.casefold()] = []
    try:
        raw_paths = discover_raw_files(raw_dir)
    except ValueError as exc:
        raise ExplanationError(str(exc)) from exc
    for path in raw_paths:
        if path.stem.casefold() in paths_by_stem:
            paths_by_stem[path.stem.casefold()].append(path)
    selected_paths = {}
    for sample in sample_ids:
        matches = paths_by_stem[sample.casefold()]
        if not matches:
            raise ExplanationError(
                f"找不到所選樣本的 RAW：{sample}（搜尋根目錄：{raw_dir}）"
            )
        if len(matches) > 1:
            raise ExplanationError(
                f"樣本 {sample} 對應多個 RAW，請選擇唯一對應的資料夾："
                + "、".join(str(path) for path in matches)
            )
        selected_paths[sample] = matches[0]

    for index, sample in enumerate(sample_ids):
        check_cancel()
        path = selected_paths[sample]
        known = (verified_raws or {}).get(path)
        state = stat_identity(path.stat())
        if (
            known is None
            or known.acquisition.path_identity != state
            or known.result.policy is not mass_policy
        ):
            known = None
            try:
                identity = build_acquisition_source_identity(
                    path,
                    acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
                    observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
                    should_cancel=is_cancelled,
                )
            except FingerprintCancelledError as exc:
                raise ReviewAcquisitionCancelled("已取消曲線載入。") from exc
            if stat_identity(path.stat()) != state:
                raise ExplanationError(f"{path.name} 在讀取時被修改，請稍後再試。")
        else:
            identity = known.acquisition.source
        with open_raw(
            path,
            dll_dir,
            expected_acquisition_source=identity,
            _expected_path_identity=state,
            mass_recalibration=mass_policy,
            prepared_mass_recalibration=known,
        ) as raw:
            if verified_raws is not None:
                verified_raws[path] = PreparedRawMassRecalibration(
                    AcquisitionSourceExpectation(identity, state),
                    raw.mass_recalibration_result,
                )
            lower, upper = raw.acquisition_rt_bounds()
            for label, (mz, tolerance) in coordinates.items():
                check_cancel()
                rt, intensity = raw.extract_xic(mz, lower, upper, tolerance)
                prepared_result = prepare_physical_signal(
                    rt, intensity, profile=profile
                )
                prepared = prepared_result.prepared
                if prepared is None:
                    raise ExplanationError(
                        f"{sample} / {label} 沒有可繪製的 XIC 資料。"
                    )
                reference_rt = support_reference_rts.get((sample, label))
                support_failures: tuple[str, ...] = ()
                if reference_rt is not None:
                    support_failures = physical_peak_support_failures_at_reference(
                        prepared,
                        enumerate_physical_segment_universe(prepared_result),
                        reference_rt,
                    )
                yield AcquiredReviewXIC(
                    sample=sample,
                    target=label,
                    mz=mz,
                    ppm=tolerance,
                    acquisition_source_id=identity.acquisition_source_id,
                    rt_window=(lower, upper),
                    trace=XICTrace.from_arrays(rt, intensity),
                    prepared=prepared,
                    support_failures=support_failures,
                )
        if on_progress is not None:
            on_progress(index + 1, len(sample_ids), sample)
    check_cancel()

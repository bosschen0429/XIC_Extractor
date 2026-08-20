from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from xic_extractor.alignment.csv_io import (
    read_discovery_batch_index,
    read_discovery_candidates_csv,
)
from xic_extractor.raw_reader import open_raw
from xic_extractor.xic_models import XICRequest, XICTrace


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate Thermo RAW XIC single-vs-batch equivalence."
    )
    parser.add_argument("--discovery-batch-index", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--dll-dir", type=Path, required=True)
    parser.add_argument("--sample-count", type=int, default=1)
    parser.add_argument("--request-count", type=int, default=6)
    parser.add_argument("--rt-window-min", type=float, default=0.5)
    parser.add_argument("--ppm", type=float, default=20.0)
    args = parser.parse_args(argv)

    batch = read_discovery_batch_index(args.discovery_batch_index)
    checked_samples = 0
    checked_requests = 0
    raw_call_total = 0
    for sample_stem in batch.sample_order:
        if checked_samples >= args.sample_count:
            break
        raw_path = _raw_path(args.raw_dir, batch.raw_files[sample_stem], sample_stem)
        if not raw_path.exists():
            continue
        candidates = read_discovery_candidates_csv(batch.candidate_csvs[sample_stem])
        requests = _candidate_requests(
            candidates,
            request_count=args.request_count,
            rt_window_min=args.rt_window_min,
            ppm=args.ppm,
        )
        if len(requests) < 2:
            continue
        with open_raw(raw_path, args.dll_dir) as raw:
            _assert_single_request_equivalence(raw, requests)
            _assert_same_window_batch_equivalence(raw, requests)
            raw_call_total += raw.raw_chromatogram_call_count
        checked_samples += 1
        checked_requests += len(requests)

    if checked_samples == 0:
        raise SystemExit("No RAW samples with enough discovery candidates were checked")
    print(
        "PASS raw_xic_batch_equivalence "
        f"samples={checked_samples} requests={checked_requests} "
        f"raw_chromatogram_call_count={raw_call_total}"
    )
    return 0


def _raw_path(raw_dir: Path, raw_file: Path | None, sample_stem: str) -> Path:
    if raw_file is not None and str(raw_file):
        return raw_dir / raw_file.name
    return raw_dir / f"{sample_stem}.raw"


def _candidate_requests(
    candidates,
    *,
    request_count: int,
    rt_window_min: float,
    ppm: float,
) -> tuple[XICRequest, ...]:
    requests: list[XICRequest] = []
    for candidate in candidates:
        rt = (
            candidate.ms1_apex_rt
            if candidate.ms1_apex_rt is not None
            else candidate.best_seed_rt
        )
        requests.append(
            XICRequest(
                mz=float(candidate.precursor_mz),
                rt_min=rt - rt_window_min,
                rt_max=rt + rt_window_min,
                ppm_tol=ppm,
            )
        )
        if len(requests) >= request_count:
            break
    return tuple(requests)


def _assert_single_request_equivalence(
    raw,
    requests: tuple[XICRequest, ...],
) -> None:
    for request in requests:
        direct = _trace_from_pair(
            raw.extract_xic(
                request.mz,
                request.rt_min,
                request.rt_max,
                request.ppm_tol,
            )
        )
        batched = raw.extract_xic_many((request,))[0]
        _assert_trace_equal(direct, batched, context=f"single {request}")


def _assert_same_window_batch_equivalence(
    raw,
    requests: tuple[XICRequest, ...],
) -> None:
    window = requests[0]
    same_window = tuple(
        XICRequest(
            mz=request.mz,
            rt_min=window.rt_min,
            rt_max=window.rt_max,
            ppm_tol=request.ppm_tol,
        )
        for request in requests[: min(4, len(requests))]
    )
    direct = tuple(
        _trace_from_pair(
            raw.extract_xic(
                request.mz,
                request.rt_min,
                request.rt_max,
                request.ppm_tol,
            )
        )
        for request in same_window
    )
    batched = raw.extract_xic_many(same_window)
    for index, (left, right) in enumerate(zip(direct, batched, strict=True)):
        _assert_trace_equal(left, right, context=f"same-window index={index}")


def _trace_from_pair(pair) -> XICTrace:
    rt, intensity = pair
    return XICTrace.from_arrays(rt, intensity)


def _assert_trace_equal(left: XICTrace, right: XICTrace, *, context: str) -> None:
    if not np.array_equal(left.rt, right.rt) or not np.array_equal(
        left.intensity,
        right.intensity,
    ):
        raise SystemExit(f"RAW XIC batch mismatch: {context}")


if __name__ == "__main__":
    raise SystemExit(main())

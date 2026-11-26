"""Draw the RT drift guide figures from one grouping snapshot.

Documentation adapter for ``docs/user/rt-drift.md``. The input is the
``grouping-inputs.json.gz`` an Alignment run writes with
``--save-grouping-inputs``; it holds the frozen calibration, drift model and
anchor observations, so nothing is refitted. Figures name no sample, only
injection order and the pooled-QC role.

    .venv\\Scripts\\python.exe -m tools.rt_drift_guide_figures <snapshot>
"""

from __future__ import annotations

import argparse
import io
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from xic_extractor.alignment.rt_anchors import BatchRtModelInputs  # noqa: E402
from xic_extractor.alignment.rt_drift import DriftAnchor, RtDriftModel  # noqa: E402
from xic_extractor.alignment.sample_rt_calibration import (  # noqa: E402
    BatchRtCalibration,
)
from xic_extractor.diagnostics.stage_snapshot import read_grouping_inputs  # noqa: E402

DEFAULT_OUTPUT = Path("docs/user/assets/rt-drift")
SAMPLE = "#2563a8"
QC = "#c2410c"
MUTED = "#9ca3af"

plt.rcParams.update(
    {
        "font.family": ["Microsoft JhengHei", "Noto Sans TC", "sans-serif"],
        "font.size": 10,
        "svg.fonttype": "none",
        "svg.hashsalt": "rt-drift-guide",
        "figure.facecolor": "#fbfaf7",
        "axes.facecolor": "#fbfaf7",
        "axes.edgecolor": "#c9c4b8",
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.color": "#e7e3da",
        "lines.linewidth": 1.4,
    }
)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("snapshot", type=Path, help="grouping-inputs.json.gz")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--mz",
        type=float,
        help="Drift anchor to decompose, nearest precursor m/z; default: the "
        "anchor with both coefficients nonzero observed in most samples",
    )
    args = parser.parse_args(argv)
    inputs, _ = read_grouping_inputs(args.snapshot)
    models = {
        id(w.rt_drift): w
        for w in inputs.worksets
        if w.rt_drift is not None and w.rt_drift.anchors
    }
    if len(models) != 1:
        raise SystemExit(f"Expected one batch drift model, found {len(models)}")
    (workset,) = models.values()
    drift, calibration, model_inputs = (
        workset.rt_drift,
        workset.rt_calibration,
        workset.rt_model_inputs,
    )
    if drift is None or calibration is None or model_inputs is None:
        raise SystemExit("Snapshot lacks the calibration or its anchor observations")
    figures = Figures(drift, calibration, model_inputs, args.output_dir)
    anchor = figures.example_anchor(args.mz)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    figures.residuals(anchor)
    figures.batch_state(anchor)
    figures.sample_shift()
    figures.decomposition(anchor)
    print(
        f"{len(model_inputs.sample_stems)} injections, "
        f"{len(model_inputs.qc_samples)} pooled QC, {len(drift.anchors)} drift "
        f"anchors; example m/z {figures.mz[anchor.channel]:.2f} at "
        f"{anchor.reference_rt_min:.2f} min -> {args.output_dir}"
    )


class Figures:
    def __init__(
        self,
        drift: RtDriftModel,
        calibration: BatchRtCalibration,
        inputs: BatchRtModelInputs,
        output_dir: Path,
    ) -> None:
        self.drift = drift
        self.calibration = calibration
        self.order = dict(inputs.injection_order)
        self.qc = inputs.qc_samples
        self.observations = [o for batch in inputs.anchor_batches for o in batch]
        self.mz = {o.channel: o.precursor_mz for o in self.observations}
        self.output_dir = output_dir
        self.source = (
            f"資料：{len(inputs.sample_stems)} 針（其中 {len(self.qc)} 針 pooled QC）"
        )

    def example_anchor(self, mz: float | None) -> DriftAnchor:
        anchors = self.drift.anchors
        if mz is not None:
            return min(anchors, key=lambda a: abs(self.mz[a.channel] - mz))
        both = [a for a in anchors if a.coefficients.nonzero_count == 2] or anchors
        return max(both, key=lambda a: (len(a.owner_ids), a.channel))

    def _calibrated(self, keep) -> dict[str, float]:
        values = {}
        for item in self.observations:
            if not keep(item):
                continue
            fitted = self.calibration.samples.get(item.raw_stem)
            rt = fitted.calibrated_rt(item.rt_min) if fitted is not None else None
            if rt is not None:
                values[item.raw_stem] = rt
        return values

    def _anchor_rt(self, anchor: DriftAnchor) -> dict[str, float]:
        return self._calibrated(lambda o: o.owner_id in anchor.owner_ids)

    def _ordinary_rt(self) -> tuple[str, dict[str, float]]:
        drifting = {a.channel for a in self.drift.anchors}
        counts: dict[str, set[str]] = {}
        for o in self.observations:
            if o.channel not in drifting:
                counts.setdefault(o.channel, set()).add(o.raw_stem)
        channel = max(counts, key=lambda c: (len(counts[c]), c))
        return channel, self._calibrated(lambda o: o.channel == channel)

    def _scatter(self, ax, values: Mapping[str, float], label: str = "") -> None:
        for qc, color, marker, name in (
            (False, SAMPLE, "o", "樣本"),
            (True, QC, "D", "pooled QC"),
        ):
            samples = [s for s in values if (s in self.qc) == qc]
            ax.scatter(
                [self.order[s] for s in samples],
                [values[s] for s in samples],
                s=18 if not qc else 26,
                color=color,
                marker=marker,
                label=f"{name}{label}",
                zorder=3,
            )
        ax.set_xlabel("進樣順序")

    def _save(self, fig, name: str) -> None:
        fig.text(0.01, 0.01, self.source, fontsize=8, color="#6b7280")
        buffer = io.StringIO()
        fig.savefig(buffer, format="svg", metadata={"Date": None})
        plt.close(fig)
        (self.output_dir / name).write_bytes(_compact_svg(buffer.getvalue()).encode())

    def residuals(self, anchor: DriftAnchor) -> None:
        """After the general map: an ordinary channel is flat, an anchor is not."""
        channel, ordinary = self._ordinary_rt()
        cases = (
            (channel, "一般化合物", ordinary),
            (anchor.channel, "漂移錨點", self._anchor_rt(anchor)),
        )
        fig, axes = plt.subplots(1, 2, figsize=(10, 3.6), sharey=True)
        for ax, (name, kind, values) in zip(axes, cases, strict=True):
            self._scatter(ax, _centered(values))
            rt = float(np.median(list(values.values())))
            ax.set_title(f"{kind}：m/z {self.mz[name]:.2f}，約 {rt:.1f} min")
        axes[0].set_ylabel("一般校正後 RT − 中位數（秒）")
        axes[1].legend(loc="upper left", frameon=False)
        fig.tight_layout(rect=(0, 0.04, 1, 1))
        self._save(fig, "residuals.svg")

    def batch_state(self, example: DriftAnchor) -> None:
        """Every drift anchor's own B shape, coloured by its RT."""
        anchors = sorted(self.drift.anchors, key=lambda a: a.reference_rt_min)
        rts = [a.reference_rt_min for a in anchors]
        norm = matplotlib.colors.Normalize(min(rts), max(rts))
        cmap = matplotlib.colormaps["viridis"]
        fig, ax = plt.subplots(figsize=(10, 3.8))
        for anchor in anchors:
            samples = sorted(anchor.shape, key=self.order.__getitem__)
            highlighted = anchor is example
            ax.plot(
                [self.order[s] for s in samples],
                [anchor.shape[s] for s in samples],
                color="#111827" if highlighted else cmap(norm(anchor.reference_rt_min)),
                alpha=1.0 if highlighted else 0.6,
                linewidth=2.6 if highlighted else 1.1,
                zorder=4 if highlighted else 2,
                label=f"m/z {self.mz[anchor.channel]:.2f}（下方拆解的例子）"
                if highlighted
                else None,
            )
        ax.legend(loc="upper left", frameon=False)
        for sample in sorted(self.qc):
            ax.axvline(self.order[sample], color=QC, alpha=0.35, linewidth=1, zorder=0)
        bar = fig.colorbar(matplotlib.cm.ScalarMappable(norm, cmap), ax=ax)
        bar.set_label("漂移錨點的 RT（min）")
        ax.set_xlabel("進樣順序（直線：pooled QC 的位置）")
        ax.set_ylabel("B（5–95% 範圍縮放為 1）")
        ax.set_title(f"{len(anchors)} 個漂移錨點各自的 B 形狀")
        fig.tight_layout(rect=(0, 0.04, 1, 1))
        self._save(fig, "batch-state.svg")

    def sample_shift(self) -> None:
        """M per injection; unknown M is left out."""
        values = {s: m * 60.0 for s, m in self.drift.sample_shift_min.items()}
        fig, ax = plt.subplots(figsize=(10, 3.4))
        ax.axhline(0, color=MUTED, linewidth=1)
        self._scatter(ax, values)
        ax.set_ylabel("M（秒）")
        ax.set_title("每一針的 M")
        ax.legend(loc="upper left", frameon=False)
        fig.tight_layout(rect=(0, 0.04, 1, 1))
        self._save(fig, "sample-shift.svg")

    def decomposition(self, anchor: DriftAnchor) -> None:
        """RT − center = a × B + b × M + error, for one anchor."""
        coefficients = anchor.coefficients
        observed = self._anchor_rt(anchor)
        shape, shift = anchor.shape, self.drift.sample_shift_min
        samples = [s for s in observed if s in shape and s in shift]
        time = {s: coefficients.time * shape[s] for s in samples}
        sample = {s: coefficients.sample_shift * shift[s] for s in samples}
        center = float(np.median([observed[s] - time[s] - sample[s] for s in samples]))
        seconds = {
            "measured": {s: (observed[s] - center) * 60 for s in samples},
            "time": {s: time[s] * 60 for s in samples},
            "sample": {s: sample[s] * 60 for s in samples},
        }
        seconds["error"] = {
            s: seconds["measured"][s] - seconds["time"][s] - seconds["sample"][s]
            for s in samples
        }
        panels = (
            ("measured", "① 量到的：RT − center"),
            ("time", f"② a × B（a = {coefficients.time:.2f} min）"),
            ("sample", f"③ b × M（b = {coefficients.sample_shift:.2f}）"),
            ("error", "④ 剩下的誤差：① − ② − ③"),
        )
        fig, axes = plt.subplots(1, 4, figsize=(12, 3.4), sharey=True)
        for ax, (key, title) in zip(axes, panels, strict=True):
            self._scatter(ax, seconds[key])
            ax.set_title(title, fontsize=10)
            ax.axhline(0, color=MUTED, linewidth=1, zorder=1)
        axes[0].set_ylabel("秒")
        fig.suptitle(
            f"m/z {self.mz[anchor.channel]:.2f}，約 {anchor.reference_rt_min:.1f} min："
            f"中間 90% 寬 {_spread(seconds['measured']):.0f} 秒 → "
            f"{_spread(seconds['error']):.0f} 秒（{len(samples)} 針）",
            fontsize=11,
        )
        fig.tight_layout(rect=(0, 0.04, 1, 1))
        self._save(fig, "decomposition.svg")


def _compact_svg(text: str) -> str:
    """One line per path and 0.01-px coordinates: same picture, a reviewable diff."""
    text = re.sub(r'd="([^"]*)"', lambda m: f'd="{" ".join(m[1].split())}"', text)
    text = re.sub(
        r"-?\d+\.\d{3,}", lambda m: f"{float(m[0]):.2f}".rstrip("0").rstrip("."), text
    )
    return "\n".join(line.rstrip() for line in text.splitlines()) + "\n"


def _centered(values: Mapping[str, float]) -> dict[str, float]:
    middle = float(np.median(list(values.values())))
    return {s: (v - middle) * 60 for s, v in values.items()}


def _spread(values: Mapping[str, float]) -> float:
    """Middle 90% width, the same span B is scaled by."""
    low, high = np.percentile(list(values.values()), [5, 95])
    return float(high - low)


if __name__ == "__main__":
    main()

"""Read target tables that name a method instead of declaring parameters.

Earlier tables had one ``method`` (or ``cid_nl_tag``) column: blank, dR/R/MeR,
or the name of an identification method defined beside the table. Reading
converts them to the parameter columns; writing always uses the new format.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from xic_extractor.configuration.models import ConfigError
from xic_extractor.configuration.targeted_identification import (
    DEFAULT_DELTA_MASS_SETTINGS,
    LEGACY_CID_NL_LOSSES,
    DeltaMassSettings,
    QuantRelativeIon,
    delta_mass_payload,
    format_offset,
)

LEGACY_METHOD_FIELD = "method"
LEGACY_TAG_FIELD = "cid_nl_tag"
# The earlier workbook sheet of named methods; such workbooks are no longer read.
LEGACY_METHOD_SHEET = "Identification Methods"
_SETTING_FIELDS = (
    "apex_floor_sec",
    "scan_interval_multiplier",
    "shape_window_sec",
    "min_shape_r",
)


def has_legacy_method_column(fields: Sequence[str]) -> bool:
    return LEGACY_METHOD_FIELD in fields or LEGACY_TAG_FIELD in fields


def upgrade_target_table(
    rows: Sequence[Mapping[str, str]],
    legacy_methods: Mapping | None = None,
) -> tuple[list[dict[str, str]], dict | None]:
    """Return new-format rows and the Delta Mass details they need.

    ``legacy_methods`` is the earlier ``identification_methods`` JSON object.
    Returns ``None`` details when no row used a named method.
    """
    copied = [dict(row) for row in rows]
    if not any(has_legacy_method_column(list(row)) for row in copied):
        return copied, None
    methods = dict(legacy_methods or {})
    ions: dict[str, tuple[QuantRelativeIon, ...]] = {}
    shared: list[tuple[str, DeltaMassSettings]] = []
    problems: list[str] = []
    upgraded: list[dict[str, str]] = []
    for row in copied:
        if LEGACY_METHOD_FIELD in row and LEGACY_TAG_FIELD in row:
            raise ConfigError(
                "target table has both method and cid_nl_tag columns; keep only method"
            )
        chosen = str(
            row.pop(LEGACY_METHOD_FIELD, row.pop(LEGACY_TAG_FIELD, "")) or ""
        ).strip()
        label = str(row.get("label", "")).strip()
        row.setdefault("cid_nl_loss_da", "")
        row.setdefault("delta_mass_mz", "")
        upgraded.append(row)
        if not chosen:
            continue
        if chosen in LEGACY_CID_NL_LOSSES:
            row["cid_nl_loss_da"] = str(LEGACY_CID_NL_LOSSES[chosen])
            continue
        if chosen not in methods:
            problems.append(f"{label}: 找不到方法 {chosen!r} 的定義")
            continue
        try:
            loss, target_ions, settings = _convert(methods[chosen])
        except ValueError as exc:
            problems.append(f"{label}（方法 {chosen}）：{exc}")
            continue
        if loss is not None:
            row["cid_nl_loss_da"] = str(loss)
        if target_ions:
            ions[label] = target_ions
            row["delta_mass_mz"] = ", ".join(
                format_offset(ion.offset_mz) for ion in target_ions
            )
            shared.append((label, settings))
    if len({settings for _, settings in shared}) > 1:
        problems.append(
            "這些目標的 Delta Mass m/z 容差或共洗脫設定不同，新格式整份清單共用一組："
            + "、".join(label for label, _ in shared)
        )
    if problems:
        raise ConfigError("舊格式的辨認方法無法轉成目標參數欄：" + "；".join(problems))
    if not shared:
        return upgraded, None
    return upgraded, delta_mass_payload(shared[0][1], ions)


def _convert(method: Mapping) -> tuple[float | None, tuple, DeltaMassSettings]:
    if not isinstance(method, Mapping):
        raise ValueError("方法定義格式不正確")
    conditions = method.get("conditions") or []
    if not isinstance(conditions, list) or not conditions:
        raise ValueError("方法沒有條件")
    kinds = [c.get("kind") if isinstance(c, Mapping) else None for c in conditions]
    if any(kind not in ("cid_nl", "delta_mass") for kind in kinds):
        raise ValueError("只支援 CID-NL 與 Delta Mass 條件")
    if kinds.count("cid_nl") > 1 or kinds.count("delta_mass") > 1:
        raise ValueError("同類條件超過一個")
    if method.get("combine_mode", "all") == "any" and len(conditions) > 1:
        raise ValueError("組合方式是「任一符合」")
    ppm = float(method.get("mz_tolerance_ppm", 20.0))
    loss = None
    ions: tuple[QuantRelativeIon, ...] = ()
    settings = DEFAULT_DELTA_MASS_SETTINGS
    for condition in conditions:
        if condition["kind"] == "cid_nl":
            if condition.get("tag") not in LEGACY_CID_NL_LOSSES:
                raise ValueError("CID-NL 條件需為 dR、R 或 MeR")
            loss = LEGACY_CID_NL_LOSSES[condition["tag"]]
            continue
        positions = condition.get("positions") or []
        quant = [p for p in positions if float(p.get("offset_mz", 0)) == 0]
        if len(quant) != 1 or len(positions) < 2:
            raise ValueError("Delta Mass 需要一個 Δ 0 的定量離子與至少一個伴隨離子")
        ions = tuple(
            QuantRelativeIon(
                label=str(p["label"]),
                offset_mz=float(p["offset_mz"]),
                ratio_min=float(p.get("ratio_min", 0.1)),
                ratio_max=float(p.get("ratio_max", 10.0)),
                # The earlier method default did not compare shape.
                compare_shape=bool(p.get("compare_shape", False)),
            )
            for p in positions
            if p is not quant[0]
        )
        settings = DeltaMassSettings(
            mz_tolerance_ppm=ppm,
            **{
                key: float(condition[key])
                for key in _SETTING_FIELDS
                if key in condition
            },
        )
    if not ions:
        # A named CID-NL-only method required the loss; the CID-NL column alone
        # only ranks peaks, so converting it would silently admit more peaks.
        raise ValueError(
            "只有 CID-NL 條件的方法是必要條件，新的 CID-NL 欄只用於排序；"
            "請改用 CID-NL 欄並確認選峰，或加入 Delta Mass"
        )
    return loss, ions, settings

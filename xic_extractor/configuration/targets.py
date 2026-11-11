import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.configuration.cid_nl_profile import (
    DEFAULT_CID_NL_MASS_POLICY,
    NeutralLossProfile,
)
from xic_extractor.configuration.csv_io import (
    LEGACY_TARGET_FIELDS,
    TARGET_FIELDS,
    _read_target_rows,
)
from xic_extractor.configuration.models import ConfigError, Target
from xic_extractor.configuration.parsing import (
    _config_error,
    _parse_float,
)
from xic_extractor.configuration.targeted_identification import (
    CidNlCondition,
    DeltaMassCondition,
    DeltaMassSettings,
    QuantRelativeIon,
    TargetIdentification,
    delta_mass_payload,
    format_offset,
    ion_name,
    parse_delta_mass_payload,
    target_cid_nl_loss,
)

LOGGER = logging.getLogger(__name__)


def _read_targets(path: Path, *, delta_mass: Mapping | None = None) -> list[Target]:
    from xic_extractor.configuration.legacy_target_methods import (
        upgrade_target_table,
    )

    numbered = _read_target_rows(path)
    rows, legacy_details = upgrade_target_table([row for _, row in numbered])
    return _parse_target_rows(
        path,
        [(number, row) for (number, _), row in zip(numbered, rows, strict=True)],
        delta_mass if delta_mass is not None else legacy_details,
    )


def parse_target_rows(
    rows: Sequence[Mapping[str, str]],
    path: Path = Path("targets.csv"),
    *,
    delta_mass: Mapping | None = None,
) -> tuple[Target, ...]:
    """Validate an in-memory table with the same target and ISTD rules as CSV intake.

    ``delta_mass`` is the Delta Mass details payload: list-shared settings and
    each target's ion ratio ranges, shape comparison and names.
    """
    return tuple(
        _parse_target_rows(
            path,
            [(number, dict(row)) for number, row in enumerate(rows, 2)],
            delta_mass,
        )
    )


def _parse_target_rows(
    path: Path,
    rows: list[tuple[int, dict[str, str]]],
    delta_mass: Mapping | None,
) -> list[Target]:
    try:
        settings, details = parse_delta_mass_payload(delta_mass)
    except (TypeError, ValueError, KeyError) as exc:
        raise ConfigError(f"{path}: Delta Mass 細節不正確：{exc}") from exc
    targets: list[Target] = []
    seen: set[str] = set()

    for row_number, row in rows:
        target = _parse_target_row(
            path,
            row_number,
            row,
            settings,
            details.get(str(row.get("label", "")).strip()),
        )
        if target.label in seen:
            raise _config_error(
                path, row_number, "label", target.label, "must be unique"
            )
        seen.add(target.label)
        targets.append(target)
    if not targets:
        raise _config_error(path, 2, "label", "", "at least one target row is required")
    if unknown := sorted(set(details) - seen):
        raise ConfigError(f"{path}: Delta Mass 細節指向不存在的目標：{unknown[0]}")

    _validate_target_relationships(path, targets)
    return targets


def _validate_target_relationships(path: Path, targets: list[Target]) -> None:
    by_label = {target.label: target for target in targets}
    for row_number, target in enumerate(targets, start=2):
        if target.is_istd and target.istd_pair:
            raise _config_error(
                path,
                row_number,
                "istd_pair",
                target.istd_pair,
                "is_istd=true targets must not set istd_pair",
            )
        if not target.istd_pair:
            continue
        pair = by_label.get(target.istd_pair)
        if pair is None or not pair.is_istd:
            raise _config_error(
                path,
                row_number,
                "istd_pair",
                target.istd_pair,
                "must reference a target with is_istd=true",
            )


def parse_delta_mass_offsets(text: str) -> tuple[float, ...]:
    """Read a comma-separated Δm/z cell, for example ``+1.0021, -2.0157``."""
    return tuple(
        float(part.strip())
        for part in text.replace("，", ",").split(",")
        if part.strip()
    )


def _parse_target_row(
    path: Path,
    row_number: int,
    row: dict[str, str],
    settings: DeltaMassSettings,
    details: tuple[QuantRelativeIon, ...] | None,
) -> Target:
    retired = sorted(set(row) & {*LEGACY_TARGET_FIELDS, "method", "cid_nl_tag"})
    if retired:
        raise _config_error(
            path,
            row_number,
            retired[0],
            row[retired[0]],
            "unsupported target schema; use expected_rt_min, role, "
            "cid_nl_loss_da and delta_mass_mz",
        )
    values = {field: str(row.get(field, "") or "").strip() for field in TARGET_FIELDS}
    label = values["label"]
    if not label:
        raise _config_error(path, row_number, "label", label, "must not be empty")
    mz = _parse_float(path, row_number, "mz", values["mz"])
    expected_rt = _parse_float(
        path, row_number, "expected_rt_min", values["expected_rt_min"]
    )
    if mz <= 0 or expected_rt < 0:
        raise _config_error(
            path,
            row_number,
            "mz/expected_rt_min",
            "",
            "m/z must be positive and expected RT non-negative",
        )
    if values["role"] not in {"analyte", "istd"}:
        raise _config_error(
            path, row_number, "role", values["role"], "must be analyte or istd"
        )
    loss = None
    if values["cid_nl_loss_da"]:
        loss = _parse_float(
            path, row_number, "cid_nl_loss_da", values["cid_nl_loss_da"]
        )
        if not 0 < loss < mz:
            raise _config_error(
                path,
                row_number,
                "cid_nl_loss_da",
                values["cid_nl_loss_da"],
                "CID-NL 中性丟失必須大於 0 且小於目標 m/z",
            )
    try:
        offsets = parse_delta_mass_offsets(values["delta_mass_mz"])
    except ValueError:
        raise _config_error(
            path,
            row_number,
            "delta_mass_mz",
            values["delta_mass_mz"],
            "must be comma-separated Δm/z numbers",
        ) from None
    if not offsets and details:
        raise _config_error(
            path, row_number, "delta_mass_mz", "", "Delta Mass 細節需要對應的 Δm/z"
        )
    identification = (
        _identification(
            path, row_number, values, mz, loss, offsets, settings, details or ()
        )
        if offsets
        else None
    )
    policy = DEFAULT_CID_NL_MASS_POLICY
    # CID-NL alone ranks candidates; with Delta Mass it is a required condition.
    plain_loss = loss if identification is None else None
    # Existing in-memory consumers retain a center pair, not a search window.
    return Target(
        label=label,
        mz=mz,
        rt_min=expected_rt,
        rt_max=expected_rt,
        ppm_tol=settings.mz_tolerance_ppm
        if identification is not None
        else policy.precursor_mz_tolerance_ppm,
        neutral_loss_da=plain_loss,
        nl_ppm_warn=policy.nl_tolerance_ppm if plain_loss is not None else None,
        nl_ppm_max=policy.nl_tolerance_ppm if plain_loss is not None else None,
        is_istd=values["role"] == "istd",
        istd_pair=values["istd_pair"],
        expected_rt_min=expected_rt,
        identification=identification,
    )


def _identification(
    path: Path,
    row_number: int,
    values: dict[str, str],
    mz: float,
    loss: float | None,
    offsets: tuple[float, ...],
    settings: DeltaMassSettings,
    details: tuple[QuantRelativeIon, ...],
) -> TargetIdentification:
    def fail(reason: str) -> ConfigError:
        return _config_error(
            path, row_number, "delta_mass_mz", values["delta_mass_mz"], reason
        )

    if len(set(offsets)) != len(offsets) or 0 in offsets:
        raise fail("Δm/z 不可重複，也不可為 0")
    by_offset = {ion.offset_mz: ion for ion in details}
    if len(by_offset) != len(details) or not set(by_offset) <= set(offsets):
        raise fail("Delta Mass 細節的 Δm/z 必須和目標列一致")
    ions = tuple(
        by_offset.get(offset) or QuantRelativeIon(ion_name(offset), offset)
        for offset in offsets
    )
    names = [ion.label for ion in ions]
    if len(set(names)) != len(names) or "base" in names:
        raise fail("Δm/z 太接近：伴隨離子名稱相同")
    masses = sorted([mz, *(mz + offset for offset in offsets)])
    if masses[0] <= 0 or any(
        right - left <= (left + right) * settings.mz_tolerance_ppm * 1e-6
        for left, right in zip(masses, masses[1:], strict=False)
    ):
        raise fail("ions require positive m/z and non-overlapping XIC windows")
    condition = DeltaMassCondition(ions, settings)
    try:
        condition.pattern_settings(values["label"])
    except ValueError as exc:
        raise fail(str(exc)) from None
    return TargetIdentification(
        (*((CidNlCondition(loss),) if loss is not None else ()), condition)
    )


def target_definition_row(target: Target) -> dict[str, str]:
    """The target-table row that admits to ``target``."""
    loss = target_cid_nl_loss(target)
    ions = target.identification.delta_mass.ions if target.identification else ()
    return {
        "label": target.label,
        "mz": str(target.mz),
        "expected_rt_min": str(target.expected_rt_min),
        "role": "istd" if target.is_istd else "analyte",
        "istd_pair": target.istd_pair,
        "cid_nl_loss_da": "" if loss is None else str(loss),
        "delta_mass_mz": ", ".join(format_offset(ion.offset_mz) for ion in ions),
    }


def reparse_target(target: Target, path: Path) -> Target:
    """Admit target's own definition alone, without list relationships."""
    settings, details = parse_delta_mass_payload(targets_delta_mass_payload([target]))
    return _parse_target_row(
        path, 2, target_definition_row(target), settings, details.get(target.label)
    )


def targets_delta_mass_payload(targets: Sequence[Target]) -> dict | None:
    """Delta Mass details of admitted targets, or None when none use it."""
    conditions = {
        target.label: target.identification.delta_mass
        for target in targets
        if target.identification is not None
    }
    if not conditions:
        return None
    settings = next(iter(conditions.values())).settings
    return delta_mass_payload(
        settings, {label: condition.ions for label, condition in conditions.items()}
    )


def read_targets(path: Path) -> tuple[Target, ...]:
    if path.suffix.lower() == ".xlsx":
        from xic_extractor.configuration.targeted_method_files import (
            load_workbook_targets,
        )

        return load_workbook_targets(path)
    return tuple(_read_targets(path))


@dataclass(frozen=True)
class IstdTargetSelection:
    """Untargeted projection of a validated shared target list."""

    targets: tuple[Target, ...]
    skipped_count: int
    method_skipped: tuple[Target, ...] = ()

    def for_method(
        self, profiles: Sequence[NeutralLossProfile]
    ) -> "IstdTargetSelection":
        """Limit standards to active chemistry, not measured detection status.

        Match configured loss values, not editable tag names or measurement
        tolerances. Each admitted standard retains its own CID-NL identity.
        """
        losses = {profile.neutral_loss_da for profile in profiles}
        applicable: list[Target] = []
        skipped: list[Target] = []
        for target in (*self.targets, *self.method_skipped):
            (applicable if target.neutral_loss_da in losses else skipped).append(target)
        return IstdTargetSelection(
            tuple(applicable), self.skipped_count, tuple(skipped)
        )


def read_istd_targets(path: Path) -> IstdTargetSelection:
    targets = read_targets(path)
    istds = tuple(target for target in targets if target.is_istd)
    if not istds:
        raise ValueError(
            f"{path}: no internal standards; set role=istd for an ISTD row "
            "or clear the optional list to run without ISTD alignment."
        )
    return IstdTargetSelection(istds, len(targets) - len(istds))

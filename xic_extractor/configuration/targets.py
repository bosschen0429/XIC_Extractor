import logging
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.configuration.csv_io import (
    OPTIONAL_TARGET_METADATA_FIELDS,
    TARGET_FIELDS,
    _read_target_rows,
)
from xic_extractor.configuration.models import Target
from xic_extractor.configuration.parsing import (
    _config_error,
    _parse_bool,
    _parse_float,
)

LOGGER = logging.getLogger(__name__)

ISOTOPE_LABEL_TYPES = frozenset({"deuterated", "heavy_non_deuterium", "unknown"})
PAIRED_RT_RELATIONS = frozenset(
    {"istd_not_later_than_pair", "learned_delta_only", "none"}
)
SAMPLE_APPLICABILITIES = frozenset({"all", "rna_containing"})


def _read_targets(path: Path) -> list[Target]:
    targets: list[Target] = []
    seen: set[str] = set()

    for row_number, row in _read_target_rows(path):
        target = _parse_target_row(path, row_number, row)
        if target.label in seen:
            raise _config_error(
                path, row_number, "label", target.label, "must be unique"
            )
        seen.add(target.label)
        targets.append(target)
    if not targets:
        raise _config_error(path, 2, "label", "", "at least one target row is required")

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
        if target.is_istd and target.paired_rt_relation != "none":
            raise _config_error(
                path,
                row_number,
                "paired_rt_relation",
                target.paired_rt_relation,
                "is_istd=true targets must leave paired_rt_relation blank",
            )
        if not target.istd_pair:
            if target.paired_rt_relation != "none":
                raise _config_error(
                    path,
                    row_number,
                    "paired_rt_relation",
                    target.paired_rt_relation,
                    "requires istd_pair",
                )
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
        if (
            target.paired_rt_relation == "istd_not_later_than_pair"
            and pair.isotope_label_type != "deuterated"
        ):
            raise _config_error(
                path,
                row_number,
                "paired_rt_relation",
                target.paired_rt_relation,
                "requires paired ISTD isotope_label_type=deuterated",
            )


def _parse_target_row(path: Path, row_number: int, row: dict[str, str]) -> Target:
    values = {
        field: str(row.get(field, "")).strip()
        for field in (*TARGET_FIELDS, *OPTIONAL_TARGET_METADATA_FIELDS)
    }
    label = values["label"]
    if not label:
        raise _config_error(path, row_number, "label", label, "must not be empty")

    mz = _parse_float(path, row_number, "mz", values["mz"])
    rt_min = _parse_float(path, row_number, "rt_min", values["rt_min"])
    rt_max = _parse_float(path, row_number, "rt_max", values["rt_max"])
    ppm_tol = _parse_float(path, row_number, "ppm_tol", values["ppm_tol"])
    is_istd = _parse_bool(path, row_number, "is_istd", values["is_istd"] or "false")

    if mz <= 0:
        raise _config_error(path, row_number, "mz", values["mz"], "must be > 0")
    if ppm_tol <= 0:
        raise _config_error(
            path, row_number, "ppm_tol", values["ppm_tol"], "must be > 0"
        )
    if rt_min < 0 or rt_max < 0 or rt_min >= rt_max:
        raise _config_error(
            path,
            row_number,
            "rt_min",
            values["rt_min"],
            "must be non-negative and < rt_max",
        )

    neutral_loss_da, nl_ppm_warn, nl_ppm_max = _parse_neutral_loss(
        path, row_number, values, mz
    )

    return Target(
        label=label,
        mz=mz,
        rt_min=rt_min,
        rt_max=rt_max,
        ppm_tol=ppm_tol,
        neutral_loss_da=neutral_loss_da,
        nl_ppm_warn=nl_ppm_warn,
        nl_ppm_max=nl_ppm_max,
        is_istd=is_istd,
        istd_pair=values["istd_pair"],
        isotope_label_type=_parse_target_metadata_enum(
            path,
            row_number,
            "isotope_label_type",
            values["isotope_label_type"],
            ISOTOPE_LABEL_TYPES,
            default="unknown",
        ),
        paired_rt_relation=_parse_target_metadata_enum(
            path,
            row_number,
            "paired_rt_relation",
            values["paired_rt_relation"],
            PAIRED_RT_RELATIONS,
            default="none",
        ),
        sample_applicability=_parse_target_metadata_enum(
            path,
            row_number,
            "sample_applicability",
            values["sample_applicability"],
            SAMPLE_APPLICABILITIES,
            default="all",
        ),
    )


def _parse_neutral_loss(
    path: Path, row_number: int, values: dict[str, str], mz: float
) -> tuple[float | None, float | None, float | None]:
    if values["neutral_loss_da"] == "":
        if values["nl_ppm_warn"] or values["nl_ppm_max"]:
            LOGGER.warning(
                "%s row %s has NL thresholds without neutral_loss_da; "
                "thresholds ignored",
                path,
                row_number,
            )
        return None, None, None

    neutral_loss_da = _parse_float(
        path, row_number, "neutral_loss_da", values["neutral_loss_da"]
    )
    nl_ppm_warn = _parse_float(path, row_number, "nl_ppm_warn", values["nl_ppm_warn"])
    nl_ppm_max = _parse_float(path, row_number, "nl_ppm_max", values["nl_ppm_max"])

    if neutral_loss_da <= 0 or neutral_loss_da >= mz:
        raise _config_error(
            path,
            row_number,
            "neutral_loss_da",
            values["neutral_loss_da"],
            "must be > 0 and < mz",
        )
    if nl_ppm_warn <= 0:
        raise _config_error(
            path, row_number, "nl_ppm_warn", values["nl_ppm_warn"], "must be > 0"
        )
    if nl_ppm_max <= 0:
        raise _config_error(
            path, row_number, "nl_ppm_max", values["nl_ppm_max"], "must be > 0"
        )
    if nl_ppm_warn > nl_ppm_max:
        raise _config_error(
            path,
            row_number,
            "nl_ppm_warn",
            values["nl_ppm_warn"],
            "must be <= nl_ppm_max",
        )

    return neutral_loss_da, nl_ppm_warn, nl_ppm_max


def _parse_target_metadata_enum(
    path: Path,
    row_number: int,
    field: str,
    value: str,
    allowed: frozenset[str],
    *,
    default: str,
) -> str:
    normalized = value.strip().lower()
    if normalized == "":
        return default
    if normalized not in allowed:
        raise _config_error(
            path,
            row_number,
            field,
            value,
            f"must be one of {', '.join(sorted(allowed))} or blank",
        )
    return normalized


def read_targets(path: Path) -> tuple[Target, ...]:
    return tuple(_read_targets(path))


@dataclass(frozen=True)
class IstdTargetSelection:
    """Untargeted projection of a validated shared target list."""

    targets: tuple[Target, ...]
    skipped_count: int


def read_istd_targets(path: Path) -> IstdTargetSelection:
    targets = read_targets(path)
    istds = tuple(target for target in targets if target.is_istd)
    if not istds:
        raise ValueError(
            f"{path}: no internal standards; set is_istd=TRUE for an ISTD row "
            "or clear the optional list to run without ISTD alignment."
        )
    return IstdTargetSelection(istds, len(targets) - len(istds))

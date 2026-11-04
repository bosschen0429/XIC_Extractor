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
    OPTIONAL_TARGET_METADATA_FIELDS,
    TARGET_FIELDS,
    _read_target_rows,
    normalize_target_row,
)
from xic_extractor.configuration.models import Target
from xic_extractor.configuration.parsing import (
    _config_error,
    _parse_float,
)
from xic_extractor.configuration.targeted_identification import (
    CID_NL_TAGS,
    DeltaMassCondition,
    IdentificationMethod,
)

LOGGER = logging.getLogger(__name__)

SAMPLE_APPLICABILITIES = frozenset({"all", "rna_containing"})


def _read_targets(path: Path, *, identification_methods=None) -> list[Target]:
    return _parse_target_rows(path, _read_target_rows(path), identification_methods)


def parse_target_rows(
    rows: Sequence[Mapping[str, str]],
    path: Path = Path("targets.csv"),
    *,
    identification_methods: Mapping[str, IdentificationMethod] | None = None,
) -> tuple[Target, ...]:
    """Validate an in-memory table with the same target and ISTD rules as CSV intake."""
    return tuple(
        _parse_target_rows(
            path,
            [(number, dict(row)) for number, row in enumerate(rows, 2)],
            identification_methods,
        )
    )


def _parse_target_rows(
    path: Path,
    rows: list[tuple[int, dict[str, str]]],
    identification_methods=None,
) -> list[Target]:
    targets: list[Target] = []
    seen: set[str] = set()

    for row_number, row in rows:
        target = _parse_target_row(path, row_number, row, identification_methods)
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


def _parse_target_row(
    path: Path,
    row_number: int,
    row: dict[str, str],
    identification_methods=None,
) -> Target:
    from xic_extractor.configuration.targeted_strategy import targeted_tag_profile

    row = normalize_target_row(row)
    retired = sorted(set(row) & set(LEGACY_TARGET_FIELDS))
    if retired:
        raise _config_error(
            path,
            row_number,
            retired[0],
            row[retired[0]],
            "unsupported target schema; use expected_rt_min, method and role",
        )
    values = {
        field: str(row.get(field, "")).strip()
        for field in (*TARGET_FIELDS, *OPTIONAL_TARGET_METADATA_FIELDS)
    }
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
    # Blank is MS1 only; dR/R/MeR are built-in CID-NL; anything else is a
    # user-defined identification method.
    chosen = values["method"]
    method = (identification_methods or {}).get(chosen)
    if chosen and chosen not in CID_NL_TAGS and method is None:
        raise _config_error(
            path,
            row_number,
            "method",
            chosen,
            "must be blank (MS1 only), dR, R, MeR or a defined identification method",
        )
    tag = chosen if chosen in CID_NL_TAGS else ""
    if method is not None:
        for condition in method.conditions:
            if not isinstance(condition, DeltaMassCondition):
                continue
            masses = sorted(mz + p.offset_mz for p in condition.positions)
            if masses[0] <= 0 or any(
                right - left <= (left + right) * method.mz_tolerance_ppm * 1e-6
                for left, right in zip(masses, masses[1:], strict=False)
            ):
                raise _config_error(
                    path,
                    row_number,
                    "method",
                    chosen,
                    "positions require positive m/z and non-overlapping XIC windows",
                )
    tag_profile = targeted_tag_profile(tag) if tag else None
    policy = tag_profile or DEFAULT_CID_NL_MASS_POLICY
    loss = tag_profile.neutral_loss_profiles[0].neutral_loss_da if tag_profile else None
    if loss is not None and loss >= mz:
        raise _config_error(
            path, row_number, "mz", values["mz"], "must exceed the selected CID-NL mass"
        )
    # Existing in-memory consumers retain a center pair, not a search window.
    return Target(
        label=label,
        mz=mz,
        rt_min=expected_rt,
        rt_max=expected_rt,
        ppm_tol=method.mz_tolerance_ppm
        if method
        else policy.precursor_mz_tolerance_ppm,
        neutral_loss_da=loss,
        nl_ppm_warn=policy.nl_tolerance_ppm if tag else None,
        nl_ppm_max=policy.nl_tolerance_ppm if tag else None,
        is_istd=values["role"] == "istd",
        istd_pair=values["istd_pair"],
        expected_rt_min=expected_rt,
        cid_nl_tag=tag,
        method_name=chosen if method else "",
        identification_method=method,
        sample_applicability=_parse_target_metadata_enum(
            path,
            row_number,
            "sample_applicability",
            values["sample_applicability"],
            SAMPLE_APPLICABILITIES,
            default="all",
        ),
    )


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

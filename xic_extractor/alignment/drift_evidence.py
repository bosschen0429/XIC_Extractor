from __future__ import annotations

import json
import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass

from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.configuration.models import Target
from xic_extractor.mass_accuracy import mz_tolerance_da, neutral_loss_match


@dataclass(frozen=True)
class KnownIstdReference:
    """Declared ISTD definitions and measured reference coordinates, never areas."""

    workbook_path: str
    workbook_sha256: str
    anchor: TargetedIstdIdentityAnchor
    coordinates: tuple[tuple[str, str, float, float], ...]

    @property
    def provenance(self) -> dict[str, str]:
        return {
            "workbook_path": self.workbook_path,
            "workbook_sha256": self.workbook_sha256,
            "target_snapshot": json.dumps(
                _istd_target_snapshot_payload(self.anchor.targets),
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ),
            "coordinates": json.dumps(self.coordinates, allow_nan=False),
            "observed_labels": json.dumps(self.anchor.observed_labels),
            "policy": "measured_istd_shared_rt_fit_same_target_fallback",
        }

    def bind(self, owners: tuple[SampleLocalMS1Owner, ...]) -> KnownIstdOwnerLookup:
        """Bind measured ISTD coordinates to every eligible physical owner."""
        targets = {target.label: target for target in self.anchor.targets}
        owners_by_sample: dict[str, list[SampleLocalMS1Owner]] = {}
        for owner in owners:
            owners_by_sample.setdefault(owner.sample_stem, []).append(owner)
        candidates: list[tuple[str, str]] = []
        counts: dict[str, int] = {}
        for label, sample, rt, _ in self.coordinates:
            target = targets[label]
            matches = tuple(
                owner
                for owner in owners_by_sample.get(sample, ())
                if owner.peak.rt_left_min <= rt <= owner.peak.rt_right_min
                and owner.primary_identity_event is not None
                and target.neutral_loss_da is not None
                and target.nl_ppm_max is not None
                and neutral_loss_match(
                    owner.primary_identity_event.precursor_mz,
                    owner.primary_identity_event.product_mz,
                    target.neutral_loss_da,
                    target.nl_ppm_max,
                    observed_loss_da=(
                        owner.primary_identity_event.observed_neutral_loss_da
                    ),
                )[2]
                and abs(owner.peak.mz - target.mz)
                <= mz_tolerance_da(target.mz, target.ppm_tol)
            )
            for owner in matches:
                counts[owner.owner_id] = counts.get(owner.owner_id, 0) + 1
            if len(matches) == 1:
                candidates.append((matches[0].owner_id, label))
        return KnownIstdOwnerLookup(
            tuple(item for item in candidates if counts[item[0]] == 1)
        )


@dataclass(frozen=True)
class KnownIstdOwnerLookup:
    """Each owner uniquely matched to one measured ISTD target, by label."""

    bindings: tuple[tuple[str, str], ...]


def mint_known_istd_reference_from_measurements(
    targets: Sequence[Target],
    coordinates: Mapping[tuple[str, str], float],
    *,
    source_identifier: str = "<in-memory>",
    source_sha256: str = "",
) -> KnownIstdReference:
    """Mint a KnownIstdReference from declared targets and measured apex RT.

    Directly populates the node map from coordinates without reading Excel.
    """
    istd_targets = tuple(t for t in targets if t.is_istd)
    if not istd_targets:
        raise ValueError("known ISTD reference requires at least one ISTD target")
    if len({t.label for t in istd_targets}) != len(istd_targets):
        raise ValueError("known ISTD reference requires unique ISTD target labels")

    labels = {t.label for t in istd_targets}

    for (label, _sample), rt in coordinates.items():
        if label not in labels:
            raise ValueError(f"undeclared ISTD target {label!r}")
        if not math.isfinite(rt) or rt < 0:
            raise ValueError(f"reference RT must be nonnegative finite float: {rt!r}")

    centers = {
        label: statistics.median(
            rt for (target, _), rt in coordinates.items() if target == label
        )
        for label in labels
        if any(target == label for target, _ in coordinates)
    }
    coords = tuple(
        (label, sample, rt, rt - centers[label])
        for (label, sample), rt in sorted(coordinates.items())
        if label in centers
    )
    return KnownIstdReference(
        workbook_path=source_identifier,
        workbook_sha256=source_sha256,
        anchor=build_targeted_istd_identity_anchor(
            istd_targets, observed_labels=labels
        ),
        coordinates=coords,
    )


def _istd_target_snapshot_payload(targets: tuple[Target, ...]) -> dict[str, object]:
    definitions = []
    for target in targets:
        payload = asdict(target)
        if target.identification is None:
            del payload["identification"]
        definitions.append({"target_label": target.label, "target": payload})
    return {
        "schema": "target_list_snapshot",
        "identity_namespace": "known-istd-reference",
        "definitions": definitions,
    }


@dataclass(frozen=True, slots=True)
class TargetedIstdIdentityAnchor:
    """Run-local target anchor for the known DNA dR ISTD lane.

    The anchor is derived from the owner-validated target definitions and the
    existing targeted ISTD workbook labels.  It is an internal grouping
    context, not a second grouping owner or a persisted Matrix identity.
    """

    targets: tuple[Target, ...]
    observed_labels: tuple[str, ...]


def build_targeted_istd_identity_anchor(
    targets: tuple[Target, ...],
    *,
    observed_labels: Iterable[str],
) -> TargetedIstdIdentityAnchor:
    """Mint a bounded target anchor from existing owner inputs."""

    labels = tuple(
        sorted({str(label).strip() for label in observed_labels if str(label).strip()})
    )
    if not targets or not labels:
        raise ValueError("ISTD identity anchor requires targets and observed labels")
    return TargetedIstdIdentityAnchor(
        targets=targets,
        observed_labels=labels,
    )

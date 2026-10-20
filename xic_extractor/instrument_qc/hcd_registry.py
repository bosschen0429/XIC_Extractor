from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import cast

from xic_extractor.instrument_qc.models import ActivationMethod, HCDProductIon

PRODUCT_REGISTRY_COLUMNS = (
    "compound_or_group",
    "precursor_mz",
    "activation",
    "product_label",
    "product_mz",
    "product_role",
)

BASE_PRODUCT_MASSES = {
    "G": (
        ("G", 151.0494),
        ("G+H", 152.0567),
        ("G+H-NH3", 135.0302),
        ("G_new", 55.0290),
    ),
    "A": (
        ("A", 135.0544),
        ("A+H", 136.0617),
        ("A+H-NH3", 119.0352),
        ("A_new", 92.0243),
    ),
    "C": (
        ("C", 111.0432),
        ("C+H", 112.0505),
        ("C+H-NH3", 95.0240),
        ("C_new", 68.0494),
    ),
    "T": (
        ("T", 126.0429),
        ("T+H", 127.0502),
        ("T+H-NH3", 110.0237),
        ("T_new", 95.0491),
    ),
    "U": (
        ("U", 112.0273),
        ("U+H", 113.0346),
        ("U+H-NH3", 96.0080),
        ("U+H-H2O", 95.0240),
        ("U_new", 70.0293),
    ),
}


def built_in_hcd_products() -> tuple[HCDProductIon, ...]:
    return (
        *_sdolek_products(),
        *(
            product
            for group, masses in BASE_PRODUCT_MASSES.items()
            for product in _base_products(group, masses)
        ),
    )


def load_hcd_product_registry(path: Path | None) -> tuple[HCDProductIon, ...]:
    products = {product_key(item): item for item in built_in_hcd_products()}
    if path is None:
        return tuple(products.values())
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        missing = [
            column for column in PRODUCT_REGISTRY_COLUMNS if column not in fieldnames
        ]
        if missing:
            raise ValueError(
                "Missing HCD product registry columns: " + ", ".join(missing)
            )
        for row in reader:
            if not row["compound_or_group"].strip():
                continue
            product = HCDProductIon(
                compound_or_group=row["compound_or_group"].strip(),
                precursor_mz=_optional_float(row["precursor_mz"]),
                activation=_parse_activation(row["activation"]),
                product_label=row["product_label"].strip(),
                product_mz=float(row["product_mz"]),
                product_role=row["product_role"].strip(),
            )
            products[product_key(product)] = product
    return tuple(products.values())


def product_key(product: HCDProductIon) -> tuple[str, str, str]:
    return (
        product.compound_or_group.casefold(),
        product.activation.casefold(),
        product.product_label.casefold(),
    )


def infer_base_group_from_label(label: str) -> str | None:
    label_base = base_group_from_label_base_letter(label)
    if label_base is not None:
        return label_base
    normalized = _normalize_label(label)
    if any(token in normalized for token in ("dg", "guo", "oxodg", "oxog")):
        return "G"
    if any(token in normalized for token in ("da", "ade", "meda", "n6meda")):
        return "A"
    if any(token in normalized for token in ("dc", "cyt", "medc", "hmdc", "cadc")):
        return "C"
    if "dt" in normalized or normalized == "t":
        return "T"
    if normalized.endswith("g") or normalized == "g":
        return "G"
    if normalized.endswith("a") or normalized == "a":
        return "A"
    if normalized.endswith("c") or normalized == "c":
        return "C"
    return None


def resolve_hcd_product_group(
    label: str,
    *,
    explicit_base_group: str | None = None,
    explicit_product_group: str | None = None,
) -> tuple[str | None, str]:
    if explicit_product_group:
        return explicit_product_group, "explicit_product_group"
    if explicit_base_group:
        return explicit_base_group, "explicit_base_group"
    label_base = base_group_from_label_base_letter(label)
    if label_base is not None:
        return label_base, "label_base_letter"
    inferred = infer_base_group_from_label(label)
    if inferred is not None:
        return inferred, "heuristic"
    return None, "unmapped"


def products_for_group(
    products: tuple[HCDProductIon, ...],
    *,
    compound: str,
    product_group: str | None,
    activation: ActivationMethod,
) -> tuple[HCDProductIon, ...]:
    keys = [compound.casefold()]
    if product_group:
        keys.append(product_group.casefold())
    candidates = [
        product for product in products if product.compound_or_group.casefold() in keys
    ]
    if activation == "CID":
        filtered = [product for product in candidates if product.activation == "CID"]
    elif activation == "CIDwHCD":
        filtered = [
            product
            for product in candidates
            if product.activation in {"CID", "wHCD", "HCD"}
        ]
    elif activation in {"wHCD", "HCD"}:
        filtered = [
            product for product in candidates if product.activation in {"wHCD", "HCD"}
        ]
    else:
        filtered = candidates
    return tuple(filtered)


def _sdolek_products() -> tuple[HCDProductIon, ...]:
    values: list[HCDProductIon] = []
    for compound, precursor, activation, masses in (
        ("SDO", 311.0814, "CID", (156.0768, 245.1035, 218.0232, 108.0446)),
        ("SDO", 311.0814, "wHCD", (156.0770, 108.0446, 92.0497, 65.0338)),
        ("LEK", 556.2771, "CID", (425.1820, 397.1870, 538.2664, 510.2710)),
        ("LEK", 556.2771, "wHCD", (120.0810, 136.0759, 278.1138, 221.0922)),
    ):
        for index, mass in enumerate(masses, start=1):
            values.append(
                HCDProductIon(
                    compound_or_group=compound,
                    precursor_mz=precursor,
                    activation=cast(ActivationMethod, activation),
                    product_label=f"Product-{index}",
                    product_mz=mass,
                    product_role="sdolek_product",
                )
            )
    return tuple(values)


def _base_products(
    group: str,
    masses: tuple[tuple[str, float], ...],
) -> tuple[HCDProductIon, ...]:
    return tuple(
        HCDProductIon(
            compound_or_group=group,
            precursor_mz=None,
            activation="HCD",
            product_label=label,
            product_mz=mass,
            product_role="base_product",
        )
        for label, mass in masses
    )


def _parse_activation(value: str) -> ActivationMethod:
    normalized = value.strip()
    if normalized in {"CID", "wHCD", "HCD", "CIDwHCD", "unknown"}:
        return normalized  # type: ignore[return-value]
    raise ValueError(f"unsupported HCD product activation: {value}")


def _optional_float(value: str) -> float | None:
    value = value.strip()
    if not value:
        return None
    return float(value)


def _normalize_label(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", label.casefold())


def base_group_from_label_base_letter(label: str) -> str | None:
    label_without_isotopes = re.sub(r"\[[^\]]+\]", "", label)
    candidates = [
        char for char in label_without_isotopes if char in {"A", "T", "C", "G", "U"}
    ]
    if not candidates:
        return None
    return candidates[-1]

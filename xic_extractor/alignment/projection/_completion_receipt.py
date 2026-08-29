"""In-process identity graph digest for completed projection receipts."""

from __future__ import annotations

from collections.abc import Mapping, Set
from dataclasses import fields, is_dataclass
from hashlib import sha256
from typing import Any


def retained_owner_digest(
    value: object,
    *,
    identity_only: tuple[object, ...] = (),
) -> bytes:
    """Digest exact retained identities and fields without invoking owner policy."""

    digest = sha256()
    seen: dict[int, int] = {}
    identity_only_ids = {id(item) for item in identity_only}

    def feed(text: str) -> None:
        encoded = text.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)

    def visit(item: Any) -> None:
        if item is None or type(item) in {bool, int, float, str, bytes}:
            feed(f"scalar:{type(item).__module__}.{type(item).__qualname__}")
            feed(repr(item))
            return

        owner_id = id(item)
        if owner_id in seen:
            feed(f"ref:{seen[owner_id]}")
            return
        seen[owner_id] = len(seen)
        feed(f"owner:{type(item).__module__}.{type(item).__qualname__}")
        feed(str(owner_id))
        if owner_id in identity_only_ids:
            feed("identity-only")
            return

        if is_dataclass(item) and not isinstance(item, type):
            for owner_field in fields(item):
                if owner_field.name == "_completion_seal":
                    continue
                feed(owner_field.name)
                visit(getattr(item, owner_field.name))
            return
        if isinstance(item, tuple | list):
            feed(f"sequence:{len(item)}")
            for member in item:
                visit(member)
            return
        if isinstance(item, Mapping):
            feed(f"mapping:{len(item)}")
            for key, member in item.items():
                visit(key)
                visit(member)
            return
        if isinstance(item, Set):
            feed(f"set:{len(item)}")
            for member in sorted(
                item,
                key=lambda member: (
                    type(member).__module__,
                    type(member).__qualname__,
                    repr(member),
                ),
            ):
                visit(member)
            return
        feed(repr(item))

    visit(value)
    return digest.digest()


__all__ = ("retained_owner_digest",)

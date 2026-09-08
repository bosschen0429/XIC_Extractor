"""Receipt parity protects retained-owner identity and tamper detection."""

from dataclasses import dataclass, field
from hashlib import sha256

import pytest

from xic_extractor.alignment import _completion_receipt as receipt


@dataclass
class Owner:
    value: object
    _completion_seal: object = field(default=None)


def test_native_receipt_encodes_exact_python_tokens(monkeypatch) -> None:
    native = pytest.importorskip("xic_extractor.alignment._completion_receipt_native")
    shared = Owner(["非 ASCII", b"\x00\xff", -0.0, float("nan"), 2**100])
    identity_only = Owner(object())
    graph = [None, True, shared, shared, {"ordered": (1, 2)}, {3, "a"}]
    graph.extend([graph, identity_only, Owner])
    chunks = []

    class Capture:
        def update(self, data):
            chunks.append(data)

        def digest(self):
            return sha256(b"".join(chunks)).digest()

    monkeypatch.setattr(receipt, "sha256", Capture)
    expected = receipt._python_retained_owner_digest(
        graph, identity_only=(identity_only,)
    )
    assert native.encoded_graph(graph, identity_only=(identity_only,)) == b"".join(
        chunks
    )
    actual = receipt.retained_owner_digest(graph, identity_only=(identity_only,))
    assert actual == expected


@pytest.mark.parametrize("fallback", [False, True])
def test_receipt_detects_changed_fields_identity_and_aliasing(monkeypatch, fallback):
    if fallback:
        monkeypatch.setattr(receipt, "_native_digest", None)
    shared = Owner([1, "value"])
    opaque = Owner(["original"])
    graph = [shared, shared, opaque]

    def digest():
        return receipt.retained_owner_digest(graph, identity_only=(opaque,))

    original = digest()
    shared._completion_seal = object()
    opaque.value.append("excluded")
    assert digest() == original
    shared.value.append("changed")
    assert digest() != original
    shared.value.pop()
    assert digest() == original
    graph[1] = Owner([1, "value"])
    assert digest() != original


@pytest.mark.parametrize("fallback", [False, True])
def test_receipt_preserves_encoding_errors_and_recursion_bound(monkeypatch, fallback):
    if fallback:
        monkeypatch.setattr(receipt, "_native_digest", None)

    class BadRepr:
        def __repr__(self):
            raise ValueError("owner repr failed")

    with pytest.raises(ValueError, match="owner repr failed"):
        receipt.retained_owner_digest(BadRepr())
    nested = []
    for _ in range(2000):
        nested = [nested]
    with pytest.raises(RecursionError):
        receipt.retained_owner_digest(nested)
    expected = receipt._python_retained_owner_digest(None)
    assert receipt.retained_owner_digest(None) == expected

"""Atheris fuzz target for GestaltDB deserializers (ClusterFuzzLite).

Exercises the JSON and MessagePack deserializers with arbitrary bytes and
re-encodes successfully decoded payloads, back through the same serializer, to
catch crashes, hangs and unexpected exceptions in codec paths.

Failure policy: malformed storage bytes may raise ``ValueError``/``TypeError``
(codec and structural tagged-value errors) or ``RecursionError`` (pathologically
nested JSON). Any other exception during decoding, or *any* exception during the
post-decode re-encode, is an unexpected failure and is allowed to escape so the
fuzzer reports it. A successful roundtrip must also decode back to an equal
payload (see :func:`_structural_equal`); silent value drift fails the target too.
"""

import math
import sys

try:  # pragma: no cover - libFuzzer always provides atheris in the fuzz build
    import atheris
except ImportError:  # pragma: no cover - lets unit tests import the policy helpers
    atheris = None

from gestaltdb.serializers import JSONSerializer, MessagePackSerializer

EXPECTED_DECODE_ERRORS = (ValueError, TypeError, RecursionError)


def _structural_equal(left: object, right: object) -> bool:
    """Compare decoded trees without Python recursion, including NaN values."""
    pending = [(left, right)]
    while pending:
        left, right = pending.pop()
        if isinstance(left, float) and isinstance(right, float):
            if left != right and not (math.isnan(left) and math.isnan(right)):
                return False
        elif isinstance(left, dict) and isinstance(right, dict):
            if left.keys() != right.keys():
                return False
            pending.extend((left[key], right[key]) for key in left)
        elif isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
            if len(left) != len(right):
                return False
            pending.extend(zip(left, right))
        elif left != right:
            return False
    return True


def _roundtrip(serializer, blob: bytes) -> None:
    try:
        decoded = serializer.deserialize(blob)
    except EXPECTED_DECODE_ERRORS:
        return
    encoded = serializer.serialize(decoded)
    restored = serializer.deserialize(encoded)
    if not _structural_equal(decoded, restored):
        raise AssertionError(
            f"{type(serializer).__name__} roundtrip drifted for {blob!r}"
        )


def TestOneInput(data: bytes) -> None:  # noqa: N802 - fuzzer entry-point convention
    fdp = atheris.FuzzedDataProvider(data)
    blob = fdp.ConsumeBytes(4096)
    _roundtrip(JSONSerializer(), blob)
    _roundtrip(MessagePackSerializer(), blob)


def main() -> None:
    if atheris is None:
        raise RuntimeError("atheris is required to run this fuzz target")
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()


if __name__ == "__main__":
    main()

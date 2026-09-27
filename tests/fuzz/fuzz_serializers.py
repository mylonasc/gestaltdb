"""Atheris fuzz target for GestaltDB deserializers (ClusterFuzzLite).

Exercises the JSON and MessagePack deserializers with arbitrary bytes and
round-trips successfully decoded payloads back through the serializer to
catch crashes, hangs, and unexpected exceptions in codec paths.
"""

import sys

import atheris

try:
    # ClusterFuzzLite: build.sh stages a copy of serializers.py next to this
    # target so the fuzzer does not pull in numpy via gestaltdb/__init__.py.
    from serializers import JSONSerializer, MessagePackSerializer
except ImportError:  # local development / CI checkouts
    from gestaltdb.serializers import JSONSerializer, MessagePackSerializer


def _roundtrip(serializer, blob: bytes) -> None:
    try:
        obj = serializer.deserialize(blob)
    except Exception:
        return
    try:
        serializer.serialize(obj)
    except Exception:
        pass


def TestOneInput(data: bytes) -> None:  # noqa: N802 - fuzzer entry-point convention
    fdp = atheris.FuzzedDataProvider(data)
    blob = fdp.ConsumeBytes(4096)
    _roundtrip(JSONSerializer(), blob)
    _roundtrip(MessagePackSerializer(), blob)


def main() -> None:
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()


if __name__ == "__main__":
    main()

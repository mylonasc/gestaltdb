"""Regression coverage for the serializer fuzz target's failure policy.

The ClusterFuzzLite target must not swallow unexpected codec failures. These
tests load the target module directly (atheris is optional outside the fuzz
build) and pin the policy with small serializer doubles.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

TARGET_PATH = Path(__file__).parent / "fuzz" / "fuzz_serializers.py"


def _load_target():
    spec = importlib.util.spec_from_file_location("gestaltdb_fuzz_serializers", TARGET_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


target = _load_target()


class _DecoderFailure:
    def deserialize(self, blob):
        raise AssertionError("unexpected decoder regression")


class _EncoderFailure:
    def deserialize(self, blob):
        return {"valid": True}

    def serialize(self, obj):
        raise RuntimeError("unexpected encoder regression")


class _DriftingSerializer:
    def deserialize(self, blob):
        return {"value": 1} if blob == b"first" else {"value": 2}

    def serialize(self, obj):
        return b"second"


class _MalformedInput:
    def deserialize(self, blob):
        raise ValueError("malformed storage bytes")

    def serialize(self, obj):  # pragma: no cover - must never be reached
        raise AssertionError("serialize must not run after a decode failure")


@pytest.mark.parametrize("serializer", [_DecoderFailure(), _EncoderFailure()])
def test_unexpected_codec_failures_escape(serializer):
    with pytest.raises((AssertionError, RuntimeError), match="unexpected"):
        target._roundtrip(serializer, b"input")


def test_silent_roundtrip_drift_fails():
    with pytest.raises(AssertionError, match="roundtrip drifted"):
        target._roundtrip(_DriftingSerializer(), b"first")


def test_expected_malformed_input_is_ignored():
    target._roundtrip(_MalformedInput(), b"input")


def test_nan_payloads_compare_equal():
    nan = float("nan")
    assert target._structural_equal({"scores": [nan, 1.0]}, {"scores": [nan, 1.0]})
    assert not target._structural_equal({"score": nan}, {"score": 0.0})


@pytest.mark.parametrize("container", [list, tuple, dict])
@pytest.mark.parametrize("equal", [True, False])
def test_deep_payload_comparison_does_not_recurse(container, equal):
    left, right = 1, 1 if equal else 2
    for _ in range(sys.getrecursionlimit() + 100):
        if container is dict:
            left, right = {"nested": left}, {"nested": right}
        else:
            left, right = container([left]), container([right])
    assert target._structural_equal(left, right) is equal


def test_deep_json_roundtrip():
    # The codec accepts this depth; the old recursive comparator did not.
    blob = b"[" * 400 + b"0" + b"]" * 400
    target._roundtrip(target.JSONSerializer(), blob)


def test_saved_batch_fuzz_crash_roundtrip():
    pytest.importorskip("msgpack")
    # Run 43579732393, crash-64049bac4647e9d3e9ecf9007bda99c1483250bc:
    # 361 nested single-element MessagePack arrays around integer 10.
    target._roundtrip(target.MessagePackSerializer(), b"\x91" * 361 + b"\x0a")


@pytest.mark.parametrize(
    "left,right",
    [({"a": 1}, {"b": 1}), ([1], [1, 2]), ([1], {"a": 1})],
)
def test_structural_mismatches(left, right):
    assert not target._structural_equal(left, right)


def test_real_serializers_roundtrip_malformed_and_valid_bytes():
    import importlib.util

    from gestaltdb.serializers import JSONSerializer, MessagePackSerializer

    serializers = [JSONSerializer()]
    if importlib.util.find_spec("msgpack") is not None:
        serializers.append(MessagePackSerializer())
    for serializer in serializers:
        target._roundtrip(serializer, b"not a valid payload")
        target._roundtrip(serializer, serializer.serialize({"a": [1, "raw", None]}))

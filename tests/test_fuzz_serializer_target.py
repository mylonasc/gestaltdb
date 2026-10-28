"""Regression coverage for the serializer fuzz target's failure policy.

The ClusterFuzzLite target must not swallow unexpected codec failures. These
tests load the target module directly (atheris is optional outside the fuzz
build) and pin the policy with small serializer doubles.
"""

import importlib.util
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


def test_real_serializers_roundtrip_malformed_and_valid_bytes():
    import importlib.util

    from gestaltdb.serializers import JSONSerializer, MessagePackSerializer

    serializers = [JSONSerializer()]
    if importlib.util.find_spec("msgpack") is not None:
        serializers.append(MessagePackSerializer())
    for serializer in serializers:
        target._roundtrip(serializer, b"not a valid payload")
        target._roundtrip(serializer, serializer.serialize({"a": [1, "raw", None]}))

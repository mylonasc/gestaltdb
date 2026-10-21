"""RE-07 M1 typed literal vocabulary, normalization and comparison semantics."""

import math

import pytest

from gestaltdb.reasoning.model import ReasoningError
from gestaltdb.reasoning.terms import KINDS, Literal, compare, equal, same_kind


def test_literal_construction_validates_kinds_and_values():
    assert Literal("boolean", True).value is True
    assert Literal("integer", -7).value == -7
    assert Literal("float", 1.5).value == 1.5
    assert Literal("text", "").value == ""
    assert Literal("bytes", b"\x00").value == b"\x00"
    assert Literal("instant", 0).value == 0
    assert Literal("null", None).value is None
    for kind, value in [("boolean", 1), ("boolean", None), ("integer", True),
                        ("integer", 1.0), ("float", 1), ("text", b"x"),
                        ("bytes", "x"), ("instant", False), ("null", 0),
                        ("unknown", 1)]:
        with pytest.raises(ReasoningError):
            Literal(kind, value)


def test_lexical_parsing_is_strict_and_canonical():
    assert Literal.from_lexical("boolean", "true") == Literal("boolean", True)
    assert Literal.from_lexical("integer", "  +007 ") == Literal("integer", 7)
    assert Literal.from_lexical("float", "1e3") == Literal("float", 1000.0)
    assert Literal.from_lexical("bytes", "de ad be ef") == Literal("bytes", bytes.fromhex("deadbeef"))
    assert Literal.from_lexical("null", "") == Literal("null", None)
    for kind, lexical in [("boolean", "True"), ("boolean", "1"), ("integer", ""),
                          ("integer", "0x10"), ("integer", "1.5"), ("float", "abc"),
                          ("bytes", "zz"), ("instant", "now"), ("null", "null")]:
        with pytest.raises(ReasoningError):
            Literal.from_lexical(kind, lexical)
    assert Literal("integer", 7).lexical == "7"
    assert Literal("float", float("nan")).lexical == "NaN"
    assert Literal("float", float("inf")).lexical == "INF"
    assert Literal("bytes", b"\x00\xff").lexical == "00ff"


def test_nan_identity_and_kind_strict_equality():
    nan = Literal("float", float("nan"))
    assert not equal(nan, nan) and not equal(nan, Literal("float", 1.0))
    assert equal(Literal("integer", 1), Literal("integer", 1))
    assert not equal(Literal("integer", 1), Literal("float", 1.0))
    assert not equal(Literal("text", "1"), Literal("integer", 1))
    assert equal(Literal("null", None), Literal("null", None))
    assert same_kind(Literal("boolean", True), Literal("boolean", False))
    assert not same_kind(Literal("integer", 1), Literal("instant", 1))
    with pytest.raises(ReasoningError):
        same_kind(Literal("integer", 1), "not-a-literal")
    assert Literal.from_lexical("float", "nan") != Literal.from_lexical("float", "NaN")
    assert hash(Literal("integer", 1)) == hash(Literal("integer", 1))


def test_ordering_is_same_kind_only_with_none_for_incomparable():
    assert compare(Literal("integer", 1), Literal("integer", 2)) == -1
    assert compare(Literal("integer", 2), Literal("integer", 2)) == 0
    assert compare(Literal("text", "b"), Literal("text", "a")) == 1
    assert compare(Literal("boolean", False), Literal("boolean", True)) == -1
    assert compare(Literal("bytes", b"\x00"), Literal("bytes", b"\xff")) == -1
    nan = Literal("float", math.nan)
    assert compare(nan, nan) is None
    assert compare(Literal("float", 1.0), nan) is None
    assert compare(Literal("integer", 1), Literal("float", 1.0)) is None
    assert compare(Literal("integer", 1), Literal("instant", 1)) is None
    assert compare(Literal("null", None), Literal("null", None)) is None
    assert Literal("float", math.inf).value > Literal("float", 1e308).value


def test_core_runs_without_site_packages():
    from pathlib import Path
    import subprocess
    import sys

    path = Path(__file__).parents[1] / "src/gestaltdb/reasoning/__init__.py"
    script = f"""
import importlib.util, sys
spec = importlib.util.spec_from_file_location('standalone', {str(path)!r})
module = importlib.util.module_from_spec(spec)
sys.modules['standalone'] = module
spec.loader.exec_module(module)
from standalone.terms import Literal, compare, equal
assert equal(Literal.from_lexical('integer', '007'), Literal('integer', 7))
assert compare(Literal.from_lexical('text', 'a'), Literal.from_lexical('text', 'b')) == -1
assert not any(name.startswith(('gestaltdb', 'numpy', 'pyarrow', 'polars')) for name in sys.modules)
"""
    subprocess.run([sys.executable, "-I", "-S", "-c", script], check=True, capture_output=True, text=True)

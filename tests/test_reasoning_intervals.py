"""RE-19 interval sets, support segments and seeded oracle parity."""

import random

import pytest

from gestaltdb.reasoning.intervals import (
    Interval,
    IntervalError,
    IntervalSet,
    SupportSegment,
    coalesce_segments,
)


def test_interval_validation_and_membership():
    assert Interval(0, 1).contains(0) and not Interval(0, 1).contains(1)
    assert Interval(None, 0).contains(-10**18) and not Interval(None, 0).contains(0)
    assert Interval(5, None).contains(10**18) and not Interval(5, None).contains(4)
    assert Interval(None, None).contains(0)
    with pytest.raises(IntervalError):
        Interval(3, 3)
    with pytest.raises(IntervalError):
        Interval(4, 3)
    with pytest.raises(IntervalError):
        Interval(True, 5)
    with pytest.raises(IntervalError):
        Interval(0, "x")
    with pytest.raises(IntervalError):
        Interval(0, 1).contains(True)
    with pytest.raises(IntervalError):
        Interval(0, 1).contains("x")
    assert Interval(0, 2).overlaps(Interval(1, 3))
    assert not Interval(0, 1).overlaps(Interval(1, 2))
    assert Interval(0, 1).touches(Interval(1, 2))
    assert not Interval(0, 1).touches(Interval(2, 3))
    assert not Interval(None, 0).touches(Interval(6, None))  # unbounded ends never abut
    assert Interval(None, 0).touches(Interval(0, None))  # (-inf,0) + [0,inf) tile exactly
    assert IntervalSet([Interval(None, None)]).difference(Interval(0, 6)).intervals == (
        Interval(None, 0), Interval(6, None))
    assert Interval(0, 5).intersect(Interval(3, 9)) == Interval(3, 5)
    assert Interval(0, 1).intersect(Interval(1, 2)) is None
    assert Interval(0, 10).difference(Interval(3, 7)) == (Interval(0, 3), Interval(7, 10))
    assert Interval(0, 10).difference(Interval(0, 10)) == ()
    assert Interval(0, 10).difference(Interval(20, 30)) == (Interval(0, 10),)
    assert Interval(None, 10).difference(Interval(3, 7)) == (Interval(None, 3), Interval(7, 10))
    assert Interval(0, None).difference(Interval(3, 7)) == (Interval(0, 3), Interval(7, None))
    assert Interval(0, 1).digest != Interval(0, 2).digest
    with pytest.raises(IntervalError):
        Interval(0, 1).intersect("nope")
    with pytest.raises(IntervalError):
        Interval(0, 1).difference("nope")
    with pytest.raises(IntervalError):
        Interval(0, 1).overlaps("nope")


def test_set_normalization_never_envelopes_gaps():
    assert IntervalSet([Interval(2, 3), Interval(0, 1)]).intervals == (Interval(0, 1), Interval(2, 3))
    assert IntervalSet([Interval(0, 2), Interval(1, 3)]).intervals == (Interval(0, 3),)
    assert IntervalSet([Interval(0, 1), Interval(1, 2)]).intervals == (Interval(0, 2),)
    assert IntervalSet([Interval(0, 1), Interval(2, 3)]).intervals == (Interval(0, 1), Interval(2, 3))
    assert IntervalSet([Interval(None, 1), Interval(0, 2)]).intervals == (Interval(None, 2),)
    assert IntervalSet([Interval(0, None), Interval(5, 6)]).intervals == (Interval(0, None),)
    assert IntervalSet([]).intervals == ()
    assert 0 in IntervalSet([Interval(0, 1)]) and 1 not in IntervalSet([Interval(0, 1)])
    assert "x" not in IntervalSet([Interval(0, 1)])
    with pytest.raises(IntervalError):
        IntervalSet(["nope"])
    with pytest.raises(IntervalError):
        IntervalSet([Interval(0, 1)], max_intervals=0)
    with pytest.raises(IntervalError, match="max_intervals"):
        IntervalSet([Interval(0, 1), Interval(3, 4)], max_intervals=1)
    with pytest.raises(IntervalError):
        IntervalSet([Interval(0, 1)]).union("nope")
    assert IntervalSet([Interval(0, 1)]).digest == IntervalSet([Interval(0, 1)]).digest


def test_union_intersection_difference():
    left = IntervalSet([Interval(0, 5), Interval(10, 15)])
    right = IntervalSet([Interval(3, 12)])
    assert left.union(right).intervals == (Interval(0, 15),)
    assert left.intersection(right).intervals == (Interval(3, 5), Interval(10, 12))
    assert left.difference(right).intervals == (Interval(0, 3), Interval(12, 15))
    assert left.union(Interval(5, 10)).intervals == (Interval(0, 15),)
    assert left.intersection(Interval(20, 30)).intervals == ()
    assert left.difference(IntervalSet([])).intervals == left.intervals
    assert IntervalSet([]).union(left).intervals == left.intervals
    assert IntervalSet([]).difference(left).intervals == ()


def _points(intervals, domain):
    covered = set()
    for start, end in intervals:
        for point in domain:
            if (start is None or start <= point) and (end is None or point < end):
                covered.add(point)
    return covered


def _random_set(rng):
    ends = [rng.choice([None, 0, 1, 2, 3, 4, 5, 6, 7, 8]) for _ in range(4)]
    pairs = [(ends[0], ends[1]), (ends[2], ends[3])]
    valid = []
    for start, end in pairs:
        if start is None or end is None or start < end:
            valid.append(Interval(start, end))
    return IntervalSet(valid)


def test_seeded_oracle_parity_over_gaps_touching_and_corrections():
    rng = random.Random(20261023)
    domain = list(range(9))
    for _ in range(300):
        left, right = _random_set(rng), _random_set(rng)
        assert _points([(i.start, i.end) for i in left.union(right).intervals], domain) == (
            _points([(i.start, i.end) for i in left.intervals], domain)
            | _points([(i.start, i.end) for i in right.intervals], domain))
        assert _points([(i.start, i.end) for i in left.intersection(right).intervals], domain) == (
            _points([(i.start, i.end) for i in left.intervals], domain)
            & _points([(i.start, i.end) for i in right.intervals], domain))
        assert _points([(i.start, i.end) for i in left.difference(right).intervals], domain) == (
            _points([(i.start, i.end) for i in left.intervals], domain)
            - _points([(i.start, i.end) for i in right.intervals], domain))
        # Touching boundaries tile: union of [a,b)+[b,c) covers exactly the span.
        assert IntervalSet([Interval(2, 5), Interval(5, 7)]).intervals == (Interval(2, 7),)


def test_support_segments_coalesce_within_support_only():
    segments = [SupportSegment(Interval(0, 2), "s1"), SupportSegment(Interval(1, 3), "s1"),
                SupportSegment(Interval(0, 3), "s2"), SupportSegment(Interval(5, 6), "s1")]
    out = coalesce_segments(segments)
    assert out == (SupportSegment(Interval(0, 3), "s1"), SupportSegment(Interval(5, 6), "s1"),
                   SupportSegment(Interval(0, 3), "s2"))
    # Touching same-support segments merge; disjoint ones keep their gap.
    assert coalesce_segments([SupportSegment(Interval(0, 1), "s"),
                              SupportSegment(Interval(1, 2), "s")]) == (
        SupportSegment(Interval(0, 2), "s"),)
    assert coalesce_segments([SupportSegment(Interval(0, 1), "s"),
                              SupportSegment(Interval(2, 3), "s")]) == (
        SupportSegment(Interval(0, 1), "s"), SupportSegment(Interval(2, 3), "s"))
    # Context partitioning: every point maps to exactly its covering supports.
    partitioned = coalesce_segments([SupportSegment(Interval(0, 5), "truth"),
                                     SupportSegment(Interval(0, 2), "evidence-a"),
                                     SupportSegment(Interval(3, 5), "evidence-b")])
    assert {item.support for item in partitioned if item.interval.contains(1)} == {"truth", "evidence-a"}
    assert {item.support for item in partitioned if item.interval.contains(4)} == {"truth", "evidence-b"}
    assert {item.support for item in partitioned if item.interval.contains(2)} == {"truth"}
    assert coalesce_segments([]) == ()
    with pytest.raises(IntervalError):
        coalesce_segments(["nope"])
    with pytest.raises(IntervalError):
        coalesce_segments(iter("nope"))
    with pytest.raises(IntervalError):
        coalesce_segments([SupportSegment(Interval(0, 1), "s")], max_segments=0)
    with pytest.raises(IntervalError, match="max_segments"):
        coalesce_segments([SupportSegment(Interval(0, 1), "a"), SupportSegment(Interval(0, 1), "b")],
                          max_segments=1)
    with pytest.raises(IntervalError):
        SupportSegment("nope", "s")
    with pytest.raises(IntervalError):
        SupportSegment(Interval(0, 1), "")


def test_core_runs_without_site_packages():
    from pathlib import Path
    import subprocess
    import sys

    path = Path(__file__).parents[1] / "src/gestaltdb/reasoning/__init__.py"
    script = f"""\
import importlib.util, sys
spec = importlib.util.spec_from_file_location('standalone', {str(path)!r})
module = importlib.util.module_from_spec(spec)
sys.modules['standalone'] = module
spec.loader.exec_module(module)
from standalone.intervals import Interval, IntervalSet, coalesce_segments, SupportSegment
assert IntervalSet([Interval(0, 2), Interval(1, 3)]).intervals == (Interval(0, 3),)
assert coalesce_segments([SupportSegment(Interval(0, 1), 's')])[0].support == 's'
assert not any(name.startswith(('gestaltdb', 'numpy')) for name in sys.modules)
"""
    subprocess.run([sys.executable, "-I", "-S", "-c", script], check=True, capture_output=True, text=True)

"""RE-22 temporal conformance, replay determinism and historical compliance."""

import importlib.util

import pytest

from gestaltdb.reasoning.intervals import Interval, IntervalSet
from gestaltdb.reasoning.model import (
    ReasoningCancelledError,
    ReasoningError,
    ReasoningLimitError,
    ReasoningLimits,
    Variable,
)
from gestaltdb.reasoning.temporal_closure import (
    ClosureVerification,
    TemporalClosure,
    TemporalFact,
    change_segments,
    evaluate_range,
    verify_closure,
)
from gestaltdb.reasoning.typed import GenericProgram, GenericRule, TypedAtom, TypedFact


X, Y, Z = Variable("x"), Variable("y"), Variable("z")


def chain_program():
    return GenericProgram([
        GenericRule("base", [TypedAtom("edge", [X, Y])], [], TypedAtom("reach", [X, Y])),
        GenericRule("step", [TypedAtom("reach", [X, Y]), TypedAtom("edge", [Y, Z])], [],
                    TypedAtom("reach", [X, Z])),
    ])


def span(start, end):
    return IntervalSet([Interval(start, end)])


def test_verify_accepts_healthy_overlapping_and_disjoint_closures():
    prog = chain_program()
    facts = [TemporalFact(TypedFact("edge", ["a", "b"]), span(0, 10)),
             TemporalFact(TypedFact("edge", ["b", "c"]), IntervalSet([Interval(0, 3), Interval(5, 15)])),
             TemporalFact(TypedFact("edge", ["c", "d"]), span(20, 25))]
    closure = evaluate_range(prog, facts, limits=ReasoningLimits())
    report = verify_closure(prog, closure, limits=ReasoningLimits())
    assert isinstance(report, ClosureVerification)
    assert report.segments == len(closure.segments) and report.derivations == len(closure.derivations)
    assert report.points_checked == len(closure.segments)
    # Overlapping + disjoint supports both verify; gaps stay gaps.
    assert closure.answers(TypedFact("reach", ["a", "c"])) == IntervalSet([Interval(0, 3), Interval(5, 10)])
    assert closure.answers(TypedFact("reach", ["c", "d"])) == span(20, 25)
    assert closure.answers(TypedFact("reach", ["a", "d"])).intervals == ()


def test_verify_rejects_tampered_closures():
    prog = chain_program()
    facts = [TemporalFact(TypedFact("edge", ["a", "b"]), span(0, 10)),
             TemporalFact(TypedFact("edge", ["b", "c"]), span(5, 15))]
    closure = evaluate_range(prog, facts, limits=ReasoningLimits())
    dropped = TemporalClosure(closure.segments, closure.base, closure.derivations[1:])
    with pytest.raises(ReasoningError, match="mismatch"):
        verify_closure(prog, dropped, limits=ReasoningLimits())
    shifted = TemporalClosure((Interval(99, 100),) + closure.segments[1:], closure.base, closure.derivations)
    with pytest.raises(ReasoningError, match="drifted|mismatch|unknown segment"):
        verify_closure(prog, shifted, limits=ReasoningLimits())
    wrong_base = [TemporalFact(TypedFact("edge", ["a", "b"]), span(0, 4)),
                  TemporalFact(TypedFact("edge", ["b", "c"]), span(5, 15))]
    with pytest.raises(ReasoningError):
        verify_closure(prog, TemporalClosure(closure.segments, tuple(wrong_base), closure.derivations),
                       limits=ReasoningLimits())
    with pytest.raises(ReasoningError):
        verify_closure("nope", closure, limits=ReasoningLimits())
    with pytest.raises(ReasoningError):
        verify_closure(prog, "nope", limits=ReasoningLimits())
    with pytest.raises(ReasoningCancelledError):
        verify_closure(prog, closure, limits=ReasoningLimits(), cancel=lambda: True)


def test_system_time_replay_is_deterministic():
    prog = chain_program()
    facts = [TemporalFact(TypedFact("edge", [f"n{i}", f"n{i+1}"]), span(i, i + 6)) for i in range(10)]
    first = evaluate_range(prog, facts, limits=ReasoningLimits())
    second = evaluate_range(prog, facts, limits=ReasoningLimits())
    assert first == second
    assert first.segments == second.segments
    assert verify_closure(prog, second, limits=ReasoningLimits()).derivations == len(second.derivations)


def test_limits_expose_no_partial_results():
    prog = chain_program()
    facts = [TemporalFact(TypedFact("edge", [f"n{i}", f"n{i+1}"]), span(i, i + 2)) for i in range(12)]
    with pytest.raises(ReasoningLimitError):
        evaluate_range(prog, facts, limits=ReasoningLimits(), max_segments=2)
    with pytest.raises(ReasoningError):
        change_segments(facts, max_segments=0)
    assert len(change_segments(facts)) > 2  # inputs untouched by the failed runs


def test_historical_compliance_employment_scenario():
    prog = GenericProgram([
        GenericRule("employed-during", [TypedAtom("contract", [X, Y])], [],
                    TypedAtom("employed", [X, Y])),
        GenericRule("covered", [TypedAtom("employed", [X, Y]), TypedAtom("policy", [Y, Z])], [],
                    TypedAtom("covered", [X, Z])),
    ])
    history = [TemporalFact(TypedFact("contract", ["ann", "acme"]), span(0, 12)),
               TemporalFact(TypedFact("contract", ["ann", "acme"]), span(18, 30)),
               TemporalFact(TypedFact("policy", ["acme", "p1"]), span(0, 40))]
    closure = evaluate_range(prog, history, limits=ReasoningLimits())
    assert closure.answers(TypedFact("employed", ["ann", "acme"])) == IntervalSet(
        [Interval(0, 12), Interval(18, 30)])
    assert TypedFact("covered", ["ann", "p1"]) not in closure.at(15)
    assert TypedFact("covered", ["ann", "p1"]) in closure.at(20)
    # Correction narrows history; the audit trail stays per-interval.
    corrected = [TemporalFact(TypedFact("contract", ["ann", "acme"]), span(0, 10)),
                 TemporalFact(TypedFact("contract", ["ann", "acme"]), span(18, 30)),
                 TemporalFact(TypedFact("policy", ["acme", "p1"]), span(0, 40))]
    from gestaltdb.reasoning.temporal_closure import update_range
    out = update_range(prog, closure, corrected, limits=ReasoningLimits())
    assert out.closure.answers(TypedFact("employed", ["ann", "acme"])) == IntervalSet(
        [Interval(0, 10), Interval(18, 30)])
    assert len(out.closure.evidence(TypedFact("employed", ["ann", "acme"]))) == 2
    verify_closure(prog, out.closure, limits=ReasoningLimits())


BACKEND = next((module for module in ("lmdb", "plyvel", "pyrex")
                if importlib.util.find_spec(module) is not None), None)


@pytest.mark.skipif(BACKEND is None, reason="no capability-supported storage backend installed")
def test_historical_readback_on_backend(tmp_path):
    from gestaltdb.graphdb import GraphDB, Node
    from gestaltdb.kvstores import LMDBStore, LevelDBStore, PyRexStore
    from gestaltdb.serializers import PickleSerializer
    from gestaltdb.temporal import TemporalInstant, TemporalInterval

    stores = {"lmdb": LMDBStore, "leveldb": LevelDBStore, "pyrex": PyRexStore}
    graph = GraphDB(stores[BACKEND](path=str(tmp_path / "backend")), PickleSerializer())
    try:
        graph.put_node_version(Node(node_id="ann", properties={"employer": "acme"}),
                               valid=TemporalInterval(TemporalInstant(0), TemporalInstant(10)))
        graph.put_node_version(Node(node_id="ann", properties={"employer": "globex"}),
                               valid=TemporalInterval(TemporalInstant(10), TemporalInstant(20)))
        assert graph.get_node_as_of("ann", valid_time=TemporalInstant(0)) is not None
        assert graph.get_node_as_of("ann", valid_time=TemporalInstant(9)) is not None
        assert graph.get_node_as_of("ann", valid_time=TemporalInstant(10)) is not None
        assert graph.get_node_as_of("ann", valid_time=TemporalInstant(19)) is not None
        assert graph.get_node_as_of("ann", valid_time=TemporalInstant(20)) is None
        first = graph.get_node_as_of("ann", valid_time=TemporalInstant(3))
        second = graph.get_node_as_of("ann", valid_time=TemporalInstant(13))
        assert first.node.properties["employer"] == "acme"
        assert second.node.properties["employer"] == "globex"
    finally:
        graph.close()

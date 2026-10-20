"""Independent closure oracles and explicit prototype capability boundaries."""

import itertools
import random
import sys

import pytest

from gestaltdb.reasoning.adapters.polars import PolarsDatalogPrototype, _FrameOps
from gestaltdb.reasoning.engine import DatalogEngine
from gestaltdb.reasoning.model import Atom, Fact, Program, ReasoningError, ReasoningLimitError, ReasoningLimits, Rule, Variable
from gestaltdb.reasoning.protocols import Evidence, InputContext
from tests.test_reasoning_engine import _reference


X, Y, Z = Variable("x"), Variable("y"), Variable("z")
CONTEXT = InputContext("in-memory", "world", "ontology", "policy", "mapping")


def evaluate(program, facts, **limits):
    pytest.importorskip("polars")
    pytest.importorskip("pyarrow")
    return PolarsDatalogPrototype().evaluate(program, facts, context=CONTEXT,
                                             limits=ReasoningLimits(**limits))


@pytest.mark.parametrize("kind", ["chain", "ground", "disconnected", "duplicate", "late-delta", "constants", "empty-join"])
def test_specialized_shapes_match_reference_and_indexed_engine(kind):
    inputs = [Fact("a", "p", "b"), Fact("b", "p", "c"), Fact("c", "q", "d")]
    rules = {
        "chain": [Rule("t", [Atom(X, "p", Y), Atom(Y, "p", Z)], Atom(X, "p", Z))],
        "ground": [Rule("ground", [Atom("a", "p", "b")], Atom("?x", "out", "?y"))],
        "disconnected": [Rule("d", [Atom(X, "p", Y), Atom("c", "q", Z)], Atom(X, "out", Z))],
        "duplicate": [Rule("d", [Atom(X, "p", Y)] * 2, Atom(Y, "out", X))],
        "late-delta": [Rule("copy", [Atom(X, "q", Y)], Atom(X, "late", Y)),
                       Rule("late", [Atom(X, "p", Y), Atom(Y, "late", Z)], Atom(X, "out", Z))],
        "constants": [Rule("c", [Atom(X, "p", "b")], Atom("é", "out", X)),
                      Rule("same", [Atom(X, "p", X)], Atom(X, "out", X))],
        "empty-join": [Rule("e", [Atom(X, "p", Y), Atom("not-present", "q", Z)], Atom(X, "out", Z))],
    }[kind]
    program = Program(rules)
    result = evaluate(program, inputs)
    assert set(result.result.facts) == _reference(program, inputs)
    expected = DatalogEngine().evaluate(program, inputs, limits=ReasoningLimits())
    assert result.result == expected
    assert result.identity.program_digest == program.digest and result.identity.context == CONTEXT
    assert result.metrics["atom_plans"] >= 1


def test_seeded_random_rules_match_independent_grounding():
    rng = random.Random(20261021)
    terms = [X, Y, "a", "b"]
    for _ in range(30):
        inputs = [Fact(rng.choice("ab"), rng.choice("pq"), rng.choice("ab")) for _ in range(5)]
        rules = []
        for number in range(3):
            body = [Atom(rng.choice(terms), rng.choice("pq"), rng.choice(terms)) for _ in range(rng.randint(1, 3))]
            bound = set().union(*(a.variables for a in body))
            heads = ["a", "b", *sorted(bound, key=lambda v: v.name)]
            rules.append(Rule(str(number), body, Atom(rng.choice(heads), rng.choice("pq"), rng.choice(heads))))
        program = Program(rules)
        result = evaluate(program, inputs).result
        assert set(result.facts) == _reference(program, inputs)
        assert result == evaluate(program, list(reversed(inputs)) + inputs).result


def test_duplicates_evidence_empty_inputs_and_unreferenced_predicates():
    fact = Fact("?constant", "unused", "urn:" + "x" * 1024)
    program = Program()
    result = evaluate(program, [Evidence(fact, "a:v1"), Evidence(fact, "b:v1")]).result
    assert result.facts == (fact,) and result.iterations == 0 and result.proofs is None
    assert evaluate(program, []).result.facts == ()
    missing = Program([Rule("m", [Atom(X, "p", Y)], Atom(X, "q", Y))])
    assert evaluate(missing, []).result.iterations == 0


def test_unsupported_proofs_fail_before_input_or_optional_import(monkeypatch):
    def forbidden():
        raise AssertionError("input consumed")
        yield

    monkeypatch.setitem(sys.modules, "polars", None)
    with pytest.raises(ReasoningError, match="witness"):
        PolarsDatalogPrototype().evaluate(Program(), forbidden(), context=CONTEXT,
                                         limits=ReasoningLimits(), collect_proofs=True)
    with pytest.raises(ImportError, match=r"gestaltdb\[polars\]"):
        PolarsDatalogPrototype().evaluate(Program(), [], context=CONTEXT, limits=ReasoningLimits())


def test_input_fact_rule_and_iteration_limits_do_not_poison_next_run():
    program = Program([Rule("a", [Atom(X, "p", Y)], Atom(X, "q", Y)),
                       Rule("b", [Atom(X, "q", Y)], Atom(X, "r", Y))])
    seen = []

    def source():
        for number in itertools.count():
            seen.append(number)
            yield Fact("a", "p", "b")

    with pytest.raises(ReasoningLimitError, match="max_input_facts"):
        evaluate(program, source(), max_input_facts=2)
    assert seen == [0, 1, 2]
    for kwargs, match in [({"max_rules": 1}, "max_rules"), ({"max_facts": 1}, "max_facts"),
                          ({"max_iterations": 1}, "max_iterations"), ({"max_candidate_rows": 1}, "max_candidate_rows")]:
        with pytest.raises(ReasoningLimitError, match=match):
            evaluate(program, [Fact("a", "p", "b")], **kwargs)
    assert len(evaluate(program, [Fact("a", "p", "b")]).result.facts) == 3


def test_intermediate_bound_rejects_cartesian_join_before_materialization():
    pl = pytest.importorskip("polars")
    ops = _FrameOps(pl, ReasoningLimits(), 20)
    left = pl.DataFrame({"v0": range(10), "_unit": [0] * 10}, schema={"v0": pl.UInt64, "_unit": pl.UInt64})
    right = left.rename({"v0": "v1"})
    with pytest.raises(ReasoningLimitError, match="before materialization"):
        ops.join(left, right, ["_unit"])
    assert ops.metrics["join_calls"] == 0


def test_internal_semi_and_anti_joins_use_exact_set_existence():
    pl = pytest.importorskip("polars")
    ops = _FrameOps(pl, ReasoningLimits(), 100)
    left, right = pl.DataFrame({"k": [1, 2, 3]}), pl.DataFrame({"k": [2, 2]})
    assert ops.filter_join(left, right, ["k"], how="semi")["k"].to_list() == [2]
    assert ops.filter_join(left, right, ["k"], how="anti")["k"].to_list() == [1, 3]


def test_source_failure_and_invalid_input_never_return_partial_closure():
    def broken():
        yield Fact("a", "p", "b")
        raise RuntimeError("upstream failed")

    with pytest.raises(RuntimeError, match="upstream failed"):
        evaluate(Program(), broken())
    with pytest.raises(ReasoningError):
        evaluate(Program(), ["not-fact"])

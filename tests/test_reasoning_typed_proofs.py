"""#392 derivation tracking for typed reasoning."""

from dataclasses import replace

import pytest

from gestaltdb.reasoning.model import ReasoningError, ReasoningLimits, Variable
from gestaltdb.reasoning.terms import Literal
from gestaltdb.reasoning.typed import (
    AggregateRule,
    Aggregation,
    Comparison,
    GenericEngine,
    GenericProgram,
    GenericResult,
    GenericRule,
    StratifiedRun,
    TypedAtom,
    TypedFact,
)
from gestaltdb.reasoning.typed_proofs import (
    AbsenceCheck,
    AggregateDerivation,
    TypedExplanation,
    TypedFactWitness,
    TypedProofTrace,
)


X, Y, N = Variable("x"), Variable("y"), Variable("n")
LIMITS = ReasoningLimits()


def parent_program():
    return GenericProgram([
        GenericRule("child", [TypedAtom("parent", [X, Y])], [], TypedAtom("child", [Y])),
        GenericRule("grandchild", [TypedAtom("child", [X]), TypedAtom("parent", [X, Y])],
                    [], TypedAtom("grandchild", [Y])),
    ])


def test_derived_facts_carry_premises_and_rule_ids():
    facts = [TypedFact("parent", ["a", "b"]), TypedFact("parent", ["b", "c"])]
    result = GenericEngine().evaluate(parent_program(), facts, limits=LIMITS, collect_proofs=True)
    assert result.proofs is not None and not result.proofs.truncated
    node = result.proofs._by_fact[TypedFact("child", ["b"])]
    assert node.rule.rule_id == "child"
    assert node.premises == (TypedFact("parent", ["a", "b"]),)
    root = result.proofs._by_fact[TypedFact("parent", ["a", "b"])]
    assert root.rule is None and root.premises == ()
    explanation = result.proofs.explain(TypedFact("grandchild", ["c"]))
    assert isinstance(explanation, TypedExplanation)
    assert {node.fact for node in explanation.nodes} == {
        TypedFact("parent", ["a", "b"]), TypedFact("parent", ["b", "c"]),
        TypedFact("child", ["b"]), TypedFact("grandchild", ["c"])}
    assert not explanation.truncated


def test_comparisons_are_ground_and_held():
    program = GenericProgram([GenericRule(
        "adult", [TypedAtom("age", [X, Y])], [Comparison(">=", Y, Literal("integer", 18))],
        TypedAtom("adult", [X]))])
    facts = [TypedFact("age", ["a", Literal("integer", 30)])]
    result = GenericEngine().evaluate(program, facts, limits=LIMITS, collect_proofs=True)
    node = result.proofs._by_fact[TypedFact("adult", ["a"])]
    assert node.comparisons == (Comparison(">=", Literal("integer", 30), Literal("integer", 18)),)
    assert all(check.holds({}) for check in node.comparisons)
    with pytest.raises(ReasoningError):
        GenericEngine().evaluate(program, facts, limits=LIMITS, collect_proofs="yes")


def test_negation_explanations_name_the_closed_scope():
    program = GenericProgram([GenericRule(
        "ok", [TypedAtom("person", [X])], [], TypedAtom("ok", [X]),
        negated=[TypedAtom("banned", [X])])], closed=["banned"])
    facts = [TypedFact("person", ["a"]), TypedFact("person", ["b"]), TypedFact("banned", ["b"])]
    run = GenericEngine().evaluate_stratified(program, facts, limits=LIMITS, collect_proofs=True)
    assert run.proofs is not None
    node = run.proofs._by_fact[TypedFact("ok", ["a"])]
    assert node.absence == (AbsenceCheck(0, "banned", ("a",), ("banned",)),)
    assert TypedFact("ok", ["b"]) not in run.result
    assert run.proofs.explain(TypedFact("ok", ["b"])).truncated


def test_aggregates_identify_contributing_facts_and_ops():
    program = GenericProgram([], aggregates=[AggregateRule(
        "cnt", [TypedAtom("edge", [X, Y])], (X,), (Aggregation("count", Y, N),),
        TypedAtom("deg", [X, N]))])
    facts = [TypedFact("edge", ["a", "x"]), TypedFact("edge", ["a", "y"])]
    result = GenericEngine().evaluate(program, facts, limits=LIMITS, collect_proofs=True)
    node = result.proofs._by_fact[TypedFact("deg", ["a", Literal("integer", 2)])]
    assert len(node.aggregates) == 1
    derivation = node.aggregates[0]
    assert derivation.op == "count" and derivation.group == ("a",)
    assert isinstance(derivation, AggregateDerivation)
    assert set(derivation.operands) == set(facts)
    assert derivation.result == Literal("integer", 2)
    assert node.premises == ()
    explanation = result.proofs.explain(TypedFact("deg", ["a", Literal("integer", 2)]))
    assert {item.fact for item in explanation.nodes} == {
        TypedFact("deg", ["a", Literal("integer", 2)]), *facts}


def test_stratified_aggregate_proofs_cover_result_closure():
    # Regression for #411: evaluate_stratified omitted the proof collector
    # when running the aggregate stratum, so the untruncated trace missed
    # aggregate conclusions and failed result-closure validation.
    total = Variable("total")
    value = Variable("value")
    program = GenericProgram([], aggregates=[AggregateRule(
        "sum-values", [TypedAtom("amount", [value])], (),
        (Aggregation("sum", value, total),),
        TypedAtom("total", [total]))])
    facts = [TypedFact("amount", [Literal("integer", 10)])]
    expected = {*facts, TypedFact("total", [Literal("integer", 10)])}

    ordinary = GenericEngine().evaluate(program, facts, limits=LIMITS, collect_proofs=True)
    assert set(ordinary.facts) == expected

    without_proofs = GenericEngine().evaluate_stratified(
        program, facts, limits=LIMITS, collect_proofs=False)
    assert set(without_proofs.result.facts) == expected

    run = GenericEngine().evaluate_stratified(
        program, facts, limits=LIMITS, collect_proofs=True)
    assert set(run.result.facts) == expected
    assert run.proofs is not None and not run.proofs.truncated
    assert len(run.proofs.nodes) == len(run.result.facts)
    node = run.proofs._by_fact[TypedFact("total", [Literal("integer", 10)])]
    assert node.aggregates and node.aggregates[0].op == "sum"
    assert set(node.aggregates[0].operands) == set(facts)
    explanation = run.proofs.explain(TypedFact("total", [Literal("integer", 10)]))
    assert not explanation.truncated
    assert {item.fact for item in explanation.nodes} == expected


def test_traces_are_deterministic_and_generation_bound():
    facts = [TypedFact("parent", ["a", "b"]), TypedFact("parent", ["b", "c"])]
    first = GenericEngine().evaluate(parent_program(), facts, limits=LIMITS, collect_proofs=True)
    second = GenericEngine().evaluate(parent_program(), list(reversed(facts)),
                                      limits=LIMITS, collect_proofs=True)
    assert first.proofs.nodes == second.proofs.nodes
    assert isinstance(first.proofs, TypedProofTrace)
    assert first.proofs.horizon == second.proofs.horizon
    assert first.proofs.is_current_for(parent_program(), facts)
    assert not first.proofs.is_current_for(parent_program(), facts + [TypedFact("parent", ["c", "d"])])


def test_proof_budgets_truncate_traces_never_closures():
    facts = [TypedFact("parent", [f"n{i}", f"n{i + 1}"]) for i in range(5)]
    program = GenericProgram([GenericRule(
        "child", [TypedAtom("parent", [X, Y])], [], TypedAtom("child", [Y]))])
    full = GenericEngine().evaluate(program, facts, limits=LIMITS)
    assert full.proofs is None
    tight = ReasoningLimits(max_proof_nodes=2, max_proof_edges=4)
    partial = GenericEngine().evaluate(program, facts, limits=tight, collect_proofs=True)
    assert partial.proofs.truncated
    assert set(partial.facts) == set(full.facts)
    assert len(partial.proofs.nodes) <= 2
    assert partial.proofs.explain(TypedFact("child", ["n5"])).truncated


def test_retraction_invalidates_explanations_until_rebuilt():
    facts = [TypedFact("parent", ["a", "b"]), TypedFact("parent", ["b", "c"])]
    before = GenericEngine().evaluate(parent_program(), facts, limits=LIMITS, collect_proofs=True)
    assert not before.proofs.explain(TypedFact("child", ["c"])).truncated
    reduced = [TypedFact("parent", ["a", "b"])]
    after = GenericEngine().evaluate(parent_program(), reduced, limits=LIMITS, collect_proofs=True)
    assert before.proofs.horizon != after.proofs.horizon
    assert not before.proofs.is_current_for(parent_program(), reduced)
    assert after.proofs.explain(TypedFact("child", ["c"])).truncated
    assert after.proofs.explain(TypedFact("child", ["c"])).nodes == ()
    assert not after.proofs.explain(TypedFact("child", ["b"])).truncated


def test_mismatched_proofs_fail_closed():
    facts = [TypedFact("parent", ["a", "b"])]
    result = GenericEngine().evaluate(parent_program(), facts, limits=LIMITS, collect_proofs=True)
    other = GenericProgram([GenericRule(
        "other", [TypedAtom("parent", [X, Y])], [], TypedAtom("kin", [X, Y]))])
    with pytest.raises(ReasoningError):
        GenericResult(tuple(result.facts), 1, other.digest, result.proofs)
    with pytest.raises(ReasoningError):
        StratifiedRun(GenericResult(tuple(result.facts) + (TypedFact("extra", ["z"]),),
                                    result.iterations, result.program_digest),
                      result.proofs.program.strata, (), (), False, result.proofs)
    with pytest.raises(ReasoningError):
        TypedFactWitness(TypedFact("child", ["b"]), parent_program().rules[0],
                         (TypedFact("parent", ["x", "y"]),))
    assert GenericEngine().evaluate(parent_program(), facts, limits=LIMITS).proofs is None


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
from standalone.typed import GenericEngine, GenericProgram, GenericRule, TypedAtom, TypedFact
from standalone.model import ReasoningLimits, Variable
x = Variable('x')
program = GenericProgram([GenericRule('r', [TypedAtom('p', [x])], [], TypedAtom('q', [x]))])
result = GenericEngine().evaluate(program, [TypedFact('p', ['a'])],
                                  limits=ReasoningLimits(), collect_proofs=True)
assert result.proofs.explain(TypedFact('q', ['a'])).nodes
assert not any(name.startswith(('gestaltdb', 'numpy')) for name in sys.modules)
"""
    subprocess.run([sys.executable, "-I", "-S", "-c", script], check=True, capture_output=True, text=True)


@pytest.mark.parametrize("mode", ["missing", "unrelated", "ungrounded"])
def test_comparison_evidence_must_match_every_rule_filter(mode):
    rule = GenericRule("adult", [TypedAtom("age", [X, Y])],
                       [Comparison(">=", Y, Literal("integer", 18))], TypedAtom("adult", [X]))
    result = GenericEngine().evaluate(GenericProgram([rule]),
        [TypedFact("age", ["a", Literal("integer", 30)])], limits=LIMITS, collect_proofs=True)
    node = result.proofs._by_fact[TypedFact("adult", ["a"])]
    checks = {"missing": (),
              "unrelated": (Comparison("==", Literal("integer", 1), Literal("integer", 1)),),
              "ungrounded": rule.filters}[mode]
    with pytest.raises(ReasoningError, match="comparison"):
        replace(node, comparisons=checks)


@pytest.mark.parametrize("mode", ["missing", "duplicate", "wrong-scope"])
def test_absence_evidence_must_cover_each_rule_negation(mode):
    rule = GenericRule("ok", [TypedAtom("person", [X])], [], TypedAtom("ok", [X]),
                       negated=[TypedAtom("banned", [X]), TypedAtom("suspended", [X])])
    program = GenericProgram([rule], closed=["banned", "suspended"])
    run = GenericEngine().evaluate_stratified(program, [TypedFact("person", ["a"])],
                                             limits=LIMITS, collect_proofs=True)
    node = run.proofs._by_fact[TypedFact("ok", ["a"])]
    checks = {"missing": node.absence[:1],
              "duplicate": (node.absence[0], node.absence[0]),
              "wrong-scope": (replace(node.absence[0], scope=("unrelated",)), node.absence[1])}
    with pytest.raises(ReasoningError, match="absence"):
        replace(node, absence=checks[mode])


def test_trace_rejects_same_id_with_a_different_rule_definition():
    original = GenericRule("r", [TypedAtom("p", [X])], [], TypedAtom("q", [X]))
    program = GenericProgram([original])
    result = GenericEngine().evaluate(program, [TypedFact("p", ["a"])],
                                      limits=LIMITS, collect_proofs=True)
    impostor = GenericRule("r", [TypedAtom("p", [X])], [], TypedAtom("other", [X]))
    forged = TypedFactWitness(TypedFact("other", ["a"]), impostor, (TypedFact("p", ["a"]),))
    with pytest.raises(ReasoningError, match="rule"):
        replace(result.proofs, nodes=(*[n for n in result.proofs.nodes if n.rule is None], forged))


def test_trace_rejects_absence_scope_different_from_program():
    rule = GenericRule("ok", [TypedAtom("person", [X])], [], TypedAtom("ok", [X]),
                       negated=[TypedAtom("banned", [X])])
    program = GenericProgram([rule], closed=["banned", "suspended"])
    run = GenericEngine().evaluate_stratified(program, [TypedFact("person", ["a"])],
                                             limits=LIMITS, collect_proofs=True)
    node = run.proofs._by_fact[TypedFact("ok", ["a"])]
    changed = replace(node, absence=(replace(node.absence[0], scope=("banned",)),))
    with pytest.raises(ReasoningError, match="scope"):
        replace(run.proofs, nodes=(*[n for n in run.proofs.nodes if n.rule is None], changed))


def test_trace_rejects_absence_contradicted_by_retained_fact():
    rule = GenericRule("ok", [TypedAtom("person", [X])], [], TypedAtom("ok", [X]),
                       negated=[TypedAtom("banned", [X])])
    program = GenericProgram([rule], closed=["banned"])
    run = GenericEngine().evaluate_stratified(program, [TypedFact("person", ["a"])],
                                             limits=LIMITS, collect_proofs=True)
    blocker = TypedFactWitness(TypedFact("banned", ["a"]), None)
    with pytest.raises(ReasoningError, match="absence"):
        replace(run.proofs, nodes=(*run.proofs.nodes, blocker), truncated=True)


def test_aggregate_metadata_must_match_the_rule():
    rule = AggregateRule("cnt", [TypedAtom("edge", [X, Y])], (X,),
                         (Aggregation("count", Y, N),), TypedAtom("deg", [X, N]))
    result = GenericEngine().evaluate(GenericProgram([], aggregates=[rule]),
        [TypedFact("edge", ["a", "x"])], limits=LIMITS, collect_proofs=True)
    node = result.proofs._by_fact[TypedFact("deg", ["a", Literal("integer", 1)])]
    with pytest.raises(ReasoningError, match="aggregate"):
        replace(node, aggregates=(replace(node.aggregates[0], op="sum"),))


@pytest.mark.parametrize("stratified", [False, True])
def test_proof_validation_does_not_rebuild_closure_set_per_membership(monkeypatch, stratified):
    def costly_contains(*args):
        pytest.fail("proof validation must index the closure once, not call __contains__ per edge")

    monkeypatch.setattr(GenericResult, "__contains__", costly_contains)
    engine = GenericEngine()
    evaluate = engine.evaluate_stratified if stratified else engine.evaluate
    result = evaluate(parent_program(), [TypedFact("parent", ["a", "b"])],
                      limits=LIMITS, collect_proofs=True)
    assert result.proofs is not None


@pytest.mark.parametrize("stratified", [False, True])
def test_truncated_proof_absence_is_checked_against_the_full_result(stratified):
    rule = GenericRule("ok", [TypedAtom("person", [X])], [], TypedAtom("ok", [X]),
                       negated=[TypedAtom("banned", [X])])
    program = GenericProgram([rule], closed=["banned"])
    run = GenericEngine().evaluate_stratified(program, [TypedFact("person", ["a"])],
                                             limits=LIMITS, collect_proofs=True)
    trace = replace(run.proofs, truncated=True)
    facts = (*run.result.facts, TypedFact("banned", ["a"]))
    with pytest.raises(ReasoningError, match="absence"):
        if stratified:
            replace(run, result=replace(run.result, facts=facts), proofs=trace)
        else:
            replace(run.result, facts=facts, proofs=trace)


@pytest.mark.parametrize("stratified", [False, True])
@pytest.mark.parametrize("max_edges", [1, 2, 3])
def test_edge_budgets_preserve_closure_with_filtered_derivations(stratified, max_edges):
    rule = GenericRule("adult", [TypedAtom("age", [X, Y])],
                       [Comparison(">=", Y, Literal("integer", 18))], TypedAtom("adult", [X]))
    program = GenericProgram([rule])
    facts = [TypedFact("age", ["a", Literal("integer", 30)])]
    engine = GenericEngine()
    evaluate = engine.evaluate_stratified if stratified else engine.evaluate
    full = evaluate(program, facts, limits=LIMITS)
    partial = evaluate(program, facts, limits=ReasoningLimits(max_proof_edges=max_edges),
                       collect_proofs=True)
    assert (partial.result.facts if stratified else partial.facts) == (
        full.result.facts if stratified else full.facts)
    assert partial.proofs.truncated == (max_edges < 2)


@pytest.mark.parametrize("mode", ["missing", "duplicate", "group", "operands", "extra-filter"])
def test_aggregate_evidence_covers_one_consistent_group(mode):
    total = Variable("total")
    rule = AggregateRule("stats", [TypedAtom("value", [X, Y])], (X,),
                         (Aggregation("count", Y, N), Aggregation("sum", Y, total)),
                         TypedAtom("stats", [X, N, total]))
    fact = TypedFact("value", ["a", Literal("integer", 3)])
    result = GenericEngine().evaluate(GenericProgram([], aggregates=[rule]), [fact],
                                      limits=LIMITS, collect_proofs=True)
    node = result.proofs._by_fact[TypedFact("stats", ["a", Literal("integer", 1), Literal("integer", 3)])]
    changes = {
        "missing": {"aggregates": node.aggregates[:1]},
        "duplicate": {"aggregates": (node.aggregates[0], node.aggregates[0])},
        "group": {"aggregates": (node.aggregates[0], replace(node.aggregates[1], group=("b",)))},
        "operands": {"aggregates": (node.aggregates[0], replace(node.aggregates[1], operands=(
            TypedFact("value", ["b", Literal("integer", 3)]),)))},
        "extra-filter": {"comparisons": (Comparison("==", Literal("integer", 1), Literal("integer", 1)),)},
    }
    with pytest.raises(ReasoningError, match="aggregate"):
        replace(node, **changes[mode])


def test_program_change_invalidates_the_trace_even_with_unchanged_inputs():
    facts = [TypedFact("parent", ["a", "b"])]
    program = parent_program()
    result = GenericEngine().evaluate(program, facts, limits=LIMITS, collect_proofs=True)
    changed = GenericProgram([replace(program.rules[0], head=TypedAtom("other", [Y]))])
    assert not result.proofs.is_current_for(changed, facts)
    assert result.proofs.is_current_for(program, facts)

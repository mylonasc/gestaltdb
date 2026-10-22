"""RE-10 scoped closed-world negation with exact lower-stratum anti-joins."""

import itertools

import pytest

from gestaltdb.reasoning.model import ReasoningCancelledError, ReasoningError, ReasoningLimitError, ReasoningLimits
from gestaltdb.reasoning.terms import Literal
from gestaltdb.reasoning.typed import (
    GenericEngine,
    GenericProgram,
    GenericResult,
    GenericRule,
    NegationWitness,
    StratifiedRun,
    TypedAtom,
    TypedFact,
)
from gestaltdb.reasoning.model import Variable


X, Y, Z = Variable("x"), Variable("y"), Variable("z")


def stratified(rules=(), facts=(), closed=(), **kwargs):
    return GenericEngine().evaluate_stratified(
        GenericProgram(list(rules), closed=list(closed)), list(facts),
        limits=ReasoningLimits(), **kwargs)


def test_scope_declaration_range_restriction_and_head_binding():
    good = GenericRule("ok", [TypedAtom("candidate", [X])], [],
                       TypedAtom("eligible", [X]), [TypedAtom("disqualified", [X])])
    assert good.negated[0].predicate == "disqualified"
    with pytest.raises(ReasoningError, match="constant"):
        GenericRule("bad", [TypedAtom("p", [X])], [], TypedAtom("q", [X]),
                    [TypedAtom(Variable("w"), [X])])
    with pytest.raises(ReasoningError, match="relational body"):
        GenericRule("bad", [TypedAtom("p", [X])], [], TypedAtom("q", [X]),
                    [TypedAtom("missing", [Z])])
    with pytest.raises(ReasoningError, match="unsafe rule"):
        GenericRule("bad", [TypedAtom("p", [X])], [], TypedAtom("q", [Z]),
                    [TypedAtom("missing", [X])])
    with pytest.raises(ReasoningError, match="closed-world declaration"):
        GenericProgram([good])
    with pytest.raises(ReasoningError, match="closed-world declaration"):
        GenericProgram([good], closed=["other"])
    with pytest.raises(ReasoningError, match="non-empty"):
        GenericProgram([], closed=[""])
    program = GenericProgram([good], closed=["disqualified", "disqualified"])
    assert program.closed == ("disqualified",)
    with pytest.raises(ReasoningError, match="stratified"):
        program.rules[0] and GenericEngine().evaluate(program, [], limits=ReasoningLimits())


def test_negative_cycles_fail_closed_at_construction():
    flip = GenericRule("flip", [TypedAtom("p", [X])], [], TypedAtom("q", [X]),
                       [TypedAtom("q", [X])])
    with pytest.raises(ReasoningError, match="own stratum"):
        GenericProgram([flip], closed=["p", "q"])
    a = GenericRule("a", [TypedAtom("p", [X])], [], TypedAtom("q", [X]), [TypedAtom("r", [X])])
    b = GenericRule("b", [TypedAtom("q", [X])], [], TypedAtom("r", [X]), [TypedAtom("p", [X])])
    with pytest.raises(ReasoningError, match="[Nn]egative dependency cycle"):
        GenericProgram([a, b], closed=["p", "q", "r"])


def test_anti_join_reads_completed_lower_strata_with_witnesses():
    eligible = GenericRule("eligible", [TypedAtom("candidate", [X])], [],
                           TypedAtom("eligible", [X]), [TypedAtom("disqualified", [X])])
    facts = [TypedFact("candidate", ["a"]), TypedFact("candidate", ["b"]),
             TypedFact("disqualified", ["b"])]
    run = stratified([eligible], facts, closed=["disqualified"], collect_negation_trace=True)
    assert isinstance(run, StratifiedRun)
    assert TypedFact("eligible", ["a"]) in run.result
    assert TypedFact("eligible", ["b"]) not in run.result
    assert run.result.iterations == 1
    assert run.strata[0] != run.strata[-1]
    assert not run.truncated and len(run.witnesses) == 1
    witness = run.witnesses[0]
    assert isinstance(witness, NegationWitness)
    assert (witness.rule_id, witness.predicate, witness.args) == ("eligible", "disqualified", ("b",))
    assert witness.scope == ("disqualified",)
    assert witness.horizon == run.witnesses[0].horizon and len(witness.horizon) == 71
    assert stratified([eligible], facts, closed=["disqualified"]).witnesses == ()
    # Deterministic regardless of input order.
    assert stratified([eligible], list(reversed(facts)), closed=["disqualified"]).result == run.result


def _reference(program, facts):
    closure = set(facts)
    # Independently compute numerical ranks, rather than reuse the engine's
    # positive-SCC schedule. Negative dependencies require a strictly lower rank.
    predicates = {atom.predicate for rule in program.rules
                  for atom in (*rule.body, *rule.negated, rule.head)}
    ranks = dict.fromkeys(predicates, 0)
    for _ in range(len(predicates) + 1):
        before = ranks.copy()
        for rule in program.rules:
            required = [ranks[atom.predicate] for atom in rule.body]
            required += [ranks[atom.predicate] + 1 for atom in rule.negated]
            ranks[rule.head.predicate] = max(ranks[rule.head.predicate], *required)
        if ranks == before:
            break
    else:
        raise AssertionError("negative dependency cycle in reference input")
    universe = sorted({value for fact in facts for value in fact.values},
                      key=lambda v: (isinstance(v, str), str(v)))
    for rule in program.rules:
        universe.extend(term for atom in (*rule.body, *rule.negated, rule.head)
                        for term in atom.args if isinstance(term, (str, Literal)))
        universe.extend(term for item in rule.filters for term in (item.left, item.right) if isinstance(term, Literal))
    universe = list(dict.fromkeys(universe))
    variables = sorted({term.name for rule in program.rules for atom in (*rule.body, *rule.negated)
                        for term in atom.args if isinstance(term, Variable)})
    for rank in sorted(set(ranks.values())):
        while True:
            added = set()
            for rule in program.rules:
                if ranks[rule.head.predicate] != rank:
                    continue
                for combo in itertools.product(universe, repeat=len(variables)):
                    named = {Variable(name): value for name, value in zip(variables, combo)}
                    ground = []
                    for atom in rule.body:
                        values = tuple(named.get(term, term) if isinstance(term, Variable) else term
                                       for term in atom.args)
                        if any(isinstance(value, Variable) for value in values):
                            break
                        ground.append(TypedFact(atom.predicate, values))
                    else:
                        if not all(grounded in closure for grounded in ground):
                            continue
                        if not all(item.holds(named) for item in rule.filters):
                            continue
                        blocked = False
                        for atom in rule.negated:
                            values = tuple(named.get(term, term) if isinstance(term, Variable) else term
                                           for term in atom.args)
                            if any(isinstance(value, Variable) for value in values):
                                raise AssertionError("unbound negation in oracle")
                            if TypedFact(atom.predicate, values) in closure:
                                blocked = True
                                break
                        if blocked:
                            continue
                        head = tuple(named.get(term, term) if isinstance(term, Variable) else term
                                     for term in rule.head.args)
                        if not any(isinstance(value, Variable) for value in head):
                            added.add(TypedFact(rule.head.predicate, head))
            if added <= closure:
                break
            closure.update(added)
    return closure


def test_stratified_closure_matches_independent_reference_and_recomputes():
    rules = [
        GenericRule("reach", [TypedAtom("edge", [X, Y])], [], TypedAtom("reach", [X, Y])),
        GenericRule("step", [TypedAtom("reach", [X, Y]), TypedAtom("edge", [Y, Z])], [],
                    TypedAtom("reach", [X, Z])),
        GenericRule("free", [TypedAtom("node", [X])], [], TypedAtom("free", [X]),
                    [TypedAtom("blocked", [X])]),
    ]
    program = GenericProgram(rules, closed=["blocked"])
    order = [list(component) for component in program.strata]
    assert order == [["blocked"], ["edge"], ["node"], ["free"], ["reach"]]
    position = {name: index for index, component in enumerate(order) for name in component}
    assert position["blocked"] < position["free"] and position["edge"] < position["reach"]
    facts = [TypedFact("edge", ["a", "b"]), TypedFact("edge", ["b", "c"]),
             TypedFact("node", ["a"]), TypedFact("node", ["b"]), TypedFact("node", ["c"]),
             TypedFact("blocked", ["b"])]
    run = GenericEngine().evaluate_stratified(program, facts, limits=ReasoningLimits())
    assert set(run.result.facts) == _reference(program, facts)
    assert TypedFact("free", ["a"]) in run.result and TypedFact("free", ["b"]) not in run.result
    assert TypedFact("reach", ["a", "c"]) in run.result
    # Base insert/delete recomputation agrees on the updated horizon.
    extended = GenericEngine().evaluate_stratified(
        program, facts + [TypedFact("blocked", ["c"])], limits=ReasoningLimits())
    assert TypedFact("free", ["c"]) not in extended.result
    assert set(extended.result.facts) == _reference(program, facts + [TypedFact("blocked", ["c"])])
    shrunk = [fact for fact in facts if fact != TypedFact("edge", ["b", "c"])]
    removed = GenericEngine().evaluate_stratified(program, shrunk, limits=ReasoningLimits())
    assert TypedFact("reach", ["a", "c"]) not in removed.result
    assert set(removed.result.facts) == _reference(program, shrunk)


def test_compliance_example_end_to_end():
    eligible = GenericRule("eligible-vendor",
                           [TypedAtom("vendor", [X]), TypedAtom("turnover", [X, Y])],
                           [], TypedAtom("eligible", [X]), [TypedAtom("sanctioned", [X])])
    facts = [TypedFact("vendor", ["acme"]), TypedFact("turnover", ["acme", Literal("integer", 9000)]),
             TypedFact("vendor", ["globex"]), TypedFact("turnover", ["globex", Literal("integer", 1200)]),
             TypedFact("sanctioned", ["globex"])]
    run = GenericEngine().evaluate_stratified(
        GenericProgram([eligible], closed=["sanctioned"]), facts, limits=ReasoningLimits())
    assert TypedFact("eligible", ["acme"]) in run.result
    assert TypedFact("eligible", ["globex"]) not in run.result
    assert run.result == GenericEngine().evaluate_stratified(
        GenericProgram([eligible], closed=["sanctioned", "sanctioned"]), facts,
        limits=ReasoningLimits()).result


@pytest.mark.parametrize("nested", [False, True])
def test_reference_completes_derived_blockers_before_testing_absence(nested):
    rules = [
        GenericRule("block", [TypedAtom("p", [X])], [], TypedAtom("blocked", [X])),
        GenericRule("free", [TypedAtom("p", [X])], [], TypedAtom("free", [X]),
                    [TypedAtom("blocked", [X])]),
    ]
    if nested:
        rules.append(GenericRule("eligible", [TypedAtom("p", [X])], [], TypedAtom("eligible", [X]),
                                 [TypedAtom("free", [X])]))
    program = GenericProgram(rules, closed=["blocked", "free"])
    facts = [TypedFact("p", ["a"])]
    expected = {TypedFact("p", ["a"]), TypedFact("blocked", ["a"])}
    if nested:
        expected.add(TypedFact("eligible", ["a"]))
    assert _reference(program, facts) == expected
    assert set(GenericEngine().evaluate_stratified(program, facts, limits=ReasoningLimits()).result.facts) == expected


def test_limits_cancellation_and_plain_evaluation_parity():
    rule = GenericRule("copy", [TypedAtom("p", [X])], [], TypedAtom("q", [X]),
                       [TypedAtom("missing", [X])])
    program = GenericProgram([rule], closed=["missing"])
    with pytest.raises(ReasoningError, match="stratified"):
        GenericEngine().evaluate(program, [], limits=ReasoningLimits())
    with pytest.raises(ReasoningLimitError, match="max_iterations"):
        chain = GenericProgram([GenericRule("t", [TypedAtom("p", [X, Y]), TypedAtom("p", [Y, Z])],
                                              [], TypedAtom("p", [X, Z]))])
        GenericEngine().evaluate_stratified(chain, [TypedFact("p", ["a", "b"]), TypedFact("p", ["b", "c"])],
                                            limits=ReasoningLimits(max_iterations=1))
    with pytest.raises(ReasoningCancelledError):
        GenericEngine().evaluate_stratified(program, [], limits=ReasoningLimits(), cancel=lambda: True)
    with pytest.raises(ReasoningError):
        GenericEngine().evaluate_stratified(program, [], limits=ReasoningLimits(), cancel="yes")
    plain = GenericProgram([GenericRule("copy", [TypedAtom("p", [X])], [], TypedAtom("q", [X]))])
    facts = [TypedFact("p", ["a"]), TypedFact("p", ["b"])]
    assert (GenericEngine().evaluate_stratified(plain, facts, limits=ReasoningLimits()).result.facts
            == GenericEngine().evaluate(plain, facts, limits=ReasoningLimits()).facts)
    assert GenericEngine().evaluate_stratified(GenericProgram([]), [], limits=ReasoningLimits()).result.facts == ()
    assert len(GenericEngine().evaluate_stratified(
        program, [TypedFact("p", ["a"])], limits=ReasoningLimits()).result.facts) == 2

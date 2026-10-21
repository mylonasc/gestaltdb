"""RE-07 M2 generic typed engine: n-ary relations, literals and filter pruning."""

import itertools

import pytest

from gestaltdb.reasoning.model import ReasoningCancelledError, ReasoningError, ReasoningLimitError, ReasoningLimits
from gestaltdb.reasoning.terms import Literal, compare
from gestaltdb.reasoning.typed import (
    AggregateRule,
    Aggregation,
    Comparison,
    GenericEngine,
    GenericProgram,
    GenericResult,
    GenericRule,
    MultiHeadRule,
    TypedAtom,
    TypedFact,
    expand_predicate_variables,
    normalize_program,
    predicate_domain,
)
from gestaltdb.reasoning.model import Variable


X, Y, Z = Variable("x"), Variable("y"), Variable("z")


def evaluate(rules=(), facts=(), collect_proofs=False, **limits):
    return GenericEngine().evaluate(GenericProgram(rules), facts, limits=ReasoningLimits(**limits),
                                    collect_proofs=collect_proofs)


def test_typed_rule_validation_is_range_restricted_and_predicate_safe():
    good = GenericRule("r", [TypedAtom("p", [X, Literal("integer", 1)])],
                       [Comparison(">=", X, Literal("integer", 0))], TypedAtom("q", [X]))
    assert good.filters[0].variables == frozenset([X])
    with pytest.raises(ReasoningError, match="filter variables"):
        GenericRule("r", [TypedAtom("p", [X])], [Comparison("<", Y, Literal("integer", 1))], TypedAtom("q", [X]))
    with pytest.raises(ReasoningError, match="head variables"):
        GenericRule("r", [TypedAtom("p", [X])], [], TypedAtom("q", [Y]))
    template = GenericRule("t", [TypedAtom(Variable("w"), [X, Y])], [], TypedAtom("q", [X]))
    with pytest.raises(ReasoningError, match="unexpanded"):
        GenericProgram([template])
    with pytest.raises(ReasoningError, match="not also occur as a term"):
        GenericRule("bad", [TypedAtom(Variable("w"), [X, Variable("w")])], [], TypedAtom("q", [X]))
    with pytest.raises(ReasoningError):
        GenericRule("r", [], [], TypedAtom("q", [X]))
    with pytest.raises(ReasoningError, match="op"):
        Comparison("=~", X, Literal("integer", 1))
    with pytest.raises(ReasoningError, match="unbound"):
        Comparison("<", X, Literal("integer", 1)).holds({})
    with pytest.raises(ReasoningError, match="Variable or Literal"):
        Comparison("<", X, "not-a-literal")
    assert Comparison("<", X, Literal("integer", 1)).holds({X: "entity"}) is False
    assert Comparison("!=", X, Literal("integer", 1)).holds({X: "entity"}) is False
    assert Comparison("<", X, X).variables == frozenset([X])
    with pytest.raises(ReasoningError, match="unique"):
        GenericProgram([good, good])
    assert GenericProgram([good]).digest != GenericProgram([]).digest


def test_literal_terms_match_exactly_and_filters_prune():
    facts = [TypedFact("age", ["alice", Literal("integer", 30)]),
             TypedFact("age", ["bob", Literal("integer", 12)]),
             TypedFact("age", ["carol", Literal("text", "old")])]
    adult = GenericRule("adult", [TypedAtom("age", [X, Y])],
                        [Comparison(">=", Y, Literal("integer", 18))], TypedAtom("adult", [X]))
    result = evaluate([adult], facts)
    assert TypedFact("adult", ["alice"]) in result
    assert TypedFact("adult", ["bob"]) not in result
    assert TypedFact("adult", ["carol"]) not in result
    assert len(result.facts) == len(facts) + 1
    assert result.iterations == 1
    assert result.program_digest == GenericProgram([adult]).digest
    assert TypedFact("adult", ["alice"]) in result
    assert TypedFact("adult", ["bob"]) not in result


def test_witness_rejection_limits_cancellation_and_input_validation():
    rule = GenericRule("copy", [TypedAtom("p", [X])], [], TypedAtom("q", [X]))
    with pytest.raises(ReasoningError, match="witness"):
        evaluate([rule], [TypedFact("p", ["a"])], collect_proofs=True)
    with pytest.raises(ReasoningCancelledError):
        GenericEngine().evaluate(GenericProgram([rule]), [TypedFact("p", ["a"])],
                                 limits=ReasoningLimits(), cancel=lambda: True)
    with pytest.raises(ReasoningLimitError, match="max_facts"):
        evaluate([rule], [TypedFact("p", ["a"])], max_facts=1)
    with pytest.raises(ReasoningError):
        evaluate([rule], ["not-a-fact"])
    with pytest.raises(ReasoningError):
        GenericEngine().evaluate([], [], limits=ReasoningLimits())
    assert evaluate([], []).facts == ()


def test_multi_head_normalization_preserves_closure_and_rejects_bad_heads():
    multi = MultiHeadRule("both", [TypedAtom("p", [X, Y])], [],
                          [TypedAtom("q", [X, Y]), TypedAtom("r", [Y, X])])
    singles = multi.normalize()
    assert [rule.rule_id for rule in singles] == ["both#0", "both#1"]
    assert all(isinstance(rule, GenericRule) for rule in singles)
    facts = [TypedFact("p", ["a", "b"])]
    assert set(evaluate(singles, facts).facts) == set(
        evaluate(normalize_program([multi]).rules, facts).facts)
    assert TypedFact("q", ["a", "b"]) in evaluate(normalize_program([multi]).rules, facts)
    assert TypedFact("r", ["b", "a"]) in evaluate(normalize_program([multi]).rules, facts)
    mixed = normalize_program([multi, GenericRule("id", [TypedAtom("p", [X])], [], TypedAtom("s", [X]))])
    assert len(mixed.rules) == 3
    with pytest.raises(ReasoningError, match="at least one head"):
        MultiHeadRule("empty", [TypedAtom("p", [X])], [], [])
    with pytest.raises(ReasoningError, match="repeat"):
        MultiHeadRule("dup", [TypedAtom("p", [X])], [], [TypedAtom("q", [X]), TypedAtom("q", [X])])
    with pytest.raises(ReasoningError, match="head variables"):
        MultiHeadRule("bad", [TypedAtom("p", [X])], [], [TypedAtom("q", [X]), TypedAtom("r", [Z])]).normalize()
    with pytest.raises(ReasoningError, match="unique"):
        normalize_program([multi, multi])
    with pytest.raises(ReasoningError):
        normalize_program(["not-a-rule"])
    with pytest.raises(ReasoningError):
        normalize_program(None)


def test_aggregate_stratum_computes_grouped_statistics_once():
    W, T, U = Variable("w"), Variable("t"), Variable("u")
    facts = [TypedFact("emp", ["a", Literal("integer", 30)]),
             TypedFact("emp", ["b", Literal("integer", 12)]),
             TypedFact("emp", ["c", Literal("integer", 24)])]
    program = GenericProgram(
        [],
        [AggregateRule("stats", [TypedAtom("emp", [X, Y])], [],
                       [Aggregation("count", X, W), Aggregation("sum", Y, T), Aggregation("min", Y, U)],
                       TypedAtom("stats", [W, T, U]))],
    )
    assert program.digest != GenericProgram([], []).digest
    result = GenericEngine().evaluate(program, facts, limits=ReasoningLimits())
    assert TypedFact("stats", [Literal("integer", 3), Literal("integer", 66), Literal("integer", 12)]) in result
    assert result.iterations == 1

def test_aggregates_are_stratified_single_pass_and_type_strict():
    T = Variable("t")
    facts = [TypedFact("sale", ["store-a", Literal("integer", 10)]),
             TypedFact("sale", ["store-a", Literal("integer", 20)]),
             TypedFact("sale", ["store-b", Literal("integer", 5)]),
             TypedFact("sale", ["store-c", Literal("null", None)])]
    totals = AggregateRule("totals", [TypedAtom("sale", [X, Y])], [X],
                           [Aggregation("sum", Y, T), Aggregation("max", Y, Z)],
                           TypedAtom("total", [X, T, Z]))
    counts = AggregateRule("counts", [TypedAtom("sale", [X, Y])], [X],
                           [Aggregation("count", Y, T)], TypedAtom("n", [X, T]))
    result = GenericEngine().evaluate(GenericProgram([], [totals, counts]), facts,
                                      limits=ReasoningLimits())
    assert TypedFact("total", ["store-a", Literal("integer", 30), Literal("integer", 20)]) in result
    assert TypedFact("total", ["store-b", Literal("integer", 5), Literal("integer", 5)]) in result
    assert TypedFact("n", ["store-a", Literal("integer", 2)]) in result
    assert TypedFact("n", ["store-c", Literal("integer", 1)]) in result
    assert not [fact for fact in result.facts if fact.predicate == "total" and fact.values[0] == "store-c"]
    assert result.iterations == 1
    assert evaluate([], []).iterations == 0
    with pytest.raises(ReasoningError, match="sink"):
        GenericProgram([GenericRule("back", [TypedAtom("total", [X])], [], TypedAtom("loop", [X]))],
                       [totals])
    with pytest.raises(ReasoningError, match="non-recursive"):
        GenericProgram([], [totals, AggregateRule("other", [TypedAtom("total", [X, Y, Z])], [X],
                                                   [Aggregation("count", X, T)], TypedAtom("other", [X, T]))])
    with pytest.raises(ReasoningError, match="count/sum/min/max"):
        AggregateRule("bad", [TypedAtom("sale", [X, Y])], [X],
                      [Aggregation("bogus", Y, T)], TypedAtom("bad", [X, T]))
    with pytest.raises(ReasoningError, match="distinct|fresh|source|group|unsafe"):
        AggregateRule("bad", [TypedAtom("sale", [X, Y])], [Z],
                      [Aggregation("count", Y, T)], TypedAtom("bad", [X, T]))
    bad_text = GenericProgram([], [AggregateRule("bad", [TypedAtom("sale", [X, Y])], [X],
                                                  [Aggregation("sum", Y, T)], TypedAtom("bad", [X, T]))])
    with pytest.raises(ReasoningError, match="integer/float"):
        GenericEngine().evaluate(bad_text, [TypedFact("sale", ["s", Literal("text", "x")])],
                                 limits=ReasoningLimits())
    with pytest.raises(ReasoningError, match="NaN"):
        GenericEngine().evaluate(bad_text, [TypedFact("sale", ["s", Literal("float", float("nan"))])],
                                 limits=ReasoningLimits())
    with pytest.raises(ReasoningError, match="unique"):
        GenericProgram([], [totals, totals])
    with pytest.raises(ReasoningError, match="aggregates must contain"):
        GenericProgram([], ["not-a-rule"])


def test_controlled_predicate_variables_expand_over_an_explicit_domain():
    P = Variable("rel")
    template = GenericRule("copy", [TypedAtom(P, [X, Y])], [], TypedAtom("mirror", [X, Y]))
    facts = [TypedFact("knows", ["a", "b"]), TypedFact("likes", ["b", "c"])]
    assert predicate_domain([template], facts) == ("knows", "likes", "mirror")
    expanded = expand_predicate_variables(template, predicate_domain([template], facts))
    assert [rule.rule_id for rule in expanded] == ["copy[rel=knows]", "copy[rel=likes]", "copy[rel=mirror]"]
    program = GenericProgram(expanded)
    result = GenericEngine().evaluate(program, facts, limits=ReasoningLimits())
    assert TypedFact("mirror", ["a", "b"]) in result
    assert TypedFact("mirror", ["b", "c"]) in result
    assert len([fact for fact in result.facts if fact.predicate == "mirror"]) == 2
    assert expand_predicate_variables(
        GenericRule("plain", [TypedAtom("p", [X])], [], TypedAtom("q", [X])), ())[0].rule_id == "plain"
    with pytest.raises(ReasoningError, match="unexpanded"):
        GenericProgram([template])
    with pytest.raises(ReasoningError, match="non-empty domain"):
        expand_predicate_variables(template, [])
    with pytest.raises(ReasoningError, match="domain predicate"):
        expand_predicate_variables(template, ["ok", 42])
    with pytest.raises(ReasoningError, match="not also occur as a term"):
        GenericRule("bad", [TypedAtom(P, [X, P])], [], TypedAtom("q", [X]))
    with pytest.raises(ReasoningError, match="iterable"):
        expand_predicate_variables(template, None)
    with pytest.raises(ReasoningError):
        predicate_domain("not-rules", facts)
    with pytest.raises(ReasoningError):
        predicate_domain([template], ["not-a-fact"])


def test_nary_recursion_and_repeated_atoms_match_independent_grounding():
    facts = [TypedFact("link", ["a", "b", "c"]), TypedFact("link", ["b", "c", "a"]),
             TypedFact("link", ["c", "a", "b"])]
    chain = GenericRule("chain", [TypedAtom("link", [X, Y, Z]), TypedAtom("link", [Y, Z, X])],
                        [], TypedAtom("meet", [X, Y, Z]))
    result = evaluate([chain], facts)
    assert TypedFact("meet", ["a", "b", "c"]) in result
    assert TypedFact("meet", ["b", "c", "a"]) in result
    assert TypedFact("meet", ["c", "a", "b"]) in result
    assert len(result.facts) == len(facts) + 3

    program = GenericProgram([GenericRule("t", [TypedAtom("p", [X, Y]), TypedAtom("p", [Y, Z])],
                                          [], TypedAtom("p", [X, Z]))])
    inputs = [TypedFact("p", ["a", "b"]), TypedFact("p", ["b", "c"]), TypedFact("p", ["c", "a"])]
    assert set(evaluate(program.rules, inputs).facts) == _typed_reference(program, inputs)
    assert isinstance(evaluate(program.rules, inputs), GenericResult)


def test_generated_typed_programs_match_independent_grounding():
    import random

    rng = random.Random(20261022)
    entities = ["a", "b"]
    literals = [Literal("integer", 0), Literal("integer", 1), Literal("text", "t")]
    for case in range(40):
        inputs = [TypedFact(rng.choice("pq"), [rng.choice(entities + [rng.choice(literals)])
                                                if rng.random() < 0.5 else rng.choice(entities)
                                                for _ in range(rng.randint(1, 2))])
                  for _ in range(5)]
        terms = [X, Y] + entities + literals
        rules = []
        for number in range(3):
            arity = rng.randint(1, 2)
            body = [TypedAtom(rng.choice("pq"), [rng.choice(terms) for _ in range(arity)])
                    for _ in range(rng.randint(1, 2))]
            bound = set().union(*(atom.variables for atom in body))
            if not bound:
                continue
            heads = entities + sorted(bound, key=lambda v: v.name)
            head = TypedAtom(rng.choice("pq"), [rng.choice(heads) for _ in range(arity)])
            filters = []
            if bound and rng.random() < 0.5:
                left = rng.choice(sorted(bound, key=lambda v: v.name))
                filters = [Comparison(rng.choice(["==", "!=", "<", ">="]), left, rng.choice(literals))]
            try:
                rules.append(GenericRule(str(number), body, filters, head))
            except ReasoningError:
                continue
        program = GenericProgram(rules)
        result = evaluate(program.rules, inputs)
        assert set(result.facts) == _typed_reference(program, inputs), case
        assert result == evaluate(program.rules, list(reversed(inputs)) + inputs)


def _typed_reference(program, inputs):
    closure = set(inputs)
    universe = [value for fact in inputs for value in fact.values]
    for rule in program.rules:
        for atom in (*rule.body, rule.head):
            universe.extend(term for term in atom.args if isinstance(term, (str, Literal)))
    universe = list(dict.fromkeys(universe))
    while True:
        added = set()
        for rule in program.rules:
            variables = sorted({term.name for atom in rule.body for term in atom.args
                                if isinstance(term, Variable)} |
                               {term.name for item in rule.filters for term in (item.left, item.right)
                                if isinstance(term, Variable)})
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
                    if all(g in closure for g in ground) and all(item.holds(named) for item in rule.filters):
                        head = tuple(named.get(term, term) if isinstance(term, Variable) else term
                                     for term in rule.head.args)
                        if not any(isinstance(value, Variable) for value in head):
                            added.add(TypedFact(rule.head.predicate, head))
        if added <= closure:
            return closure
        closure.update(added)

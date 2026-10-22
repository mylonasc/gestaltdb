"""RE-16 DRed-style deletion, rule-change invalidation and affected-work measurement."""

import random

import pytest

from gestaltdb.reasoning.maintenance import (
    Materialization,
    RetractionResult,
    Support,
    build_index,
    propagate_insertions,
    rematerialize,
    retract_facts,
)
from gestaltdb.reasoning.model import (
    ReasoningCancelledError,
    ReasoningError,
    ReasoningLimitError,
    ReasoningLimits,
    Variable,
)
from gestaltdb.reasoning.typed import GenericEngine, GenericProgram, GenericRule, TypedAtom, TypedFact


X, Y, Z = Variable("x"), Variable("y"), Variable("z")


def chain_program():
    return GenericProgram([
        GenericRule("base", [TypedAtom("edge", [X, Y])], [], TypedAtom("reach", [X, Y])),
        GenericRule("step", [TypedAtom("reach", [X, Y]), TypedAtom("edge", [Y, Z])], [],
                    TypedAtom("reach", [X, Z])),
    ])


def test_rematerialize_discovers_complete_supports():
    prog = chain_program()
    base = [TypedFact("edge", ["a", "b"]), TypedFact("edge", ["b", "c"])]
    material = rematerialize(prog, base, limits=ReasoningLimits())
    assert isinstance(material, Materialization)
    assert set(material.facts) == set(GenericEngine().evaluate(prog, base, limits=ReasoningLimits()).facts)
    assert TypedFact("reach", ["a", "c"]) in material.facts
    step = [item for item in material.supports
            if item.fact == TypedFact("reach", ["a", "c"]) and item.rule_id == "step"]
    assert len(step) == 1 and len(step[0].premises) == 2
    assert material.index.program_digest == prog.digest
    assert material == rematerialize(prog, list(reversed(base)), limits=ReasoningLimits())
    with pytest.raises(ReasoningError):
        rematerialize(prog, ["not-a-fact"], limits=ReasoningLimits())
    with pytest.raises(ReasoningError):
        rematerialize("not-a-program", base, limits=ReasoningLimits())
    with pytest.raises(ReasoningError):
        rematerialize(prog, base, limits=ReasoningLimits(), max_supports=True)


def test_final_support_loss_removes_while_duplicates_survive():
    prog = chain_program()
    base = [TypedFact("edge", ["a", "b"]), TypedFact("edge", ["b", "c"]), TypedFact("edge", ["a", "c"])]
    material = rematerialize(prog, base, limits=ReasoningLimits())
    # reach(a,c) has two independent supports: direct edge and two-hop path.
    assert len(material.index.supports_for(TypedFact("reach", ["a", "c"]))) == 2
    first = retract_facts(prog, material.facts, [TypedFact("edge", ["a", "c"])],
                          limits=ReasoningLimits(), index=material.index)
    assert isinstance(first, RetractionResult)
    assert TypedFact("reach", ["a", "c"]) in first.facts  # two-hop support survives
    assert TypedFact("edge", ["a", "c"]) in first.removed
    assert len(first.index.supports_for(TypedFact("reach", ["a", "c"]))) == 1
    second = retract_facts(prog, first.facts, [TypedFact("edge", ["b", "c"])],
                           limits=ReasoningLimits(), index=first.index)
    assert TypedFact("reach", ["a", "c"]) not in second.facts  # final support lost
    assert TypedFact("reach", ["b", "c"]) not in second.facts
    assert TypedFact("reach", ["a", "b"]) in second.facts
    assert len(second.facts) == len(set(second.facts))  # canonical, deduplicated
    assert set(second.removed) >= {TypedFact("edge", ["b", "c"]), TypedFact("reach", ["a", "c"])}
    assert second.overdeleted >= len(second.removed) and second.rederived >= 0


def test_recursive_cycles_need_overdeletion_not_counts():
    prog = GenericProgram([
        GenericRule("base", [TypedAtom("edge", [X, Y])], [], TypedAtom("reach", [X, Y])),
        GenericRule("step", [TypedAtom("reach", [X, Y]), TypedAtom("edge", [Y, Z])], [],
                    TypedAtom("reach", [X, Z])),
    ])
    base = [TypedFact("edge", ["a", "b"]), TypedFact("edge", ["b", "a"])]
    material = rematerialize(prog, base, limits=ReasoningLimits())
    assert {f.values for f in material.facts if f.predicate == "reach"} == {
        ("a", "b"), ("b", "a"), ("a", "a"), ("b", "b")}
    out = retract_facts(prog, material.facts, [TypedFact("edge", ["a", "b"])],
                        limits=ReasoningLimits(), index=material.index)
    expected = set(GenericEngine().evaluate(
        prog, [TypedFact("edge", ["b", "a"])], limits=ReasoningLimits()).facts)
    assert set(out.facts) == expected
    assert TypedFact("reach", ["a", "a"]) not in out.facts  # cycle collapses exactly
    assert TypedFact("reach", ["b", "a"]) in out.facts
    assert out.overdeleted > len(out.removed)  # over-approximation happened, then rederivation


def test_randomized_oracle_parity_for_insert_delete_sequences():
    rng = random.Random(20261023)
    for case in range(30):
        domain = [f"e{i}" for i in range(4)]
        rules = [GenericRule("base", [TypedAtom("p", [X, Y])], [], TypedAtom("q", [X, Y])),
                 GenericRule("step", [TypedAtom("q", [X, Y]), TypedAtom("p", [Y, Z])], [],
                             TypedAtom("q", [X, Z]))]
        prog = GenericProgram(rules)
        base = [TypedFact("p", [rng.choice(domain), rng.choice(domain)]) for _ in range(6)]
        material = rematerialize(prog, base, limits=ReasoningLimits())
        assert set(material.facts) == set(
            GenericEngine().evaluate(prog, base, limits=ReasoningLimits()).facts)
        live = list(dict.fromkeys(base))
        current, index = material.facts, material.index
        for _ in range(3):
            if rng.random() < 0.5:
                insert = TypedFact("p", [rng.choice(domain), rng.choice(domain)])
                grown = propagate_insertions(prog, list(current), [insert],
                                             limits=ReasoningLimits(), index=index)
                live = list(dict.fromkeys(live + [insert]))
                current, index = grown.facts, grown.index
            else:
                victim = rng.choice(live) if live else TypedFact("p", ["ghost"])
                shrunk = retract_facts(prog, list(current), [victim],
                                       limits=ReasoningLimits(), index=index)
                live = [fact for fact in live if fact != victim]
                current, index = shrunk.facts, shrunk.index
            assert set(current) == set(GenericEngine().evaluate(
                prog, live, limits=ReasoningLimits()).facts), case


def test_rule_changes_negation_and_budgets_fail_closed():
    prog = chain_program()
    base = [TypedFact("edge", ["a", "b"])]
    material = rematerialize(prog, base, limits=ReasoningLimits())
    changed = GenericProgram(list(prog.rules) + [
        GenericRule("extra", [TypedAtom("edge", [X, Y])], [], TypedAtom("loop", [X, Y]))])
    with pytest.raises(ReasoningError, match="rematerialize"):
        retract_facts(changed, material.facts, [TypedFact("edge", ["a", "b"])],
                      limits=ReasoningLimits(), index=material.index)
    fresh = rematerialize(changed, base, limits=ReasoningLimits())
    assert TypedFact("loop", ["a", "b"]) in fresh.facts
    negated = GenericProgram([GenericRule("n", [TypedAtom("p", [X])], [], TypedAtom("q", [X]),
                                          [TypedAtom("m", [X])])], closed=["m"])
    with pytest.raises(ReasoningError, match="negation"):
        retract_facts(negated, [], [TypedFact("p", ["a"])], limits=ReasoningLimits(),
                      index=build_index(chain_program(), [], []))
    with pytest.raises(ReasoningError, match="explicit DependencyIndex"):
        retract_facts(prog, material.facts, [TypedFact("edge", ["a", "b"])],
                      limits=ReasoningLimits(), index=None)
    with pytest.raises(ReasoningLimitError, match="max_supports"):
        two = rematerialize(prog, [TypedFact("edge", ["a", "b"]), TypedFact("edge", ["b", "c"])],
                            limits=ReasoningLimits())
        assert len(two.supports) > 1
        retract_facts(prog, two.facts, [TypedFact("edge", ["zzz"])],
                      limits=ReasoningLimits(), index=two.index, max_supports=1)
    with pytest.raises(ReasoningCancelledError):
        retract_facts(prog, material.facts, [TypedFact("edge", ["a", "b"])],
                      limits=ReasoningLimits(), index=material.index, cancel=lambda: True)
    with pytest.raises(ReasoningError):
        retract_facts(prog, ["not-a-fact"], [], limits=ReasoningLimits(), index=material.index)


def test_affected_work_scales_with_change_not_closure():
    prog = chain_program()
    left = [TypedFact("edge", [f"a{i}", f"a{i+1}"]) for i in range(12)]
    right = [TypedFact("edge", [f"b{i}", f"b{i+1}"]) for i in range(12)]
    material = rematerialize(prog, left + right, limits=ReasoningLimits())
    out = retract_facts(prog, material.facts, [TypedFact("edge", ["a0", "a1"])],
                        limits=ReasoningLimits(), index=material.index)
    assert TypedFact("edge", ["a0", "a1"]) in out.removed
    assert not any(fact.values[0][0] == "b" for fact in out.removed if fact.predicate == "edge")
    assert TypedFact("reach", ["b0", "b12"]) in out.facts
    assert TypedFact("reach", ["a0", "a12"]) not in out.facts
    # Work touches the affected chain; the untouched chain is never overdeleted.
    assert out.overdeleted < len(material.facts)
    assert out.work < 10 * len(material.facts)

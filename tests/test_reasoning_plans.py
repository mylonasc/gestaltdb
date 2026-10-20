"""RE-04 SCC/identity invariants, independent closure and ordered proof evidence."""

from dataclasses import FrozenInstanceError, replace
import itertools
import json
import random

import pytest

from gestaltdb.reasoning import Atom, DatalogEngine, Fact, Program, ReasoningError, ReasoningLimitError, ReasoningLimits, Rule, Variable
from gestaltdb.reasoning.plans import PlannedDatalogEngine, PreparedProgram
from gestaltdb.reasoning.protocols import Evidence, InputContext
from gestaltdb.reasoning.relational import CompilationIdentity
from tests.test_reasoning_engine import _reference


X, Y, Z = Variable("x"), Variable("y"), Variable("z")
CONTEXT = InputContext("horizon", "world", "ontology", "policy", "mapping")


def prepare(rules=(), **options):
    program = Program(rules)
    identity = CompilationIdentity(program.digest, CONTEXT, "external-strings-v1",
                                   "positive-binary-entity-v1", "indexed-scc-v1")
    return PreparedProgram(program, identity, **options)


def evaluate(plan, facts=(), *, proofs=False, telemetry=True, **limits):
    return PlannedDatalogEngine().evaluate(plan, facts, identity=plan.identity,
                                           limits=ReasoningLimits(**limits), collect_proofs=proofs,
                                           collect_telemetry=telemetry)


def copy(left, right, rule_id=None):
    return Rule(rule_id or left + "-" + right, [Atom(X, left, Y)], Atom(X, right, Y))


def test_dependency_sccs_topological_order_and_delta_occurrences_are_canonical():
    rules = [copy("seed", "a"), copy("a", "b"), copy("b", "a"), copy("b", "out"),
             Rule("repeated", [Atom(X, "other", Y)] * 2, Atom(X, "last", Y))]
    plan = prepare(rules)
    assert plan == prepare(list(reversed(rules)))
    assert plan.digest == prepare(rules).digest
    assert any(c.predicates == ("a", "b") and c.recursive for c in plan.components)
    assert [(r, i, p) for r, i, p in plan.occurrences if r == "repeated"] == [("repeated", 0, "other")]
    owner = {p: i for i, c in enumerate(plan.components) for p in c.predicates}
    for rule in plan.program.rules:
        assert all(owner[atom.predicate] <= owner[rule.head.predicate] for atom in rule.body)
    repeated = next(r for r in plan.explain_plan()["rules"] if r["rule_id"] == "repeated")
    assert repeated["body"][1]["delta_candidate"] is False
    with pytest.raises(FrozenInstanceError):
        plan.strategy = "parallel"


def test_long_nonrecursive_hierarchy_compiles_and_runs_without_recursion():
    plan = prepare([copy(f"p{i}", f"p{i+1}") for i in range(1200)])
    assert len(plan.components) == 1201
    result = evaluate(plan, [Fact("a", "p0", "b")], max_iterations=1200)
    assert len(result.result.facts) == 1201
    assert result.result.iterations == sum(result.component_rounds) == 1200


def test_scc_seeds_and_recursive_closure_match_grounding_with_proofs():
    plan = prepare([copy("seed", "p"), copy("p", "q"), copy("q", "p"),
                    Rule("transitive", [Atom(X, "q", Y), Atom(Y, "q", Z)], Atom(X, "q", Z)),
                    copy("q", "out")])
    facts = [Fact("a", "seed", "b"), Fact("b", "seed", "c")]
    run = evaluate(plan, [Evidence(f, str(i)) for i, f in enumerate(facts)], proofs=True)
    assert set(run.result.facts) == _reference(plan.program, facts)
    assert not run.result.proofs.truncated
    assert Fact("a", "out", "c") in run.result
    assert run == evaluate(plan, [Evidence(f, str(i)) for i, f in enumerate(facts)][::-1], proofs=True)


@pytest.mark.parametrize("proofs", [False, True])
def test_generated_rules_match_independent_grounding_and_default_closure(proofs):
    rng = random.Random(20261021)
    terms = [X, Y, Z, "a", "b"]
    for case in range(50):
        facts = [Fact(rng.choice("ab"), rng.choice("pqr"), rng.choice("ab")) for _ in range(6)]
        rules = []
        for i in range(4):
            body = [Atom(rng.choice(terms), rng.choice("pqr"), rng.choice(terms)) for _ in range(rng.randint(1, 3))]
            bound = set().union(*(atom.variables for atom in body))
            heads = ["a", "b", *sorted(bound, key=lambda v: v.name)]
            rules.append(Rule(str(i), body, Atom(rng.choice(heads), rng.choice("pqr"), rng.choice(heads))))
        plan = prepare(rules)
        run = evaluate(plan, facts, proofs=proofs)
        assert set(run.result.facts) == _reference(plan.program, facts), case
        assert run.result.facts == DatalogEngine().evaluate(plan.program, facts, limits=ReasoningLimits()).facts
        assert run == evaluate(plan, list(reversed(facts)) + facts, proofs=proofs)


def test_reordered_join_preserves_original_premise_positions_and_selected_index_counts():
    rule = Rule("join", [Atom(X, "wide", Y), Atom(Y, "selected", Z), Atom(X, "wide", Y)], Atom(X, "out", Z))
    plan = prepare([rule])
    facts = [Fact(str(i), "wide", str(i)) for i in range(100)] + [Fact("99", "selected", "yes")]
    run = evaluate(plan, facts, proofs=True)
    witness = next(n for n in run.result.proofs.nodes if n.fact == Fact("99", "out", "yes"))
    assert witness.premises == (Fact("99", "wide", "99"), Fact("99", "selected", "yes"), Fact("99", "wide", "99"))
    assert {r.access_path for r in run.operators} >= {"predicate-scan", "subject-index", "exact-membership"}
    assert {r.body_position for r in run.operators} == {0, 1, 2}
    assert all(r.actual_rows <= r.estimated_rows and r.matched_rows <= r.actual_rows for r in run.operators)
    assert sum(r.actual_rows for r in run.operators) > 0
    assert sum(r.actual_rows for r in run.operators) == run.candidate_rows
    wide = next(s for s in run.input_statistics if s.predicate == "wide")
    assert wide.rows == wide.distinct_subjects == wide.distinct_objects == 100
    assert wide.largest_subject_bucket == 1
    description = run.explain_plan()
    assert json.loads(json.dumps(description)) == description
    assert description["cost_model"] == "heuristic-current-bucket-cardinality"
    description["execution"]["operators"].clear()
    assert run.explain_plan()["execution"]["operators"]
    assert all(a["actual_rows"] is None for r in plan.explain_plan()["rules"] for a in r["body"])


@pytest.mark.parametrize("field", ["context_id", "world", "ontology_revision", "source_policy_id", "mapping_id"])
def test_plan_context_invalidation_occurs_before_input_consumption(field):
    plan = prepare([copy("p", "q")])
    identity = replace(plan.identity, context=replace(CONTEXT, **{field: "changed"}))

    def forbidden():
        raise AssertionError("incompatible source consumed")
        yield

    with pytest.raises(ReasoningError, match="incompatible"):
        PlannedDatalogEngine().evaluate(plan, forbidden(), identity=identity, limits=ReasoningLimits())


@pytest.mark.parametrize("field", ["program_digest", "dictionary_id", "semantics_profile", "executor_version"])
def test_plan_compatibility_fields_cannot_be_reused_implicitly(field):
    plan = prepare()
    with pytest.raises(ReasoningError, match="incompatible"):
        PlannedDatalogEngine().evaluate(plan, [], identity=replace(plan.identity, **{field: "changed"}), limits=ReasoningLimits())


def test_compile_bounds_and_unsupported_strategies_fail_explicitly():
    rules = [copy("p", "q"), copy("q", "r")]
    for kwargs in ({"max_rules": 1}, {"max_atoms": 2}):
        with pytest.raises(ReasoningLimitError):
            prepare(rules, **kwargs)
    for kwargs in ({"max_rules": True}, {"max_atoms": 0}, {"strategy": "parallel"}, {"strategy": "sort-merge"}):
        with pytest.raises(ReasoningError):
            prepare(rules, **kwargs)
    plan = prepare(rules)
    with pytest.raises(ReasoningError, match="bind"):
        PreparedProgram(Program(), plan.identity)


def test_invocation_limits_fail_closed_without_poisoning_reusable_plan():
    plan = prepare([copy("p", "q"), copy("q", "r")])
    for kwargs, message in [({"max_rules": 1}, "max_rules"), ({"max_facts": 1}, "max_facts"),
                            ({"max_candidate_rows": 1}, "max_candidate_rows"), ({"max_iterations": 1}, "max_iterations")]:
        with pytest.raises(ReasoningLimitError, match=message):
            evaluate(plan, [Fact("a", "p", "b")], **kwargs)
    seen = []

    def endless():
        for i in itertools.count():
            seen.append(i)
            yield Fact("a", "p", "b")

    with pytest.raises(ReasoningLimitError, match="max_input_facts"):
        evaluate(plan, endless(), max_input_facts=2)
    assert seen == [0, 1, 2]
    assert len(evaluate(plan, [Fact("a", "p", "b")]).result.facts) == 3
    assert evaluate(plan, []).result.facts == ()


def test_proof_truncation_and_source_failure_are_distinct_from_partial_closure():
    plan = prepare([copy("p", "q")])
    run = evaluate(plan, [Fact("a", "p", "b")], proofs=True, max_proof_nodes=1)
    assert len(run.result.facts) == 2 and run.result.proofs.truncated

    def broken():
        yield Fact("a", "p", "b")
        raise RuntimeError("source failed")

    with pytest.raises(RuntimeError, match="source failed"):
        evaluate(plan, broken())
    with pytest.raises(ReasoningError):
        evaluate(plan, ["not a Fact"])


def test_telemetry_is_opt_in_and_does_not_change_closure_proofs_or_work_counts():
    plan = prepare([copy("p", "q")])
    plain = evaluate(plan, [Fact("a", "p", "b")], proofs=True, telemetry=False)
    measured = evaluate(plan, [Fact("a", "p", "b")], proofs=True)
    assert plain.result == measured.result and plain.candidate_rows == measured.candidate_rows
    assert not plain.operators and not plain.input_statistics and not plain.output_statistics
    assert not plain.explain_plan()["execution"]["telemetry_enabled"]
    assert measured.explain_plan()["execution"]["telemetry_enabled"]


def test_empty_profiled_sites_are_zero_and_unprofiled_sites_remain_unknown():
    plan = prepare([copy("p", "q")])
    profiled = evaluate(plan).explain_plan()["rules"][0]["body"][0]
    assert profiled["actual_rows"] == profiled["estimated_rows"] == 0
    assert profiled["selected_indexes"] == []
    unprofiled = evaluate(plan, telemetry=False).explain_plan()["rules"][0]["body"][0]
    assert unprofiled["actual_rows"] is None and unprofiled["estimated_rows"] is None


def test_relation_statistics_expose_endpoint_skew_without_guessing_costs():
    run = evaluate(prepare(), [Fact("hub", "p", str(i)) for i in range(50)])
    stats = run.input_statistics[0]
    assert stats.rows == stats.distinct_objects == stats.largest_subject_bucket == 50
    assert stats.distinct_subjects == stats.largest_object_bucket == 1
    assert run.candidate_rows == 0


def test_documented_prepared_example_runs():
    from pathlib import Path
    import textwrap

    text = (Path(__file__).parents[1] / "docs/reasoning-plans.rst").read_text()
    block = text.split(".. code-block:: python\n\n", 1)[1].split("\n\nPreparation and compatibility", 1)[0]
    exec(compile(textwrap.dedent(block), "reasoning-plans.rst", "exec"), {})


def test_prepared_execution_runs_without_site_packages_or_storage_imports():
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
from standalone import Atom, Fact, Program, Rule, Variable, ReasoningLimits
from standalone.plans import PlannedDatalogEngine, PreparedProgram
from standalone.protocols import InputContext
from standalone.relational import CompilationIdentity
x, y = Variable('x'), Variable('y')
program = Program([Rule('r', [Atom(x, 'p', y)], Atom(y, 'q', x))])
identity = CompilationIdentity(program.digest, InputContext('h','w','o','p','m'),
                               'external-strings-v1', 'positive-binary-entity-v1', 'indexed-scc-v1')
run = PlannedDatalogEngine().evaluate(PreparedProgram(program, identity), [Fact('a','p','b')],
                                     identity=identity, limits=ReasoningLimits(),
                                     collect_proofs=True, collect_telemetry=True)
assert Fact('b','q','a') in run.result and not run.result.proofs.truncated
assert not any(name.startswith(('gestaltdb','numpy','polars','pyarrow')) for name in sys.modules)
"""
    subprocess.run([sys.executable, "-I", "-S", "-c", script], check=True, capture_output=True, text=True)


def test_reusing_one_plan_across_concurrent_calls_keeps_invocation_state_separate():
    from concurrent.futures import ThreadPoolExecutor

    plan = prepare([copy("p", "q")])
    facts = [Evidence(Fact("a", "p", "b"), "v1")]
    expected = evaluate(plan, facts, proofs=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        runs = list(pool.map(lambda _: evaluate(plan, facts, proofs=True), range(8)))
    assert all(run == expected for run in runs)


def test_recorded_plan_evidence_has_verified_closures_and_explicit_profile_modes():
    from pathlib import Path

    report = json.loads((Path(__file__).parents[1] / "benchmarks/results/reasoning-re04-plans.json").read_text())
    assert report["complete"] and not report["working_tree_dirty"]
    assert len(report["rows"]) == report["planned_rows"] == 144
    for row in report["rows"]:
        assert row["status"] == "ok" and row["closure_digest"] == row["expected_digest"]
        assert row["measure_reuse"] and row["metrics"]["reuse_s"] >= 0
        if row["backend"].startswith("planned"):
            assert row["metrics"]["planned_telemetry"] == ("profiled" in row["backend"])
            assert bool(row["metrics"]["planned_operator_sites"]) == ("profiled" in row["backend"])

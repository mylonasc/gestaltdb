"""Reference workload fixtures and budgets, not hardware timing assertions in CI."""

import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from benchmarks.reasoning_contracts import (
    DEFAULT_BUDGETS, percentile95, summarize, update_worker, validate_budgets,
)
from gestaltdb.reasoning.engine import DatalogEngine
from gestaltdb.reasoning.model import Atom, Fact, Program, ReasoningLimits, Rule, Variable
from tests.test_reasoning_engine import _reference


def budgets():
    return json.loads(DEFAULT_BUDGETS.read_text())


@pytest.mark.parametrize("mode", ["native", "native-proofs"])
def test_reference_update_workload_removes_final_support_and_never_claims_incremental(mode):
    result = update_worker(8, mode)
    assert result["execution_kind"] == "full-rematerialization-not-incremental"
    assert [p["closure_facts"] for p in result["phases"]] == [8, 10, 8]
    assert result["phases"][0]["closure_digest"] != result["phases"][2]["closure_digest"]
    assert result["metrics"]["delete_execution_s"] >= 0


@pytest.mark.parametrize("case", ["diamond", "cyclic-support", "disconnected", "long-identifiers"])
def test_extended_contract_fixtures_match_independent_grounding_across_updates(case):
    x, y, z = Variable("x"), Variable("y"), Variable("z")
    rules = [Rule("transitive", [Atom(x, "p", y), Atom(y, "p", z)], Atom(x, "p", z))]
    rows = {
        "diamond": [("a", "p", "b"), ("a", "p", "c"), ("b", "p", "d"), ("c", "p", "d")],
        "cyclic-support": [("a", "p", "b"), ("b", "p", "a")],
        "disconnected": [("a", "p", "b"), ("c", "p", "d")],
        "long-identifiers": [("urn:" + "a" * 1024, "p", "?constant"), ("?constant", "p", "é")],
    }[case]
    rules.append(Rule("repeated", [Atom(x, "p", x), Atom(x, "p", x)], Atom(x, "same", x)))
    program = Program(rules)
    facts = [Fact(*row) for row in rows]
    for inputs in (facts, facts + facts, facts[1:], []):
        result = DatalogEngine().evaluate(program, inputs, limits=ReasoningLimits(), collect_proofs=True)
        assert set(result.facts) == _reference(program, inputs)
        assert not result.proofs.truncated
        assert result == DatalogEngine().evaluate(program, reversed(inputs), limits=ReasoningLimits(), collect_proofs=True)


def test_budget_failures_are_not_success_and_p95_uses_nearest_rank():
    plan = budgets()
    assert percentile95(list(range(1, 21))) == 19
    assert not any(row["passed"] for row in summarize([], plan))
    plan["cases"] = plan["cases"][:1]
    plan["modes"] = ["native"]
    plan["repeats"] = 1
    row = {"case": plan["cases"][0]["name"], "mode": "native", "status": "ok",
           "metrics": {"pipeline_s": 0.01}, "worker_wall_s": 0.1, "rss_peak_bytes": 1024}
    assert summarize([row], plan)[0]["passed"]
    row["metrics"]["pipeline_s"] = 100
    assert not summarize([row], plan)[0]["passed"]
    row["metrics"]["pipeline_s"] = float("nan")
    assert not summarize([row], plan)[0]["passed"]
    row["status"] = "unavailable"
    assert not summarize([row], plan)[0]["passed"]


@pytest.mark.parametrize("change", [
    {"cases": []}, {"modes": []}, {"modes": ["native", "native"]},
    {"repeats": True}, {"repeats": 0}, {"worker_timeout_s": float("nan")},
])
def test_invalid_budgets_fail_before_running_workers(change):
    plan = budgets()
    plan.update(change)
    with pytest.raises(ValueError):
        validate_budgets(plan)


def test_case_budgets_require_positive_finite_metrics():
    for key in ("pipeline_p95_s", "cold_worker_p95_s", "rss_peak_bytes", "size"):
        plan = copy.deepcopy(budgets())
        plan["cases"][0][key] = 0
        with pytest.raises(ValueError):
            validate_budgets(plan)


def test_roadmap_audit_covers_each_gr_and_existing_evidence():
    root = Path(__file__).parents[1]
    subprocess.run([sys.executable, str(root / "planning/reasoning-engine/check_roadmap.py")],
                   check=True, cwd=root, capture_output=True, text=True)

"""RE-06 executor selection, cooperative cancellation and hardening."""

import pytest

from gestaltdb.reasoning import (
    DatalogEngine,
    ExecutorDecision,
    ExecutorRequest,
    ReasoningCancelledError,
    ReasoningError,
    ReasoningLimits,
    select_executor,
)
from gestaltdb.reasoning.adapters.polars import PolarsDatalogPrototype
from gestaltdb.reasoning.engine import DatalogEngine as IndexedEngine
from gestaltdb.reasoning.executors import INDEXED_PYTHON, INDEXED_SCC, POLARS_PROTOTYPE
from gestaltdb.reasoning.model import Atom, Fact, Program, Rule, Variable
from gestaltdb.reasoning.plans import PlannedDatalogEngine, PreparedProgram
from gestaltdb.reasoning.protocols import InputContext
from gestaltdb.reasoning.relational import CompilationIdentity


X, Y, Z = Variable("x"), Variable("y"), Variable("z")
CONTEXT = InputContext("horizon", "world", "ontology", "policy", "mapping")


def program():
    return Program([Rule("t", [Atom(X, "p", Y), Atom(Y, "p", Z)], Atom(X, "p", Z))])


def test_selection_defaults_and_explicit_preferences():
    assert select_executor(ExecutorRequest()).kind == INDEXED_PYTHON
    assert select_executor(ExecutorRequest(preferred="indexed-python")).kind == INDEXED_PYTHON
    assert select_executor(ExecutorRequest(preferred="indexed-scc")).kind == INDEXED_SCC
    assert select_executor(ExecutorRequest(preferred="polars-prototype",
                                           polars_available=True)).kind == POLARS_PROTOTYPE
    assert select_executor(ExecutorRequest()) == select_executor(ExecutorRequest())
    decision = select_executor(ExecutorRequest(mode="proofs", preferred="indexed-scc"))
    assert decision.kind == INDEXED_SCC
    assert isinstance(decision, ExecutorDecision) and decision.rationale
    with pytest.raises(ReasoningError):
        select_executor(ExecutorRequest(preferred="polars-prototype"))
    with pytest.raises(ReasoningError):
        select_executor(ExecutorRequest(mode="proofs", preferred="polars-prototype"))
    with pytest.raises(ReasoningError):
        select_executor(ExecutorRequest(profile="future-profile"))
    with pytest.raises(ReasoningError):
        select_executor(ExecutorRequest(mode="streaming"))
    with pytest.raises(ReasoningError):
        select_executor(ExecutorRequest(preferred="gpu"))
    with pytest.raises(ReasoningError):
        select_executor(ExecutorRequest(polars_available="yes"))
    with pytest.raises(ReasoningError):
        select_executor("not-a-request")
    with pytest.raises(ReasoningError):
        ExecutorDecision("unknown-v1", ("code",))
    with pytest.raises(ReasoningError):
        ExecutorDecision(INDEXED_PYTHON, ())


def test_cancellation_fails_closed_without_partial_results_or_poisoning():
    engine = IndexedEngine()
    facts = [Fact("a", "p", "b"), Fact("b", "p", "c")]
    with pytest.raises(ReasoningCancelledError):
        engine.evaluate(program(), facts, limits=ReasoningLimits(), cancel=lambda: True)
    with pytest.raises(ReasoningError, match="cancel"):
        engine.evaluate(program(), facts, limits=ReasoningLimits(), cancel="yes")
    calls = {"count": 0}

    def cancel_after_rounds():
        calls["count"] += 1
        return calls["count"] > 2

    with pytest.raises(ReasoningCancelledError):
        engine.evaluate(program(), facts * 2000, limits=ReasoningLimits(max_facts=10_000),
                        cancel=cancel_after_rounds)
    result = engine.evaluate(program(), facts, limits=ReasoningLimits())
    assert len(result.facts) == 3
    assert issubclass(ReasoningCancelledError, ReasoningError)


def test_planned_and_polars_engines_support_cancellation():
    plan_program = program()
    identity = CompilationIdentity(plan_program.digest, CONTEXT, "external-strings-v1",
                                   "positive-binary-entity-v1", "indexed-scc-v1")
    plan = PreparedProgram(plan_program, identity)
    with pytest.raises(ReasoningCancelledError):
        PlannedDatalogEngine().evaluate(plan, [Fact("a", "p", "b")], identity=identity,
                                        limits=ReasoningLimits(), cancel=lambda: True)
    run = PlannedDatalogEngine().evaluate(plan, [Fact("a", "p", "b")], identity=identity,
                                          limits=ReasoningLimits(), cancel=lambda: False)
    assert Fact("a", "p", "b") in run.result
    pytest.importorskip("polars")
    pytest.importorskip("pyarrow")
    prototype = PolarsDatalogPrototype()
    with pytest.raises(ReasoningCancelledError):
        prototype.evaluate(plan_program, [], context=CONTEXT, limits=ReasoningLimits(),
                           cancel=lambda: True)
    outcome = prototype.evaluate(plan_program, [Fact("a", "p", "b"), Fact("b", "p", "c")],
                                 context=CONTEXT, limits=ReasoningLimits(), cancel=lambda: False)
    assert Fact("a", "p", "c") in outcome.result


def test_protocol_backward_compatibility_and_core_isolation():
    engine = DatalogEngine()
    assert len(engine.evaluate(program(), [], limits=ReasoningLimits()).facts) == 0
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
from standalone import DatalogEngine, ExecutorRequest, ReasoningCancelledError, select_executor
from standalone.model import Fact, Program, ReasoningLimits
assert select_executor(ExecutorRequest()).kind == 'indexed-python-v1'
try:
    DatalogEngine().evaluate(Program(), [Fact('a','p','b')], limits=ReasoningLimits(), cancel=lambda: True)
except ReasoningCancelledError:
    pass
else:
    raise AssertionError('cancellation must fail closed')
assert not any(name.startswith(('gestaltdb','numpy','pyarrow','polars')) for name in sys.modules)
"""
    subprocess.run([sys.executable, "-I", "-S", "-c", script], check=True, capture_output=True, text=True)

"""RE-26 scale gates: operator API pins, spill/worker budgets, crossover shape."""

import pytest

from gestaltdb.reasoning.model import ReasoningError, ReasoningLimits, Variable
from gestaltdb.reasoning.scale import (
    OPERATOR_API_VERSION,
    SpillBudget,
    WorkerPolicy,
    backend_scale_notes,
    check_operator_api,
)
from gestaltdb.reasoning.maintenance import rematerialize, retract_facts
from gestaltdb.reasoning.typed import GenericProgram, GenericRule, TypedAtom, TypedFact


def test_operator_api_is_pinned():
    assert OPERATOR_API_VERSION == "v1"
    assert check_operator_api() == "v1"
    assert len(backend_scale_notes()) == 5


def test_spill_requires_explicit_budget():
    budget = SpillBudget("/tmp/opencode/spill", 1 << 20)
    budget.check(0)
    budget.check(1 << 20)
    with pytest.raises(ReasoningError, match="exceeds budget"):
        budget.check((1 << 20) + 1)
    with pytest.raises(ReasoningError):
        SpillBudget("", 1 << 20)
    with pytest.raises(ReasoningError):
        SpillBudget("/tmp/opencode/spill", 0)
    with pytest.raises(ReasoningError):
        budget.check(-1)


def test_workers_require_cancellation():
    WorkerPolicy(workers=4).check()
    WorkerPolicy(workers=1, cancel_supported=True).check()
    with pytest.raises(ReasoningError, match="cancellation"):
        WorkerPolicy(workers=4, cancel_supported=False).check()
    with pytest.raises(ReasoningError):
        WorkerPolicy(workers=0)
    with pytest.raises(ReasoningError):
        WorkerPolicy(workers=True)


def test_crossover_shape_local_changes_win_global_changes_converge():
    x, y, z = Variable("x"), Variable("y"), Variable("z")
    prog = GenericProgram([
        GenericRule("base", [TypedAtom("edge", [x, y])], [], TypedAtom("reach", [x, y])),
        GenericRule("step", [TypedAtom("reach", [x, y]), TypedAtom("edge", [y, z])], [],
                    TypedAtom("reach", [x, z])),
    ])
    base = [TypedFact("edge", [f"n{i}", f"n{i+1}"]) for i in range(24)]
    material = rematerialize(prog, base, limits=ReasoningLimits())
    single = retract_facts(prog, material.facts, base[:1], limits=ReasoningLimits(), index=material.index)
    fresh_single = rematerialize(prog, base[1:], limits=ReasoningLimits())
    assert set(single.facts) == set(fresh_single.facts)
    assert single.work < fresh_single.work  # localized: DRed far below rebuild
    half = retract_facts(prog, material.facts, base[:12], limits=ReasoningLimits(), index=material.index)
    fresh_half = rematerialize(prog, base[12:], limits=ReasoningLimits())
    assert set(half.facts) == set(fresh_half.facts)
    assert half.work < fresh_half.work  # still below; convergence documented, not crossed
    assert half.overdeleted > single.overdeleted  # affected work grows with change size


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
from standalone.scale import OPERATOR_API_VERSION, SpillBudget, WorkerPolicy, check_operator_api
assert check_operator_api() == OPERATOR_API_VERSION == 'v1'
SpillBudget('/tmp/opencode/spill', 8).check(8)
WorkerPolicy(workers=2).check()
assert not any(name.startswith(('gestaltdb', 'numpy')) for name in sys.modules)
"""
    subprocess.run([sys.executable, "-I", "-S", "-c", script], check=True, capture_output=True, text=True)

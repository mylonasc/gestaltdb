"""RE-25 design gates: inheritance allowlists, modal bounds, calculus selection."""

import pytest

from gestaltdb.reasoning.model import ReasoningError
from gestaltdb.reasoning.scopes import (
    CalculusPolicy,
    CalculusRegistry,
    CalculusSpec,
    InheritanceGrant,
    InheritancePolicy,
    ModalBoundary,
    ModalScope,
    approval_gates,
)


def test_inheritance_is_default_deny_with_explicit_grants():
    policy = InheritancePolicy()
    assert not policy.permits("alice", "base", "derived", "belief")
    with pytest.raises(ReasoningError, match="no inheritance grant"):
        policy.check("alice", "base", "derived", "belief")
    granted = InheritancePolicy([InheritanceGrant("alice", "base", "derived", "belief")])
    assert granted.permits("alice", "base", "derived", "belief")
    granted.check("alice", "base", "derived", "belief")
    assert not granted.permits("alice", "derived", "base", "belief")
    assert not granted.permits("alice", "base", "derived", "knowledge")
    assert not granted.permits("mallory", "base", "derived", "belief")
    with pytest.raises(ReasoningError, match="distinct source and target"):
        InheritanceGrant("alice", "base", "base", "belief")
    with pytest.raises(ReasoningError):
        InheritanceGrant("", "base", "derived", "belief")
    with pytest.raises(ReasoningError):
        InheritancePolicy(["nope"])
    assert InheritancePolicy([InheritanceGrant("a", "b", "c", "d")]) == InheritancePolicy(
        [InheritanceGrant("a", "b", "c", "d")])


def test_modal_scope_bounds_without_touching_modal_semantics():
    scope = ModalScope([ModalBoundary("BELIEVES", "belief", 3),
                        ModalBoundary("KNOWS", "knowledge", 1)])
    scope.check("BELIEVES", "belief", 3)
    scope.check("KNOWS", "knowledge", 0)
    with pytest.raises(ReasoningError, match="exceeds max_depth"):
        scope.check("BELIEVES", "belief", 4)
    with pytest.raises(ReasoningError, match="no modal boundary"):
        scope.check("POSSIBLE", "modal", 0)
    with pytest.raises(ReasoningError):
        scope.check("BELIEVES", "belief", -1)
    with pytest.raises(ReasoningError, match="unique per operator"):
        ModalScope([ModalBoundary("BELIEVES", "belief", 1), ModalBoundary("BELIEVES", "belief", 2)])
    with pytest.raises(ReasoningError):
        ModalScope(["nope"])
    with pytest.raises(ReasoningError):
        ModalBoundary("BELIEVES", "belief", -1)
    # The bounded modal API itself is unchanged: exact operator/frame surface.
    from gestaltdb.modal import AccessibilityKind, ModalOperator

    assert set(ModalOperator.__members__) == {"ATOM", "NOT", "AND", "OR", "BELIEVES", "KNOWS",
                                              "POSSIBLE", "NECESSARY"}
    assert set(AccessibilityKind.__members__) == {"BELIEF", "KNOWLEDGE", "MODAL"}


def test_confidence_calculi_stay_design_gated():
    registry = CalculusRegistry()
    assert registry.calculi == ()
    with pytest.raises(ReasoningError, match="not approved"):
        registry.select("noisy-or")
    spec = CalculusSpec("noisy-or",
                        CalculusPolicy("combination", "tests/test_combination.py::test_noisy_or"),
                        CalculusPolicy("retraction", "tests/test_combination.py::test_retract"),
                        CalculusPolicy("convergence", "tests/test_combination.py::test_converges"),
                        CalculusPolicy("provenance", "tests/test_combination.py::test_traces"),
                        scenario="sensor fusion with conflicting sources")
    assert CalculusRegistry([spec]).select("noisy-or") == spec
    with pytest.raises(ReasoningError, match="not approved"):
        CalculusRegistry([spec]).select("other")
    with pytest.raises(ReasoningError, match="unique"):
        CalculusRegistry([spec, spec])
    with pytest.raises(ReasoningError):
        CalculusSpec("x", "not-a-policy", CalculusPolicy("r", "e"), CalculusPolicy("c", "e"),
                     CalculusPolicy("p", "e"), scenario="s")
    assert len(approval_gates()) == 4 and all(isinstance(gate, str) for gate in approval_gates())


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
from standalone.scopes import CalculusRegistry, InheritancePolicy, ModalScope, approval_gates
assert InheritancePolicy().grants == ()
assert ModalScope().boundaries == ()
assert CalculusRegistry().calculi == () and len(approval_gates()) == 4
assert not any(name.startswith(('gestaltdb', 'numpy')) for name in sys.modules)
"""
    subprocess.run([sys.executable, "-I", "-S", "-c", script], check=True, capture_output=True, text=True)

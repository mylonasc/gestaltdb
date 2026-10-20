"""Standalone positive-Datalog contracts, independent of graph persistence.

Import values from :mod:`gestaltdb.reasoning.model`, protocols from
:mod:`gestaltdb.reasoning.protocols`, and ``DatalogEngine`` from
:mod:`gestaltdb.reasoning.engine`. Evaluation is read-only and does not publish
claims. Proof values live in :mod:`gestaltdb.reasoning.proofs`; ontology compilation
is a separate layer in :mod:`gestaltdb.ontology`. The package-root
imports are not an independent-installation or minimal-import guarantee.
"""

from .engine import DatalogEngine
from .model import (
    Atom, EvaluationResult, Fact, Program, ReasoningError, ReasoningLimitError,
    ReasoningLimits, Rule, Variable,
)
from .proofs import Explanation, FactWitness, ProofTrace
from .protocols import Evidence, Evaluator, FactSource, InputContext

__all__ = [
    "DatalogEngine", "Atom", "EvaluationResult", "Fact", "Program", "ReasoningError",
    "ReasoningLimitError", "ReasoningLimits", "Rule", "Variable", "Explanation",
    "FactWitness", "ProofTrace", "Evidence", "Evaluator", "FactSource", "InputContext",
]

"""Standalone positive-Datalog contracts, independent of graph persistence.

Import values from :mod:`gestaltdb.reasoning.model`, protocols from
:mod:`gestaltdb.reasoning.protocols`, and ``DatalogEngine`` from
:mod:`gestaltdb.reasoning.engine`. Evaluation is read-only and does not publish
claims. Proof values live in :mod:`gestaltdb.reasoning.proofs`; ontology compilation
is a separate layer in :mod:`gestaltdb.ontology`. The package-root
imports are not an independent-installation or minimal-import guarantee.
Additive relational IR and structural inspection live in ``.relational``;
these do not expand the executable binary-entity profile.
Compact reference batches/dictionaries live in ``.batches`` and optional Arrow
exchange in ``.adapters.arrow``; neither is imported by this package initializer.
Opt-in reusable SCC schedules and indexed telemetry live in ``.plans``; detailed
profiling is disabled by default and is separate from the default evaluator.
Deterministic executor selection lives in ``.executors``; cooperative
cancellation raises ``ReasoningCancelledError`` without partial results.
Bounded extraction requests and capability records live in ``.extraction``;
the pinned batched claim source in ``.adapters.batched`` borrows caller views,
and ``.adapters.native`` binds audited PyRex Arrow scans as copied raw batches.
Typed literal values and generic n-ary Datalog live in ``.terms`` and ``.typed``;
evidence-bearing validation lives in ``.validation``. Snapshot-local equality
with canonical representatives lives in ``.equality``; mappings are valid for
exactly one evaluated snapshot. The old binary entity profile is unchanged.
"""

from .engine import DatalogEngine
from .demand import BoundGoal, DemandAnswer, SpecializationCache, answer, specialize
from .equality import CanonicalMapping, DifferentFrom, EqualityScope, FunctionalProperty, InequalityConflict, SameAs, canonicalize
from .executors import ExecutorDecision, ExecutorRequest, select_executor
from .model import (
    Atom, EvaluationResult, Fact, Program, ReasoningCancelledError, ReasoningError, ReasoningLimitError,
    ReasoningLimits, Rule, Variable,
)
from .proofs import Explanation, FactWitness, ProofTrace
from .protocols import Evidence, Evaluator, FactSource, InputContext
from .terms import Literal
from .typed import AggregateRule, Aggregation, Comparison, GenericEngine, GenericProgram, GenericResult, GenericRule, MultiHeadRule, NegationWitness, StratifiedRun, TypedAtom, TypedFact, expand_predicate_variables, normalize_program, predicate_domain
from .catalog import CatalogEntry, PinnedGeneration, RuleCatalog, catalog_legacy_parity_gates, migrate_dict, pin_generation
from .intervals import Interval, IntervalError, IntervalSet, SupportSegment, coalesce_segments
from .el import EL_PROFILE, ELClassification, ELDerivation, check_proof, classify, holds_in_all_models, normalize_axiom
from .profiles import ConstructEntry, MATRIX, NAMED_SUBSETS, approval_checklist, check_construct, matrix_digest, subset_constructs
from .temporal_closure import TemporalClosure, TemporalDerivation, TemporalFact, RangeUpdate, ClosureVerification, canonicalize_segments, change_segments, evaluate_range, update_range, verify_closure
from .maintenance import DependencyIndex, Materialization, PropagationResult, RetractionResult, Support, build_index, propagate_insertions, rematerialize, retract_facts
from .validation import Denial, ValidationReport, ValidationSuite, Violation, find_unsatisfiable, validate

__all__ = [
    "DatalogEngine", "Atom", "EvaluationResult", "Fact", "Program", "ReasoningError",
    "ReasoningLimitError", "ReasoningCancelledError", "ReasoningLimits", "Rule", "Variable", "Explanation",
    "FactWitness", "ProofTrace", "Evidence", "Evaluator", "FactSource", "InputContext",
    "ExecutorDecision", "ExecutorRequest", "select_executor", "Literal",
    "CanonicalMapping", "DifferentFrom", "EqualityScope", "FunctionalProperty",
    "InequalityConflict", "SameAs", "canonicalize",
    "Comparison", "GenericEngine", "GenericProgram", "GenericResult", "GenericRule", "MultiHeadRule", "TypedAtom", "TypedFact",
    "AggregateRule", "Aggregation", "expand_predicate_variables", "normalize_program", "predicate_domain",
    "NegationWitness", "StratifiedRun",
    "Denial", "ValidationReport", "ValidationSuite", "Violation", "find_unsatisfiable", "validate",
    "BoundGoal", "DemandAnswer", "SpecializationCache", "answer", "specialize",
    "DependencyIndex", "PropagationResult", "Support", "build_index", "propagate_insertions",
    "Materialization", "RetractionResult", "rematerialize", "retract_facts",
    "CatalogEntry", "PinnedGeneration", "RuleCatalog",
    "catalog_legacy_parity_gates", "migrate_dict", "pin_generation",
    "Interval", "IntervalError", "IntervalSet", "SupportSegment", "coalesce_segments",
    "TemporalClosure", "TemporalDerivation", "TemporalFact", "RangeUpdate", "ClosureVerification",
    "canonicalize_segments", "change_segments", "evaluate_range", "update_range", "verify_closure",
    "ConstructEntry", "MATRIX", "NAMED_SUBSETS",
    "approval_checklist", "check_construct", "matrix_digest", "subset_constructs",
    "EL_PROFILE", "ELClassification", "ELDerivation",
    "check_proof", "classify", "holds_in_all_models", "normalize_axiom",
]

"""Complete current-record projection through authenticated executor admission.

Not a persistent cache, publication permit or independent closure verifier.
Current-state and temporal-prefix identities are deliberately distinct.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ...graphdb import GraphDB
from ...ontology.compiler import CompiledOntology
from ...readview import CurrentGraphReadView, CurrentReadProvenance, ProvenanceMismatchError
from ..catalog import RuleCatalog
from ..executors import PROFILE, ExecutorRequest, select_executor
from ..generation import AdmissionRequest, CurrentSourceIdentity, GenerationIdentity
from ..model import ReasoningError, ReasoningLimits, check_cancelled
from ..plans import PreparedProgram
from .execution import ExecutionOutcome, execute_closure, _polars_intermediate_limit
from .generation import catalog_for_ontology
from .graph import GraphMapping, GraphSourceLimits, PinnedGraphSource, _graph_context


def _source(provenance):
    return CurrentSourceIdentity(provenance.database_id, provenance.snapshot_sequence, provenance.token)


@dataclass(frozen=True)
class CurrentGenerationBinding:
    """Snapshot-policy compatibility metadata; construction is not authentication."""

    identity: GenerationIdentity
    provenance: CurrentReadProvenance
    mapping: GraphMapping

    def __post_init__(self):
        if (not isinstance(self.identity, GenerationIdentity)
                or not isinstance(self.provenance, CurrentReadProvenance)
                or not isinstance(self.mapping, GraphMapping)):
            raise ReasoningError("current generation identity, provenance and GraphMapping required")
        provenance = CurrentReadProvenance.from_dict(self.provenance.to_dict())
        if (self.identity.source != _source(provenance)
                or self.identity.context != _graph_context(
                    provenance, self.identity.context.ontology_revision, self.mapping)
                or self.identity.semantics_profile != PROFILE
                or self.identity.dictionary_id != "external-strings-v1"):
            raise ProvenanceMismatchError("current generation source/context policy binding mismatch")
        object.__setattr__(self, "provenance", provenance)

    def verify_view(self, view: CurrentGraphReadView) -> None:
        """Authenticate this exact active snapshot, including full-state digest."""
        self.provenance.verify_view(view)
        with PinnedGraphSource(view, mapping=self.mapping):
            pass

    def verify_source(self, graph: GraphDB) -> None:
        """Reject source movement; not atomic publication or old-view reopening."""
        if not isinstance(graph, GraphDB):
            raise ReasoningError("GraphDB required")
        self.provenance.verify_source(graph)


@dataclass(frozen=True)
class CurrentGraphExecution:
    """A complete admitted closure and its authenticated source-policy binding."""

    outcome: ExecutionOutcome
    binding: CurrentGenerationBinding


def _check_catalog(compiled, catalog):
    if not isinstance(compiled, CompiledOntology) or not isinstance(catalog, RuleCatalog):
        raise ReasoningError("CompiledOntology and RuleCatalog required")
    expected = {entry.rule_key: entry.content_digest for entry in catalog_for_ontology(compiled).entries}
    if {entry.rule_key: entry.content_digest for entry in catalog.entries} != expected:
        raise ReasoningError("catalog content does not match the compiled ontology")


def _binding(source, compiled, catalog, mapping):
    identity = GenerationIdentity(_source(source._provenance),
                                  _graph_context(source._provenance, compiled.revision_id, mapping),
                                  compiled.program.digest, catalog.digest, PROFILE, "external-strings-v1")
    return CurrentGenerationBinding(identity, source._provenance, mapping)


def bind_current_generation(view: CurrentGraphReadView, compiled: CompiledOntology, catalog: RuleCatalog, *,
                            mapping: GraphMapping, cancel: Callable[[], bool] | None = None) -> CurrentGenerationBinding:
    """Authenticate metadata before preparing a plan; no closure is computed.

    This does not admit caller-supplied facts or certify an existing result.
    The execution adapter independently reauthenticates and projects the source.
    """
    _check_catalog(compiled, catalog)
    check_cancelled(cancel)
    with PinnedGraphSource(view, mapping=mapping) as source:
        check_cancelled(cancel)
        return _binding(source, compiled, catalog, mapping)


def execute_current_graph(view: CurrentGraphReadView, compiled: CompiledOntology, catalog: RuleCatalog, *,
                          mapping: GraphMapping, limits: ReasoningLimits,
                          source_limits: GraphSourceLimits = GraphSourceLimits(),
                          executor_request: ExecutorRequest = ExecutorRequest(),
                          plan: PreparedProgram | None = None,
                          expected_generation: GenerationIdentity | None = None,
                          collect_proofs: bool = False,
                          polars_max_intermediate_rows: int | None = None,
                          cancel: Callable[[], bool] | None = None) -> CurrentGraphExecution:
    """Authenticate, project and execute, checking expected identity before records.

    Inputs always come from complete canonical-record projection, never caller
    triples or partial frontier answers. Catalog rule content must match the
    actual compiled program. Catalog versions remain part of exact reuse identity.
    Defaults stay indexed Python; SCC requires an explicit compatible plan and
    Polars requires explicit supported closure-only opt-in. No writes/fallbacks.

    Candidate and reasoning bounds do not bound snapshot hashing or memory.
    Cancellation is cooperative between backend/engine operations. The caller
    must keep the read view open throughout; this function never closes it.
    """
    _check_catalog(compiled, catalog)
    if not isinstance(limits, ReasoningLimits):
        raise ReasoningError("ReasoningLimits required")
    if expected_generation is not None and not isinstance(expected_generation, GenerationIdentity):
        raise ReasoningError("expected_generation must be a GenerationIdentity or None")
    check_cancelled(cancel)
    decision = select_executor(executor_request)
    _polars_intermediate_limit(decision.kind, polars_max_intermediate_rows)
    with PinnedGraphSource(view, mapping=mapping, limits=source_limits) as source:
        check_cancelled(cancel)
        binding = _binding(source, compiled, catalog, mapping)
        identity = binding.identity
        if expected_generation is not None:
            identity.require_compatible(expected_generation)
        outcome = execute_closure(
            compiled.program, source._evidence(cancel=cancel), executor_request=executor_request,
            admission_request=AdmissionRequest(executor_request.mode, decision.kind),
            generation=identity, plan=plan, context=identity.context,
            limits=limits, collect_proofs=collect_proofs,
            polars_max_intermediate_rows=polars_max_intermediate_rows, cancel=cancel)
        check_cancelled(cancel)
        source._check()
        return CurrentGraphExecution(outcome, binding)

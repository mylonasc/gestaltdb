"""Explicit generation-pinned, read-only Cypher over a selected logical graph.

Preparation owns authenticated closure computation; queries never accept an
arbitrary EvaluationResult, rerun reasoning, write records or alter ordinary
GraphDB.query semantics. Current binary entity reasoning only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

from ...graphdb import GraphDB
from ...ontology.compiler import CompiledOntology
from ...ontology.model import TYPE_PREDICATE
from ...query_engine.cypher.api import QueryResult, parse_ast
from ...query_engine.cypher.ast import MatchClause, Query, ReturnClause, WhereClause
from ...query_engine.cypher.plan import plan_staged_query
from ...query_engine.cypher.runtime import QueryContext, execute_plan
from ...readview import CurrentGraphReadView, ProvenanceMismatchError
from ..catalog import RuleCatalog
from ..generation import GenerationIdentity
from ..model import ReasoningError, ReasoningLimitError, ReasoningLimits, check_cancelled
from .graph import GraphMapping, GraphSourceLimits, PinnedGraphSource, _digest, _pairs
from .graph_execution import execute_current_graph
from .query_graph import _QueryGraph

QueryMode = Literal["asserted-only", "asserted-plus-inferred"]


@dataclass(frozen=True)
class QuerySelection:
    """Output labels -> class IDs and relationship types -> binary predicates.

    This is a separate projection policy, not the input GraphMapping. Only
    selected labels and relations exist in the ephemeral logical graph.
    """

    labels: tuple[tuple[str, str], ...] = ()
    edge_types: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        for name in ("labels", "edge_types"):
            object.__setattr__(self, name, _pairs(getattr(self, name), name))
        if not self.labels and not self.edge_types:
            raise ReasoningError("query selection must name labels or edge types")
        if any(predicate == TYPE_PREDICATE for _, predicate in self.edge_types):
            raise ReasoningError("select type facts as labels, not relationships")

    @property
    def digest(self) -> str:
        return _digest(["reasoning-query-selection-v1", self.labels, self.edge_types])


@dataclass(frozen=True)
class QueryLimits:
    """Projection/read/output caps, not hard scalar-expression/RSS/time limits."""

    max_nodes: int = 10_000
    max_edges: int = 20_000
    max_query_reads: int = 100_000
    max_result_rows: int = 20_000

    def __post_init__(self) -> None:
        for name in ("max_nodes", "max_edges", "max_query_reads", "max_result_rows"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ReasoningError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class ReasoningQueryResult:
    """Cypher rows with exact source/rules/generation and projection semantics."""

    result: QueryResult
    generation: GenerationIdentity
    selection: QuerySelection
    mode: QueryMode


def _plan(query: str):
    canonical = parse_ast(query)
    if (not isinstance(canonical, Query)
            or len(canonical.clauses) not in (2, 3)
            or not isinstance(canonical.clauses[0], MatchClause)
            or not isinstance(canonical.clauses[-1], ReturnClause)
            or (len(canonical.clauses) == 3 and not isinstance(canonical.clauses[1], WhereClause))):
        raise ReasoningError("reasoning queries require one MATCH, optional WHERE and RETURN; read-only subset")
    match = canonical.clauses[0]
    if match.qualifiers or match.selector or len(match.patterns) != 1:
        raise ReasoningError("reasoning queries do not support qualifiers, selectors or multiple patterns")
    pattern = match.patterns[0]
    if len(pattern.hops) > 1 or any((hop.min_length, hop.max_length) != (1, 1) for hop in pattern.hops):
        raise ReasoningError("reasoning queries support only node or fixed one-hop patterns")
    return plan_staged_query(canonical)


class ReasoningQueryView:
    """Prepare once from an authenticated snapshot; query while it stays current.

    The view is borrowed, never closed by this adapter. Preparation computes the
    complete binary closure through execute_current_graph and extracts the exact
    base fact set from the same snapshot. No caller-supplied closure is admitted.
    Source freshness and active view identity are checked before and after every
    query. This is not persistent cache admission or atomic publication.
    """

    def __init__(self, graph: GraphDB, view: CurrentGraphReadView, compiled: CompiledOntology, catalog: RuleCatalog, *,
                 mapping: GraphMapping, selection: QuerySelection, limits: ReasoningLimits,
                 source_limits: GraphSourceLimits = GraphSourceLimits(),
                 expected_generation: GenerationIdentity | None = None,
                 cancel: Callable[[], bool] | None = None):
        if not isinstance(graph, GraphDB):
            raise ReasoningError("live source GraphDB required")
        if not isinstance(selection, QuerySelection):
            raise ReasoningError("explicit QuerySelection required")
        execution = execute_current_graph(view, compiled, catalog, mapping=mapping, limits=limits,
                                          source_limits=source_limits, expected_generation=expected_generation,
                                          cancel=cancel)
        self._view, self._graph, self._store = view, graph, view.store
        self._snapshot_graph = view._graph
        self._binding = execution.binding
        self._selection = selection
        self._facts = execution.outcome.result.facts
        with PinnedGraphSource(view, mapping=mapping, limits=source_limits) as source:
            self._base = frozenset(item.fact for item in source._evidence(cancel=cancel))
        self._check(cancel)

    @property
    def generation(self) -> GenerationIdentity:
        return self._binding.identity

    @property
    def selection(self) -> QuerySelection:
        return self._selection

    def _check(self, cancel) -> None:
        check_cancelled(cancel)
        if self._view._graph is not self._snapshot_graph or self._view.store is not self._store:
            raise ProvenanceMismatchError("reasoning query view was rebound")
        self._binding.verify_view(self._view)
        self._binding.verify_source(self._graph)
        check_cancelled(cancel)

    def query(self, cypher: str, parameters: dict[str, object] | None = None, *,
              expected_generation: GenerationIdentity, mode: QueryMode,
              limits: QueryLimits = QueryLimits(),
              cancel: Callable[[], bool] | None = None) -> ReasoningQueryResult:
        """Query a selected logical set, not physical edge records or properties.

        Mode and the full expected generation are mandatory. One node/one-hop
        MATCH, optional WHERE, RETURN and its modifiers are supported; all other
        clause forms fail before execution. Scalars keep ordinary Cypher behavior
        (including allocation costs); query-read caps are not a sandbox.
        """
        if not isinstance(expected_generation, GenerationIdentity):
            raise ReasoningError("exact expected_generation required")
        self.generation.require_compatible(expected_generation)
        if mode not in ("asserted-only", "asserted-plus-inferred"):
            raise ReasoningError("mode must be asserted-only or asserted-plus-inferred")
        if not isinstance(limits, QueryLimits):
            raise ReasoningError("QueryLimits required")
        logical_plan = _plan(cypher)
        self._check(cancel)
        facts = self._facts if mode == "asserted-plus-inferred" else sorted(self._base)
        graph = _QueryGraph(facts, self._base, self.selection, limits, cancel)
        records = execute_plan(logical_plan, QueryContext(graph=graph, parameters=parameters or {}))
        if len(records) > limits.max_result_rows:
            raise ReasoningLimitError("query exceeded max_result_rows")
        self._check(cancel)
        return ReasoningQueryResult(QueryResult(logical_plan.columns, records), self.generation, self.selection, mode)

"""Opt-in asserted-plus-inferred graph queries over one admitted generation.

Ordinary Cypher never sees inferred facts; this view does not touch the
Cypher planner. It answers triple matches and single-hop path patterns from
a precomputed admitted closure (or from the asserted projection alone),
with every answer tied to the exact generation identity. Stale or
incompatible generations fail closed; nothing here materializes, mutates or
reopens backend state — the view holds fact tuples and a binding, never a
live graph handle.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ...ontology.compiler import CompiledOntology
from ...ontology.model import TYPE_PREDICATE
from ...readview import CurrentGraphReadView
from ..catalog import RuleCatalog
from ..executors import ExecutorRequest
from ..generation import GenerationIdentity
from ..model import Fact, ReasoningError, ReasoningLimits, _text, check_cancelled
from ..plans import PreparedProgram
from .graph import GraphMapping, GraphSourceLimits, PinnedGraphSource
from .graph_execution import CurrentGenerationBinding, execute_current_graph

MODES = ("asserted-only", "asserted-plus-inferred")


def _entity_or_none(value, name: str):
    if value is None:
        return None
    return _text(value, name)


@dataclass(frozen=True)
class PathPattern:
    """One single-hop pattern over the admitted closure.

    InferredMatch answers always follow fact direction; ``direction`` only
    decides which end ``source``/``target`` constrain. ``out`` matches
    ``(source)-[edge]->(target)``; ``in`` matches
    ``(source)<-[edge]-(target)``.
    """

    edge: str
    source: str | None = None
    target: str | None = None
    source_type: str | None = None
    target_type: str | None = None
    direction: str = "out"

    def __post_init__(self) -> None:
        _text(self.edge, "edge predicate")
        for name in ("source", "target", "source_type", "target_type"):
            value = getattr(self, name)
            if value is not None:
                _text(value, name)
        if self.direction not in ("out", "in"):
            raise ReasoningError("direction must be out or in")


@dataclass(frozen=True)
class InferredMatch:
    """One grounded path: endpoints plus the matched edge predicate."""

    source: str
    edge: str
    target: str

    def __post_init__(self) -> None:
        for name in ("source", "edge", "target"):
            _text(getattr(self, name), name)


@dataclass(frozen=True)
class InferredGraphView:
    """Triple matches and path patterns over one pinned generation.

    ``mode`` selects the fact set explicitly per view: ``asserted-only``
    reads the projected source records, ``asserted-plus-inferred`` reads the
    admitted closure. ``with_mode`` returns the same generation under the
    other semantics; the default query surface never silently mixes them.
    """

    binding: CurrentGenerationBinding
    asserted: tuple[Fact, ...]
    closure: tuple[Fact, ...]
    mode: str = "asserted-plus-inferred"

    def __post_init__(self) -> None:
        if not isinstance(self.binding, CurrentGenerationBinding):
            raise ReasoningError("CurrentGenerationBinding required")
        for name in ("asserted", "closure"):
            value = getattr(self, name)
            if not isinstance(value, (tuple, list)) or not all(isinstance(item, Fact) for item in value):
                raise ReasoningError(f"{name} must contain only Fact values")
            object.__setattr__(self, name, tuple(sorted(set(value))))
        if self.mode not in MODES:
            raise ReasoningError(f"mode must be one of {MODES}")

    @property
    def generation(self) -> GenerationIdentity:
        return self.binding.identity

    @property
    def facts(self) -> tuple[Fact, ...]:
        """Active fact set for the selected semantics."""
        return self.closure if self.mode == "asserted-plus-inferred" else self.asserted

    def with_mode(self, mode: str) -> InferredGraphView:
        """Same generation under explicitly selected semantics."""
        if mode not in MODES:
            raise ReasoningError(f"mode must be one of {MODES}")
        return InferredGraphView(self.binding, self.asserted, self.closure, mode)

    def match(self, *, subject: str | None = None, predicate: str | None = None,
              object: str | None = None) -> tuple[Fact, ...]:
        """Filter the active fact set; never graph records or Cypher rows."""
        for name, value in (("subject", subject), ("predicate", predicate), ("object", object)):
            if value is not None:
                _text(value, name)
        return tuple(fact for fact in self.facts
                     if (subject is None or fact.subject == subject)
                     and (predicate is None or fact.predicate == predicate)
                     and (object is None or fact.object == object))

    def query_path(self, pattern: PathPattern) -> tuple[InferredMatch, ...]:
        """Single-hop patterns, including edges present only by inference."""
        if not isinstance(pattern, PathPattern):
            raise ReasoningError("PathPattern required")
        members: dict[str, set[str]] = {}
        for fact in self.facts:
            if fact.predicate == TYPE_PREDICATE:
                members.setdefault(fact.object, set()).add(fact.subject)
        matches = []
        for fact in self.facts:
            if fact.predicate != pattern.edge:
                continue
            first, second = (fact.subject, fact.object) if pattern.direction == "out" else (
                fact.object, fact.subject)
            if pattern.source is not None and first != pattern.source:
                continue
            if pattern.target is not None and second != pattern.target:
                continue
            if pattern.source_type is not None and first not in members.get(pattern.source_type, ()):
                continue
            if pattern.target_type is not None and second not in members.get(pattern.target_type, ()):
                continue
            matches.append(InferredMatch(fact.subject, fact.predicate, fact.object))
        return tuple(matches)

    def require_generation(self, expected: GenerationIdentity) -> None:
        """Reject any logical identity drift before reusing answers."""
        self.generation.require_compatible(expected)

    def assert_current(self, view: CurrentGraphReadView) -> None:
        """Re-authenticate the exact snapshot; movement fails closed."""
        self.binding.verify_view(view)


def open_inferred_view(view: CurrentGraphReadView, compiled: CompiledOntology, catalog: RuleCatalog, *,
                       mapping: GraphMapping, limits: ReasoningLimits, mode: str = "asserted-plus-inferred",
                       source_limits: GraphSourceLimits = GraphSourceLimits(),
                       executor_request: ExecutorRequest = ExecutorRequest(),
                       plan: PreparedProgram | None = None,
                       expected_generation: GenerationIdentity | None = None,
                       collect_proofs: bool = False,
                       polars_max_intermediate_rows: int | None = None,
                       cancel: Callable[[], bool] | None = None) -> InferredGraphView:
    """Admit one generation, then answer queries from its pinned facts.

    The closure comes from full authenticated admission; the asserted set
    from a second bounded projection pass over the same snapshot. Both
    passes enforce ``source_limits``; neither truncates nor writes. The
    caller keeps the read view open for this call only — the returned view
    outlives it, carrying facts plus the binding instead of a graph handle.
    """
    if mode not in MODES:
        raise ReasoningError(f"mode must be one of {MODES}")
    check_cancelled(cancel)
    execution = execute_current_graph(
        view, compiled, catalog, mapping=mapping, limits=limits, source_limits=source_limits,
        executor_request=executor_request, plan=plan, expected_generation=expected_generation,
        collect_proofs=collect_proofs, polars_max_intermediate_rows=polars_max_intermediate_rows,
        cancel=cancel)
    with PinnedGraphSource(view, mapping=mapping, limits=source_limits) as source:
        check_cancelled(cancel)
        asserted = tuple(item.fact for item in source._evidence(cancel=cancel))
    check_cancelled(cancel)
    return InferredGraphView(execution.binding, asserted, tuple(execution.outcome.result.facts), mode)


__all__ = ["InferredGraphView", "InferredMatch", "MODES", "PathPattern", "open_inferred_view"]

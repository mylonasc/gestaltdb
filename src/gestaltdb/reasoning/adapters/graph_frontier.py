"""Indexed frontier reads from an authenticated current-property-graph snapshot.

No temporal claims, implicit ontology mapping, graph mutation or publication.
The caller owns the immutable backend view for the entire evaluation.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Callable

from ...ontology.model import TYPE_PREDICATE
from ...readview import CurrentGraphReadView, CurrentReadProvenance, ProvenanceMismatchError
from ..frontier import FrontierAnswer, evaluate_frontier
from ..model import Atom, Program, ReasoningError, ReasoningLimitError, ReasoningLimits, _text, check_cancelled
from ..protocols import InputContext
from .graph import GraphMapping, GraphSourceLimits, PinnedGraphSource, _edge_evidence, _graph_context, _node_evidence


@dataclass(frozen=True)
class CurrentFrontierAnswer:
    """Complete goal answers, snapshot-local evidence and cumulative accesses."""

    answer: FrontierAnswer
    context: InputContext
    provenance: CurrentReadProvenance
    mapping: GraphMapping
    records_read: int
    accesses: tuple[tuple[str, int], ...]


def answer_current_graph(view: CurrentGraphReadView, program: Program, goal: Atom, *,
                         ontology_revision: str, mapping: GraphMapping,
                         limits: ReasoningLimits,
                         source_limits: GraphSourceLimits = GraphSourceLimits(),
                         max_requests: int = 10_000,
                         cancel: Callable[[], bool] | None = None) -> CurrentFrontierAnswer:
    """Interleave positive binary demand with explicit GraphMapping projection.

    Bound edge endpoints use typed adjacency (incoming for object-only goals);
    unbound edges use type indexes. Labels use direct node reads or label indexes.
    Properties use direct reads, configured exact/prefix indexes, or a documented
    complete node scan when no property index exists. No index is built here.

    Record limits count candidate lookups across all requests (including repeats
    and filtered records), not snapshot hashing, KV-internal work, bytes or RSS.
    Evidence references follow PinnedGraphSource's snapshot-local conventions;
    they are not temporal version IDs, independent proofs or persistent permits.
    """
    if not isinstance(program, Program) or not isinstance(goal, Atom):
        raise ReasoningError("positive binary Program and Atom goal required")
    if not isinstance(mapping, GraphMapping) or not isinstance(limits, ReasoningLimits):
        raise ReasoningError("explicit GraphMapping and ReasoningLimits required")
    if not isinstance(source_limits, GraphSourceLimits):
        raise ReasoningError("source_limits must be GraphSourceLimits")
    if isinstance(max_requests, bool) or not isinstance(max_requests, int) or max_requests < 1:
        raise ReasoningError("max_requests must be a positive integer")
    if len(program.rules) > limits.max_rules:
        raise ReasoningLimitError("max_rules exceeded")
    _text(ontology_revision, "ontology_revision")
    check_cancelled(cancel)
    count = 0
    accesses: Counter[str] = Counter()
    # This source authenticates the immutable store digest once, then checks
    # exact view/store/provenance and active transaction identity on each read.
    with PinnedGraphSource(view, mapping=mapping, limits=source_limits) as source:
        if set(view.stale_indexes()).intersection({"node_label", "node_property", "edge_type"}):
            raise ReasoningError("current graph indexes are stale; rebuild outside the read view")

        def check():
            check_cancelled(cancel)
            source._check()

        def record(kind, key, strategy, *, missing_ok=False):
            nonlocal count
            check()
            count += 1
            if count > source_limits.max_records:
                raise ReasoningLimitError("current frontier exceeded max_records")
            accesses[strategy] += 1
            if isinstance(key, str):
                key = view.node_key_to_bytes(key)
            value = view.get_node(key) if kind == "node" else view.get_edge(key)
            check()
            if value is None and not missing_ok:
                raise ProvenanceMismatchError("indexed snapshot record disappeared")
            return value

        def selected(items, request):
            for item in items:
                if (request.subject is None or item.fact.subject == request.subject) and (
                        request.object is None or item.fact.object == request.object):
                    yield item

        def read(request):
            check()
            if request.predicate == TYPE_PREDICATE:
                for label, class_name in mapping.labels:
                    if request.object is not None and request.object != class_name:
                        continue
                    if request.subject is not None:
                        keys, strategy = (request.subject,), "node-id"
                    else:
                        keys, strategy = view.indexes.iter_node_ids_by_label(label), "label-index"
                    for key in keys:
                        node = record("node", key, strategy, missing_ok=request.subject is not None)
                        if node is not None:
                            yield from selected(_node_evidence(node, GraphMapping(labels=((label, class_name),))), request)
            for prop, predicate in mapping.node_properties:
                if predicate != request.predicate:
                    continue
                if request.subject is not None:
                    keys, strategy = (request.subject,), "node-id"
                elif prop in view.indexed_node_properties:
                    if request.object is not None:
                        keys, strategy = view.indexes.iter_node_ids_by_property(prop, request.object), "property-exact-index"
                    else:
                        keys = view.store.iter_index_prefix("node_property", [prop.encode("utf-8")])
                        strategy = "property-prefix-index"
                else:
                    keys, strategy = view.store.get_node_keys_generator(), "property-record-scan"
                for key in keys:
                    node = record("node", key, strategy, missing_ok=request.subject is not None)
                    if node is not None:
                        yield from selected(_node_evidence(node, GraphMapping(node_properties=((prop, predicate),))), request)
            for edge_type, predicate in mapping.edge_types:
                if predicate != request.predicate:
                    continue
                if request.subject is not None or request.object is not None:
                    endpoint = request.subject if request.subject is not None else request.object
                    direction = "out" if request.subject is not None else "in"
                    keys = (item["edge_id"] for item in view.iter_typed_adjacency(endpoint, edge_type, direction))
                    strategy = "typed-adjacency-" + direction
                else:
                    keys, strategy = view.indexes.iter_edge_ids_by_type(edge_type), "edge-type-index"
                for key in keys:
                    edge = record("edge", key, strategy)
                    yield from selected(_edge_evidence(edge, GraphMapping(edge_types=((edge_type, predicate),))), request)
            check()

        result = evaluate_frontier(program, goal, read, limits=limits, max_requests=max_requests, cancel=cancel)
        check()
        return CurrentFrontierAnswer(result, _graph_context(source._provenance, ontology_revision, mapping),
                                     source._provenance, mapping, count, tuple(sorted(accesses.items())))

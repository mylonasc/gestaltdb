"""Private ephemeral logical graph for the reasoning-aware Cypher adapter.

No storage, writers, procedures or forwarding to the physical graph. Returned
entities belong to this invocation only, never to a reusable closure.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator

from ..._graph.entities import Edge, Node, key_to_string, node_key_to_bytes
from ...ontology.model import TYPE_PREDICATE
from ..model import Fact, ReasoningLimitError, check_cancelled
from .graph import _digest


class _QueryGraph:
    node_key_to_bytes = staticmethod(node_key_to_bytes)
    key_to_string = staticmethod(key_to_string)
    indexed_node_properties: set[str] = set()

    def __init__(self, facts: Iterable[Fact], base: frozenset[Fact], selection,
                 limits, cancel: Callable[[], bool] | None):
        self._limits, self._cancel, self._reads = limits, cancel, 0
        self._nodes: dict[bytes, Node] = {}
        self._edges: dict[bytes, Edge] = {}
        labels = dict(selection.labels)
        types = dict(selection.edge_types)
        self._adjacency: dict[tuple[bytes, str, str], list[dict[str, bytes]]] = {}
        for fact in facts:
            check_cancelled(cancel)
            if fact.predicate == TYPE_PREDICATE:
                for label, class_id in labels.items():
                    if fact.object == class_id:
                        node = self._node(fact.subject)
                        node.labels = tuple(sorted(set(node.labels) | {label}))
            for edge_type, predicate in types.items():
                if fact.predicate != predicate:
                    continue
                if len(self._edges) >= limits.max_edges:
                    raise ReasoningLimitError("query projection exceeded max_edges")
                self._node(fact.subject)
                self._node(fact.object)
                edge_id = "logical:" + _digest([fact.subject, fact.predicate, fact.object, edge_type])
                key = edge_id.encode("utf-8")
                self._edges[key] = Edge(edge_id, fact.subject, fact.object, {
                    "type": edge_type, "predicate": predicate,
                    "reasoning_origin": "asserted" if fact in base else "inferred",
                })
                source, target = node_key_to_bytes(fact.subject), node_key_to_bytes(fact.object)
                for node_id, neighbor, direction in ((source, target, "out"), (target, source, "in")):
                    self._adjacency.setdefault((node_id, edge_type, direction), []).append(
                        {"edge_id": key, "neighbor_id": neighbor})

    def _node(self, node_id: str) -> Node:
        key = node_key_to_bytes(node_id)
        if key not in self._nodes:
            if len(self._nodes) >= self._limits.max_nodes:
                raise ReasoningLimitError("query projection exceeded max_nodes")
            self._nodes[key] = Node(node_id)
        return self._nodes[key]

    def _tick(self) -> None:
        check_cancelled(self._cancel)
        self._reads += 1
        if self._reads > self._limits.max_query_reads:
            raise ReasoningLimitError("query exceeded max_query_reads")

    def _iterate(self, items):
        for item in items:
            self._tick()
            yield item

    def get_node(self, node_id: bytes) -> Node | None:
        self._tick()
        return self._nodes.get(node_id)

    def get_edge(self, edge_id: bytes) -> Edge | None:
        self._tick()
        return self._edges.get(edge_id)

    def get_node_keys_generator(self) -> Iterator[bytes]:
        return self._iterate(sorted(self._nodes))

    def iter_node_ids_by_label(self, label: str) -> Iterator[bytes]:
        for key in self._iterate(sorted(self._nodes)):
            if label in self._nodes[key].labels:
                yield key

    def iter_edge_ids(self) -> Iterator[bytes]:
        return self._iterate(sorted(self._edges))

    def iter_typed_adjacency(self, node_id: bytes, edge_type: str, direction="out"):
        seen = set()
        for member in (("out", "in") if direction == "any" else (direction,)):
            for record in self._iterate(self._adjacency.get((node_id, edge_type, member), ())):
                if record["edge_id"] not in seen:
                    seen.add(record["edge_id"])
                    yield record

    def iter_edge_ids_by_type(self, edge_type: str) -> Iterator[bytes]:
        for key in self.iter_edge_ids():
            if self._edges[key].properties["type"] == edge_type:
                yield key

    # These are conservative candidate scans, not indexes. The runtime always
    # checks inline property maps and the residual WHERE on hydrated edges.
    def iter_edge_ids_by_type_property(self, edge_type, *args):
        return self.iter_edge_ids_by_type(edge_type)

    def iter_edge_ids_by_type_property_range(self, edge_type, *args):
        return self.iter_edge_ids_by_type(edge_type)

    def iter_edge_ids_by_property_range(self, *args):
        return self.iter_edge_ids()

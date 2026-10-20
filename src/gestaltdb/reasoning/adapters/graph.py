"""Explicit current-property-graph projection and logical-neighbor sampling.

This is not a Cypher rewrite or a sampler-array mutation. Mapping, snapshot and
closure are explicit; temporal claims never participate in this projection.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import random

from ...ontology.compiler import CompiledOntology
from ...ontology.model import TYPE_PREDICATE
from ...graphdb import _DATABASE_ID_METADATA_KEY
from ...readview import CurrentGraphReadView, CurrentReadProvenance, ProvenanceMismatchError
from ..engine import DatalogEngine
from ..model import EvaluationResult, Fact, ReasoningError, ReasoningLimitError, ReasoningLimits, _text
from ..protocols import Evidence, InputContext


def _digest(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _pairs(values, name):
    if not isinstance(values, (tuple, list)):
        raise ReasoningError(f"{name} must be explicit pairs")
    result = []
    for pair in values:
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            raise ReasoningError(f"{name} must contain pairs")
        result.append((_text(pair[0], name), _text(pair[1], name)))
    if len({a for a, _ in result}) != len(result):
        raise ReasoningError(f"{name} source keys must be unique")
    return tuple(sorted(result))


@dataclass(frozen=True)
class GraphMapping:
    """Only mapped labels/types/properties participate; properties must be entity IDs.

    ``node_properties`` maps a property key to a binary predicate, with the node
    ID as subject. Values must be single nonempty strings, never coerced literals.
    Node IDs and endpoint IDs remain unchanged. Parallel edges collapse logically.
    """

    labels: tuple[tuple[str, str], ...] = ()
    edge_types: tuple[tuple[str, str], ...] = ()
    node_properties: tuple[tuple[str, str], ...] = ()

    def __post_init__(self):
        for name in ("labels", "edge_types", "node_properties"):
            object.__setattr__(self, name, _pairs(getattr(self, name), name))

    @property
    def mapping_id(self):
        return _digest(["current-graph-mapping-v1", self.labels, self.edge_types, self.node_properties])


@dataclass(frozen=True)
class GraphSourceLimits:
    max_records: int = 100_000

    def __post_init__(self):
        if isinstance(self.max_records, bool) or not isinstance(self.max_records, int) or self.max_records < 1:
            raise ReasoningError("max_records must be a positive integer")


@dataclass(frozen=True)
class GraphReasoningResult:
    context: InputContext
    provenance: CurrentReadProvenance
    result: EvaluationResult

    def match(self, *, subject=None, predicate=None, object=None):
        """Filter logical triples, not graph records or Cypher rows."""
        return self.result.match(subject=subject, predicate=predicate, object=object)

    def sample_neighbors(self, node: str, *, predicate: str, direction: str,
                         fanout: int, seed: int) -> tuple[str, ...]:
        """Sample distinct logical endpoint IDs after full relation expansion.

        Uniform without replacement; no edge multiplicity, weights, implicit
        type expansion or automatic multi-hop work. Output is canonically sorted.
        The caller's explicit predicate can be a derived super/inverse relation.
        """
        _text(node, "node")
        _text(predicate, "predicate")
        if direction not in {"out", "in"}:
            raise ReasoningError("direction must be out or in")
        if isinstance(fanout, bool) or not isinstance(fanout, int) or fanout < 0:
            raise ReasoningError("fanout must be a non-negative integer")
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ReasoningError("seed must be an integer")
        matches = self.result.match(predicate=predicate, **({"subject": node} if direction == "out" else {"object": node}))
        candidates = sorted({fact.object if direction == "out" else fact.subject for fact in matches})
        return tuple(sorted(random.Random(seed).sample(candidates, min(fanout, len(candidates)))))


class PinnedGraphSource:
    """Borrow a caller-managed verifiable current snapshot, never temporal claims.

    Keep the read-view context open through evaluation. Closing this source does
    not close the view or graph. Snapshot authentication is checked before/after
    extraction; record limits do not bound snapshot digest cost or kernel work.
    """

    def __init__(self, view: CurrentGraphReadView, *, mapping: GraphMapping,
                 limits: GraphSourceLimits = GraphSourceLimits()):
        if type(view) is not CurrentGraphReadView or not isinstance(view.provenance, CurrentReadProvenance):
            raise ReasoningError("a verifiable CurrentGraphReadView is required")
        if not isinstance(mapping, GraphMapping) or not isinstance(limits, GraphSourceLimits):
            raise ReasoningError("explicit GraphMapping and GraphSourceLimits required")
        self._view, self._graph, self._store = view, view._graph, view.store
        self._provenance = CurrentReadProvenance.from_dict(view.provenance.to_dict())
        self._mapping, self._limits, self._closed = mapping, limits, False
        self._check()
        if self._store.state_sha256() != self._provenance.state_sha256:
            raise ProvenanceMismatchError("current snapshot state digest mismatch")

    def _check(self):
        if self._closed:
            raise ReasoningError("graph source is closed")
        if self._view._graph is not self._graph or self._view.store is not self._store:
            raise ProvenanceMismatchError("current view was rebound")
        self._provenance.verify_view(self._view)
        self._provenance.verify_snapshot_store(self._view)
        # Also probes the backend snapshot's active lifetime.
        if self._store.get_metadata(_DATABASE_ID_METADATA_KEY) != self._provenance.database_id.encode("ascii"):
            raise ProvenanceMismatchError("current snapshot database identity mismatch")

    def _evidence(self):
        labels, types, properties = dict(self._mapping.labels), dict(self._mapping.edge_types), dict(self._mapping.node_properties)
        count = 0
        for kind, keys in (("node", self._store.get_node_keys_generator()),
                           ("edge", self._store.get_edge_keys_generator())):
            for key in keys:
                self._check()
                count += 1
                if count > self._limits.max_records:
                    raise ReasoningLimitError("current graph exceeded max_records")
                record = self._view.get_node(key) if kind == "node" else self._view.get_edge(key)
                if record is None:
                    raise ReasoningError("snapshot record disappeared")
                if kind == "node":
                    for label in record.labels:
                        if label in labels:
                            fact = Fact(record.get_id, TYPE_PREDICATE, labels[label])
                            yield Evidence(fact, _digest(["node-label", record.get_id, label]))
                    for prop, predicate in properties.items():
                        if prop in record.properties:
                            fact = Fact(record.get_id, predicate, _text(record.properties[prop], "mapped entity property"))
                            yield Evidence(fact, _digest(["node-property", record.get_id, prop]))
                else:
                    edge_type = record.properties.get("type")
                    if isinstance(edge_type, str) and edge_type in types:
                        fact = Fact(record.source, types[edge_type], record.target)
                        yield Evidence(fact, _digest(["edge", record.get_id]))
        self._check()

    def evaluate(self, compiled: CompiledOntology, *, limits: ReasoningLimits, collect_proofs: bool = False):
        self._check()
        if not isinstance(compiled, CompiledOntology):
            raise ReasoningError("compiled ontology required")
        result = DatalogEngine().evaluate(compiled.program, self._evidence(), limits=limits, collect_proofs=collect_proofs)
        self._check()
        context = InputContext(
            _digest(["current-graph-context-v1", self._provenance.token, compiled.revision_id, self._mapping.mapping_id]),
            "current-property-graph", compiled.revision_id, "explicit-current-records-v1", self._mapping.mapping_id,
        )
        return GraphReasoningResult(context, self._provenance, result)

    def close(self):
        self._closed = True

    def __enter__(self):
        self._check()
        return self

    def __exit__(self, *args):
        self.close()

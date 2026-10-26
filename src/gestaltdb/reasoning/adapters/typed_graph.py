"""Typed n-ary projection of current property-graph records.

The v1 :class:`~gestaltdb.reasoning.adapters.graph.GraphMapping` covers
labels, edge types and single-string entity properties. This v2 mapping
additionally projects scalar attributes to ``Literal``-typed facts, expands
list values element-wise under an explicit bound, projects edge attributes
as n-ary ``[source, target, value]`` facts, and supports configurable
subject-identity construction. Everything stays explicit and read-only:
unmapped fields are ignored, unsupported values fail closed, and existing
v1 behavior is unchanged (v1 lives on untouched in ``adapters.graph``).

Identity construction applies to node subjects only. Edge endpoints remain
record IDs (dangling endpoints stay logical constants, as in v1), so a
mapping that renames node subjects must resolve edge endpoints through the
same identity map — :class:`PinnedTypedGraphSource` does this automatically
from the node pass; callers of the pure helpers pass an explicit resolver.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from ...graphdb import _DATABASE_ID_METADATA_KEY
from ...ontology.model import TYPE_PREDICATE
from ...readview import CurrentGraphReadView, CurrentReadProvenance, ProvenanceMismatchError
from ..model import ReasoningError, ReasoningLimitError, _text, check_cancelled
from ..protocols import InputContext
from ..terms import Literal
from ..typed import TypedFact
from .graph import GraphSourceLimits, _digest, _pairs


def _literal(value, what: str) -> Literal:
    """Coerce a scalar property value to a Literal; fail closed otherwise."""
    if isinstance(value, bool):
        return Literal("boolean", value)
    if isinstance(value, int):
        return Literal("integer", value)
    if isinstance(value, float):
        return Literal("float", value)
    if isinstance(value, str):
        return Literal("text", value)
    raise ReasoningError(f"{what} must be a str, int, float or bool scalar, got {type(value).__name__}")


def _entity(value, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReasoningError(f"{what} must be a non-empty entity string")
    return value


def _reference(kind: str, *parts) -> str:
    """Unambiguous source reference: JSON components, never delimiter joins.

    Node IDs and property names may contain ``:``. Joining them textually makes
    ``("a:b", "c")`` and ``("a", "b:c")`` indistinguishable; canonical JSON
    keeps every component separate while staying readable and deterministic.
    """
    return f"typed:{kind}:" + json.dumps(list(parts), ensure_ascii=True, separators=(",", ":"))



@dataclass(frozen=True)
class TypedEvidence:
    """One projected typed fact with a traceable source reference.

    ``reference_id`` is canonical JSON after a ``typed:<kind>:`` prefix, e.g.
    ``typed:node-attribute:["id","name"]``; list elements append their index.
    Components are never joined by a delimiter, so IDs or property names that
    contain ``:`` cannot alias another record's reference.
    """

    fact: TypedFact
    reference_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.fact, TypedFact):
            raise ReasoningError("typed evidence fact must be a TypedFact")
        _text(self.reference_id, "reference_id")


@dataclass(frozen=True)
class TypedGraphMapping:
    """Allowlist projection from graph records to typed facts.

    ``labels``, ``edge_types`` and ``node_properties`` keep exact v1
    semantics (entity strings only, never coerced). ``node_attributes``
    coerces scalar values to Literals; ``node_lists`` expands list values to
    one Literal fact per element; ``node_reference_lists`` expands lists of
    entity strings. ``edge_attributes`` projects scalar edge fields as
    ``predicate(source, target, value)`` facts; ``edge_lists`` and
    ``edge_reference_lists`` expand list-valued edge fields the same way.
    ``node_identity`` holds ``(label, property)`` pairs: nodes carrying that
    label project under their property value instead of the node ID. At most
    one identity rule may match a node; missing or mistyped identity values
    fail closed.

    ``identity_collision`` controls what happens when two distinct records map
    to one subject. The default ``"error"`` fails closed, naming both records.
    ``"merge"`` is an explicit entity-resolution choice: facts merge, while
    each projected assertion keeps its own source reference. Merging is never
    inferred and never equated with logical ``sameAs``.
    """

    labels: tuple[tuple[str, str], ...] = ()
    edge_types: tuple[tuple[str, str], ...] = ()
    node_properties: tuple[tuple[str, str], ...] = ()
    node_attributes: tuple[tuple[str, str], ...] = ()
    node_lists: tuple[tuple[str, str], ...] = ()
    node_reference_lists: tuple[tuple[str, str], ...] = ()
    edge_attributes: tuple[tuple[str, str], ...] = ()
    edge_lists: tuple[tuple[str, str], ...] = ()
    edge_reference_lists: tuple[tuple[str, str], ...] = ()
    node_identity: tuple[tuple[str, str], ...] = ()
    identity_collision: str = "error"

    def __post_init__(self):
        for name in ("labels", "edge_types", "node_properties", "node_attributes",
                     "node_lists", "node_reference_lists", "edge_attributes",
                     "edge_lists", "edge_reference_lists", "node_identity"):
            object.__setattr__(self, name, _pairs(getattr(self, name), name))
        if self.identity_collision not in ("error", "merge"):
            raise ReasoningError("identity_collision must be 'error' or 'merge'")

    @property
    def mapping_id(self) -> str:
        return _digest(["current-typed-graph-mapping-v3", self.labels, self.edge_types,
                        self.node_properties, self.node_attributes, self.node_lists,
                        self.node_reference_lists, self.edge_attributes, self.edge_lists,
                        self.edge_reference_lists, self.node_identity,
                        self.identity_collision])



@dataclass(frozen=True)
class TypedSourceLimits:
    """Record and per-value expansion bounds; overruns fail closed."""

    max_records: int = 100_000
    max_elements_per_value: int = 128

    def __post_init__(self):
        for name in ("max_records", "max_elements_per_value"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ReasoningError(f"{name} must be a positive integer")


def _subject(record, mapping: TypedGraphMapping) -> str:
    """Node subject under identity construction; ambiguity fails closed."""
    identities = [(label, prop) for label, prop in mapping.node_identity if label in record.labels]
    if not identities:
        return record.get_id
    if len(identities) > 1:
        raise ReasoningError(f"ambiguous node identity for {record.get_id!r}: {identities!r}")
    _label, prop = identities[0]
    return _entity(record.properties.get(prop), f"identity property {prop!r} on {record.get_id!r}")


def _expand_list(value, what: str, max_elements: int) -> tuple:
    if not isinstance(value, (list, tuple)):
        raise ReasoningError(f"{what} must be a list value to expand")
    if len(value) > max_elements:
        raise ReasoningLimitError(f"{what} expands to {len(value)} elements over limit {max_elements}")
    return tuple(value)


def _typed_node_evidence(record, mapping: TypedGraphMapping, limits: TypedSourceLimits,
                         *, subject: str | None = None):
    """Project one node record; pure over duck-typed records for testability."""
    subject = _subject(record, mapping) if subject is None else subject
    labels = dict(mapping.labels)
    for label in record.labels:
        if label in labels:
            yield TypedEvidence(TypedFact(TYPE_PREDICATE, [subject, labels[label]]),
                                _reference("node-label", record.get_id, label))
    for prop, predicate in mapping.node_properties:
        if prop in record.properties:
            yield TypedEvidence(TypedFact(predicate, [subject, _entity(
                record.properties[prop], f"mapped entity property {prop!r} on {record.get_id!r}")]),
                _reference("node-property", record.get_id, prop))
    for prop, predicate in mapping.node_attributes:
        if prop in record.properties:
            yield TypedEvidence(TypedFact(predicate, [subject, _literal(
                record.properties[prop], f"mapped attribute {prop!r} on {record.get_id!r}")]),
                _reference("node-attribute", record.get_id, prop))
    for prop, predicate in mapping.node_lists:
        if prop in record.properties:
            for index, item in enumerate(_expand_list(
                    record.properties[prop], f"mapped list {prop!r} on {record.get_id!r}",
                    limits.max_elements_per_value)):
                yield TypedEvidence(TypedFact(predicate, [subject, _literal(
                    item, f"mapped list {prop!r}[{index}] on {record.get_id!r}")]),
                    _reference("node-list", record.get_id, prop, index))
    for prop, predicate in mapping.node_reference_lists:
        if prop in record.properties:
            for index, item in enumerate(_expand_list(
                    record.properties[prop], f"mapped reference list {prop!r} on {record.get_id!r}",
                    limits.max_elements_per_value)):
                yield TypedEvidence(TypedFact(predicate, [subject, _entity(
                    item, f"mapped reference list {prop!r}[{index}] on {record.get_id!r}")]),
                    _reference("node-reference-list", record.get_id, prop, index))



def _typed_edge_evidence(record, mapping: TypedGraphMapping, limits: TypedSourceLimits,
                         resolve: Callable[[str], str] | None = None):
    """Project one edge record as binary plus n-ary attribute facts."""
    resolve = resolve or (lambda endpoint: endpoint)
    source, target = resolve(record.source), resolve(record.target)
    types = dict(mapping.edge_types)
    edge_type = record.properties.get("type")
    if isinstance(edge_type, str) and edge_type in types:
        yield TypedEvidence(TypedFact(types[edge_type], [source, target]),
                            _reference("edge", record.get_id))
        for attr, predicate in mapping.edge_attributes:
            if attr in record.properties:
                yield TypedEvidence(TypedFact(predicate, [source, target, _literal(
                    record.properties[attr], f"mapped edge attribute {attr!r} on {record.get_id!r}")]),
                    _reference("edge-attribute", record.get_id, attr))
        for attr, predicate in mapping.edge_lists:
            if attr in record.properties:
                for index, item in enumerate(_expand_list(
                        record.properties[attr], f"mapped edge list {attr!r} on {record.get_id!r}",
                        limits.max_elements_per_value)):
                    yield TypedEvidence(TypedFact(predicate, [source, target, _literal(
                        item, f"mapped edge list {attr!r}[{index}] on {record.get_id!r}")]),
                        _reference("edge-list", record.get_id, attr, index))
        for attr, predicate in mapping.edge_reference_lists:
            if attr in record.properties:
                for index, item in enumerate(_expand_list(
                        record.properties[attr], f"mapped edge reference list {attr!r} on {record.get_id!r}",
                        limits.max_elements_per_value)):
                    yield TypedEvidence(TypedFact(predicate, [source, target, _entity(
                        item, f"mapped edge reference list {attr!r}[{index}] on {record.get_id!r}")]),
                        _reference("edge-reference-list", record.get_id, attr, index))
    elif mapping.edge_attributes or mapping.edge_lists or mapping.edge_reference_lists:
        attrs = [attr for attr, _ in (*mapping.edge_attributes, *mapping.edge_lists,
                                      *mapping.edge_reference_lists) if attr in record.properties]
        if attrs:
            raise ReasoningError(
                f"edge {record.get_id!r} carries mapped attributes {attrs!r} but its type is unmapped")


def _typed_graph_context(provenance, revision: str, mapping: TypedGraphMapping) -> InputContext:
    return InputContext(
        _digest(["current-typed-graph-context-v1", provenance.token, revision, mapping.mapping_id]),
        "current-property-graph", revision, "explicit-current-records-v1", mapping.mapping_id)


class PinnedTypedGraphSource:
    """Borrow a caller-managed verifiable snapshot; project typed evidence.

    Same authentication contract as
    :class:`~gestaltdb.reasoning.adapters.graph.PinnedGraphSource`: the
    read-view context stays open through iteration, backend/serializer/state
    identity is checked before and after, and record limits do not bound
    snapshot digest cost. Edge endpoints resolve through the node-pass
    identity map, so identity-renamed nodes join with their edges.
    """

    def __init__(self, view: CurrentGraphReadView, *, mapping: TypedGraphMapping,
                 limits: TypedSourceLimits = TypedSourceLimits(),
                 source_limits: GraphSourceLimits = GraphSourceLimits()):
        if type(view) is not CurrentGraphReadView or not isinstance(view.provenance, CurrentReadProvenance):
            raise ReasoningError("a verifiable CurrentGraphReadView is required")
        if not isinstance(mapping, TypedGraphMapping):
            raise ReasoningError("explicit TypedGraphMapping required")
        if not isinstance(limits, TypedSourceLimits) or not isinstance(source_limits, GraphSourceLimits):
            raise ReasoningError("explicit TypedSourceLimits and GraphSourceLimits required")
        self._view, self._graph, self._store = view, view._graph, view.store
        self._serializer = self._graph.serializer
        self._provenance = CurrentReadProvenance.from_dict(view.provenance.to_dict())
        self._mapping, self._limits, self._source_limits, self._closed = mapping, limits, source_limits, False
        self._check()
        manifest = self._graph.manifest
        if (manifest["backend"]["name"] != self._provenance.backend_name
                or manifest["backend"]["layout_version"] != self._provenance.backend_layout_version
                or manifest["serializer"]["name"] != self._provenance.serializer_name
                or manifest["serializer"]["format_version"] != self._provenance.serializer_format_version):
            raise ProvenanceMismatchError("current snapshot backend/serializer identity mismatch")
        if self._store.state_sha256() != self._provenance.state_sha256:
            raise ProvenanceMismatchError("current snapshot state digest mismatch")

    def _check(self):
        if self._closed:
            raise ReasoningError("typed graph source is closed")
        if (self._view._graph is not self._graph or self._view.store is not self._store
                or self._graph.serializer is not self._serializer):
            raise ProvenanceMismatchError("current view was rebound")
        self._provenance.verify_view(self._view)
        self._provenance.verify_snapshot_store(self._view)
        if self._store.get_metadata(_DATABASE_ID_METADATA_KEY) != self._provenance.database_id.encode("ascii"):
            raise ProvenanceMismatchError("current snapshot database identity mismatch")

    def iter_evidence(self, *, cancel: Callable[[], bool] | None = None):
        """Yield TypedEvidence in store order; limits fail closed, never truncate.

        Every node resolves to one subject before projecting, so identity
        mapping applies even to nodes with no mapped properties. A subject
        owned by two distinct records is an identity collision: it fails closed
        by default, or merges when the caller set ``identity_collision="merge"``.
        Edges resolve endpoints through the record-id -> subject map, including
        when an identity-derived subject coincides with another record's ID.
        """
        self._check()
        count = 0
        owners: dict[str, str] = {}
        subject_by_id: dict[str, str] = {}
        node_keys = list(self._view.store.get_node_keys_generator())
        for key in node_keys:
            check_cancelled(cancel)
            self._check()
            count += 1
            if count > self._source_limits.max_records:
                raise ReasoningLimitError("current graph exceeded max_records")
            record = self._view.get_node(key)
            check_cancelled(cancel)
            self._check()
            if record is None:
                raise ReasoningError("snapshot record disappeared")
            node_id = record.get_id
            subject = _subject(record, self._mapping)
            owner = owners.get(subject)
            if owner is not None and owner != node_id:
                if self._mapping.identity_collision == "error":
                    raise ReasoningError(
                        f"identity collision: records {owner!r} and {node_id!r} both "
                        f"project subject {subject!r}; set identity_collision='merge' "
                        f"for intentional entity resolution")
            else:
                owners[subject] = node_id
            subject_by_id[node_id] = subject
            yield from _typed_node_evidence(record, self._mapping, self._limits,
                                            subject=subject)
        for key in self._view.store.get_edge_keys_generator():
            check_cancelled(cancel)
            self._check()
            count += 1
            if count > self._source_limits.max_records:
                raise ReasoningLimitError("current graph exceeded max_records")
            record = self._view.get_edge(key)
            check_cancelled(cancel)
            self._check()
            if record is None:
                raise ReasoningError("snapshot record disappeared")
            yield from _typed_edge_evidence(record, self._mapping, self._limits,
                                            resolve=lambda endpoint: subject_by_id.get(endpoint,
                                                                                      endpoint))
        self._check()

    def context(self, revision: str) -> InputContext:
        """Generation-pinning context; a mapping change yields a new token."""
        self._check()
        _text(revision, "revision")
        return _typed_graph_context(self._provenance, revision, self._mapping)

    def close(self):
        self._closed = True

    def __enter__(self):
        self._check()
        return self

    def __exit__(self, *args):
        self.close()


__all__ = ["PinnedTypedGraphSource", "TypedEvidence", "TypedGraphMapping", "TypedSourceLimits"]

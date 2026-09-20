"""Deterministic visualization intermediate representation (VIZ-01).

Every visualization surface (static HTML files, Jupyter cells, client-side
PNG/SVG/JSON export) renders the same JSON payload produced here. Builders
accept ``Node``/``Edge`` objects (or their ``to_dict()`` shapes), Cypher
``QueryResult`` rows, ``sample_typed_subgraph`` mappings, and
``SampledSubgraphBatch`` + ``SamplerSnapshot`` pairs.

Standard library only: this module must not gain mandatory third-party
runtime dependencies.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Mapping

#: Version of the payload schema consumed by the ``web/`` front end.
VIZ_IR_VERSION = 1

#: Default cap: maximum nodes kept in a payload before deterministic truncation.
DEFAULT_MAX_NODES = 2000

#: Default cap: maximum edges kept in a payload before deterministic truncation.
DEFAULT_MAX_EDGES = 5000

#: Passthrough property names preserved verbatim on nodes/edges when present.
RESERVED_EXTRA_KEYS = ("valid", "score", "highlight")


def _sanitize_id(value: Any) -> str:
    """Coerce an entity ID to ``str`` without ever raising.

    Args:
        value: Raw ID which may be ``str``, ``bytes``, or a scalar.

    Returns:
        Deterministic string ID (bytes decode as UTF-8 with replacement).
    """
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if value is None:
        return ""
    return str(value)


def _sanitize_label(value: Any) -> str:
    """Coerce a single label to ``str``."""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _sanitize_labels(values: Any) -> tuple[str, ...]:
    """Deduplicate labels preserving first-seen order, dropping empties."""
    if values is None:
        return ()
    if isinstance(values, (str, bytes)):
        values = (values,)
    try:
        iterator = iter(values)
    except TypeError:
        return ()
    seen: dict[str, None] = {}
    for raw in iterator:
        label = _sanitize_label(raw)
        if label and label not in seen:
            seen[label] = None
    return tuple(seen)


def _sanitize_property_value(value: Any) -> tuple[Any, bool]:
    """Coerce a property value to a JSON-safe primitive.

    Returns:
        Tuple of ``(safe_value, coerced)`` where ``coerced`` is True when the
        value was stringified or otherwise transformed.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value, False
    if isinstance(value, float):
        if math.isfinite(value):
            return value, False
        return str(value), True
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8"), False
        except UnicodeDecodeError:
            return value.decode("utf-8", errors="replace"), True
    if isinstance(value, (list, tuple)):
        items = []
        coerced = False
        for item in value:
            safe_item, item_coerced = _sanitize_property_value(item)
            items.append(safe_item)
            coerced = coerced or item_coerced
        return items, coerced
    if isinstance(value, Mapping):
        safe_map: dict[str, Any] = {}
        coerced = False
        for key, item in value.items():
            safe_item, item_coerced = _sanitize_property_value(item)
            safe_map[_sanitize_id(key)] = safe_item
            coerced = coerced or item_coerced
        return safe_map, coerced
    return str(value), True


def _sanitize_properties(properties: Any) -> dict[str, Any]:
    """Coerce a properties mapping to JSON-safe string-keyed properties."""
    if not isinstance(properties, Mapping):
        return {}
    safe: dict[str, Any] = {}
    for key, value in properties.items():
        safe_value, _ = _sanitize_property_value(value)
        safe[_sanitize_id(key)] = safe_value
    return safe


def _split_extra(properties: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split reserved passthrough keys out of sanitized properties."""
    regular = {key: value for key, value in properties.items() if key not in RESERVED_EXTRA_KEYS}
    extra = {key: properties[key] for key in RESERVED_EXTRA_KEYS if key in properties}
    return regular, extra


def _entity_id(raw: Any) -> str | None:
    """Return the stable entity ID for Node/Edge-like objects or dicts."""
    get_id = getattr(raw, "get_id", None)
    if get_id is not None:
        return _sanitize_id(get_id() if callable(get_id) else get_id)
    if isinstance(raw, Mapping) and "id" in raw:
        return _sanitize_id(raw.get("id"))
    return None


def _node_like_to_parts(raw: Any) -> tuple[str, tuple[str, ...], dict[str, Any], dict[str, Any]] | None:
    """Normalize a Node object or node-shaped dict to IR parts.

    Returns:
        ``(id, labels, properties, extra)`` or ``None`` when unrecognised.
    """
    node_id = _entity_id(raw)
    if node_id is None:
        return None
    if isinstance(raw, Mapping):
        if "source" in raw and "target" in raw:
            return None  # edge-shaped dicts are not nodes.
    elif hasattr(raw, "source") and hasattr(raw, "target"):
        return None  # Edge-like objects are not nodes.
    if isinstance(raw, Mapping) and not hasattr(raw, "labels"):
        labels = _sanitize_labels(raw.get("labels", ()))
        props = _sanitize_properties(raw.get("properties", {}))
    else:
        labels = _sanitize_labels(getattr(raw, "labels", ()))
        props = _sanitize_properties(getattr(raw, "properties", {}))
    regular, extra = _split_extra(props)
    return node_id, labels, regular, extra


def _edge_like_to_parts(raw: Any) -> tuple[str, str, str, dict[str, Any], dict[str, Any]] | None:
    """Normalize an Edge object or edge-shaped dict to IR parts.

    Returns:
        ``(id, source, target, properties, extra)`` or ``None``.
    """
    edge_id = _entity_id(raw)
    if edge_id is None:
        return None
    if isinstance(raw, Mapping) and not hasattr(raw, "source"):
        if "source" not in raw or "target" not in raw:
            return None
        source = _sanitize_id(raw.get("source"))
        target = _sanitize_id(raw.get("target"))
        props = _sanitize_properties(raw.get("properties", {}))
    elif not isinstance(raw, Mapping) and not (hasattr(raw, "source") and hasattr(raw, "target")):
        return None
    else:
        source = _sanitize_id(getattr(raw, "source", ""))
        target = _sanitize_id(getattr(raw, "target", ""))
        props = _sanitize_properties(getattr(raw, "properties", {}))
    regular, extra = _split_extra(props)
    return edge_id, source, target, regular, extra


def _is_path_like(value: Any) -> bool:
    """Return True for Cypher ``PathValue``-shaped objects."""
    return hasattr(value, "nodes") and hasattr(value, "edges") and not isinstance(value, Mapping)


@dataclass(frozen=True)
class VizNode:
    """A single node in the visualization IR."""

    id: str
    labels: tuple[str, ...] = ()
    properties: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def group(self) -> str:
        """Primary group used for coloring (first label, or ``""``)."""
        return self.labels[0] if self.labels else ""

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-safe node payload."""
        return {
            "id": self.id,
            "labels": list(self.labels),
            "group": self.group,
            "properties": dict(self.properties),
            "extra": dict(self.extra),
        }


@dataclass(frozen=True)
class VizEdge:
    """A single directed edge in the visualization IR."""

    id: str
    source: str
    target: str
    type: str = ""
    properties: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-safe edge payload."""
        return {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "type": self.type,
            "properties": dict(self.properties),
            "extra": dict(self.extra),
        }


@dataclass(frozen=True)
class TruncationInfo:
    """Exact accounting of deterministic cap enforcement."""

    nodes_dropped: int = 0
    edges_dropped: int = 0

    @property
    def truncated(self) -> bool:
        """Whether any node or edge was dropped."""
        return self.nodes_dropped > 0 or self.edges_dropped > 0

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-safe truncation payload."""
        return {
            "nodes_dropped": self.nodes_dropped,
            "edges_dropped": self.edges_dropped,
            "truncated": self.truncated,
        }


@dataclass(frozen=True)
class VizGraph:
    """Deterministic, JSON-serializable graph payload for the front end."""

    nodes: tuple[VizNode, ...] = ()
    edges: tuple[VizEdge, ...] = ()
    highlight_nodes: tuple[str, ...] = ()
    highlight_edges: tuple[str, ...] = ()
    truncation: TruncationInfo = field(default_factory=TruncationInfo)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return the full JSON-safe payload consumed by the front end."""
        return {
            "version": VIZ_IR_VERSION,
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "highlight": {
                "nodes": list(self.highlight_nodes),
                "edges": list(self.highlight_edges),
            },
            "truncation": self.truncation.to_dict(),
            "meta": dict(self.meta),
        }

    def to_json(self) -> str:
        """Return the canonical JSON encoding (sorted keys, compact)."""
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    @property
    def node_ids(self) -> tuple[str, ...]:
        """Node IDs in payload order."""
        return tuple(node.id for node in self.nodes)

    @property
    def edge_ids(self) -> tuple[str, ...]:
        """Edge IDs in payload order."""
        return tuple(edge.id for edge in self.edges)

    # -- core assembly ----------------------------------------------------

    @classmethod
    def from_nodes_edges(
        cls,
        nodes: Any,
        edges: Any,
        *,
        max_nodes: int = DEFAULT_MAX_NODES,
        max_edges: int = DEFAULT_MAX_EDGES,
        highlight_nodes: Any = (),
        highlight_edges: Any = (),
        meta: Mapping[str, Any] | None = None,
    ) -> "VizGraph":
        """Build a payload from node/edge objects or dict shapes.

        Nodes sort by stable ID and keep the first ``max_nodes``; edges sort
        by ``(source, target, id)`` and keep the first ``max_edges``. Edges
        touching a dropped node are dropped as well and counted in
        ``edges_dropped``. Duplicate IDs keep their first occurrence.
        """
        node_map: dict[str, VizNode] = {}
        for raw in nodes or ():
            parts = _node_like_to_parts(raw)
            if parts is None:
                continue
            node_id, labels, properties, extra = parts
            if node_id not in node_map:
                node_map[node_id] = VizNode(id=node_id, labels=labels, properties=properties, extra=extra)

        ordered_node_ids = sorted(node_map)
        kept_node_ids = set(ordered_node_ids[:max_nodes])
        nodes_dropped = len(ordered_node_ids) - len(kept_node_ids)

        edge_map: dict[str, VizEdge] = {}
        for raw in edges or ():
            parts = _edge_like_to_parts(raw)
            if parts is None:
                continue
            edge_id, source, target, properties, extra = parts
            if edge_id in edge_map:
                continue
            edge_type = properties.get("type")
            edge_map[edge_id] = VizEdge(
                id=edge_id,
                source=source,
                target=target,
                type=edge_type if isinstance(edge_type, str) else "",
                properties=properties,
                extra=extra,
            )

        ordered_edges = sorted(edge_map.values(), key=lambda edge: (edge.source, edge.target, edge.id))
        kept_edges: list[VizEdge] = []
        edges_dropped = 0
        for edge in ordered_edges:
            if edge.source not in kept_node_ids or edge.target not in kept_node_ids:
                edges_dropped += 1
                continue
            if len(kept_edges) >= max_edges:
                edges_dropped += 1
                continue
            kept_edges.append(edge)

        kept_nodes = [node_map[node_id] for node_id in ordered_node_ids if node_id in kept_node_ids]
        highlight_node_ids = tuple(sorted({_sanitize_id(value) for value in highlight_nodes or ()} & kept_node_ids))
        kept_edge_ids = {edge.id for edge in kept_edges}
        highlight_edge_ids = tuple(sorted({_sanitize_id(value) for value in highlight_edges or ()} & kept_edge_ids))

        payload_meta = dict(meta or {})
        payload_meta.setdefault("builder", "from_nodes_edges")
        payload_meta.setdefault("node_count", len(kept_nodes))
        payload_meta.setdefault("edge_count", len(kept_edges))
        return cls(
            nodes=tuple(kept_nodes),
            edges=tuple(kept_edges),
            highlight_nodes=highlight_node_ids,
            highlight_edges=highlight_edge_ids,
            truncation=TruncationInfo(nodes_dropped=nodes_dropped, edges_dropped=edges_dropped),
            meta=payload_meta,
        )

    # -- domain builders --------------------------------------------------

    @classmethod
    def from_cypher_result(
        cls,
        result: Any,
        *,
        max_nodes: int = DEFAULT_MAX_NODES,
        max_edges: int = DEFAULT_MAX_EDGES,
        meta: Mapping[str, Any] | None = None,
    ) -> "VizGraph":
        """Build a payload from a Cypher ``QueryResult``.

        ``Node``/``Edge``/``PathValue`` cell values become graph elements and
        populate the highlight set; node/edge-shaped dicts are accepted too.
        Scalar-only projections yield an empty topology and record their
        columns in ``meta`` so the outcome stays explainable.
        """
        records = getattr(result, "records", result) or ()
        columns = tuple(getattr(result, "columns", ()) or ())
        node_raws: dict[str, Any] = {}
        edge_raws: dict[str, Any] = {}
        highlight_nodes: set[str] = set()
        highlight_edges: set[str] = set()

        def _absorb(value: Any, highlight: bool) -> None:
            if value is None:
                return
            if _is_path_like(value):
                for node in value.nodes or ():
                    _absorb(node, highlight)
                for edge in value.edges or ():
                    _absorb(edge, highlight)
                return
            parts = _node_like_to_parts(value)
            if parts is not None:
                node_id = parts[0]
                node_raws.setdefault(node_id, value)
                if highlight:
                    highlight_nodes.add(node_id)
                return
            edge_parts = _edge_like_to_parts(value)
            if edge_parts is not None:
                edge_id, source, target = edge_parts[0], edge_parts[1], edge_parts[2]
                edge_raws.setdefault(edge_id, value)
                if highlight:
                    highlight_edges.add(edge_id)
                for endpoint in (source, target):
                    if endpoint and endpoint not in node_raws:
                        node_raws[endpoint] = {"id": endpoint}
                        if highlight:
                            highlight_nodes.add(endpoint)
                return
            if isinstance(value, (list, tuple)):
                for item in value:
                    _absorb(item, highlight)

        for record in records:
            values = record.values() if isinstance(record, Mapping) else (record,)
            for value in values:
                _absorb(value, True)

        payload_meta = dict(meta or {})
        payload_meta["builder"] = "from_cypher_result"
        payload_meta["columns"] = list(columns)
        if not node_raws and not edge_raws:
            payload_meta["note"] = "scalar-only projection carries no graph topology"
        return cls.from_nodes_edges(
            list(node_raws.values()),
            list(edge_raws.values()),
            max_nodes=max_nodes,
            max_edges=max_edges,
            highlight_nodes=sorted(highlight_nodes),
            highlight_edges=sorted(highlight_edges),
            meta=payload_meta,
        )

    @classmethod
    def from_sampled_subgraph(
        cls,
        subgraph: Mapping[str, Any],
        *,
        max_nodes: int = DEFAULT_MAX_NODES,
        max_edges: int = DEFAULT_MAX_EDGES,
        meta: Mapping[str, Any] | None = None,
    ) -> "VizGraph":
        """Build a payload from a ``sample_typed_subgraph`` mapping.

        Missing (``None``) node/edge entries become ID-only placeholders so
        sampled paths never reference absent endpoints. Nodes and edges that
        appear on sampled paths populate the highlight set.
        """
        raw_nodes = (subgraph or {}).get("nodes", {}) or {}
        raw_edges = (subgraph or {}).get("edges", {}) or {}
        paths = (subgraph or {}).get("paths", ()) or ()

        node_values: list[Any] = []
        if isinstance(raw_nodes, Mapping):
            for key, node in raw_nodes.items():
                node_values.append(node if node is not None else {"id": _sanitize_id(key)})
        else:
            node_values.extend(raw_nodes)

        edge_values: list[Any] = []
        if isinstance(raw_edges, Mapping):
            for key, edge in raw_edges.items():
                edge_values.append(edge if edge is not None else {"id": _sanitize_id(key)})
        else:
            edge_values.extend(raw_edges)

        highlight_nodes: set[str] = set()
        highlight_edges: set[str] = set()
        for sampled in paths:
            if not isinstance(sampled, Mapping):
                continue
            seed = sampled.get("seed")
            if seed is not None:
                highlight_nodes.add(_sanitize_id(seed))
            for hop in sampled.get("path", ()) or ():
                if not isinstance(hop, Mapping):
                    continue
                for key in ("source_id", "target_id", "neighbor_id"):
                    if hop.get(key) is not None:
                        highlight_nodes.add(_sanitize_id(hop[key]))
                if hop.get("edge_id") is not None:
                    highlight_edges.add(_sanitize_id(hop["edge_id"]))

        # Endpoints referenced only by hops must exist as placeholders.
        known_node_ids = {_sanitize_id(key) for key in raw_nodes} if isinstance(raw_nodes, Mapping) else set()
        for node_id in sorted(highlight_nodes - known_node_ids):
            node_values.append({"id": node_id})

        payload_meta = dict(meta or {})
        payload_meta["builder"] = "from_sampled_subgraph"
        payload_meta["path_count"] = len(list(paths))
        return cls.from_nodes_edges(
            node_values,
            edge_values,
            max_nodes=max_nodes,
            max_edges=max_edges,
            highlight_nodes=sorted(highlight_nodes),
            highlight_edges=sorted(highlight_edges),
            meta=payload_meta,
        )

    @classmethod
    def from_sampler_batch(
        cls,
        batch: Any,
        snapshot: Any,
        *,
        max_nodes: int = DEFAULT_MAX_NODES,
        max_edges: int = DEFAULT_MAX_EDGES,
        meta: Mapping[str, Any] | None = None,
    ) -> "VizGraph":
        """Build a payload from a ``SampledSubgraphBatch`` and snapshot.

        Local batch rows map back through ``node_ids_global`` to compact
        global IDs and then to external IDs via
        ``snapshot.external_node_id(...)``; relation IDs resolve via
        ``snapshot.external_relation_id(...)``. ``numpy`` is used only through
        duck typing (``tolist``/indexing), so this module keeps no mandatory
        third-party imports.
        """
        def _as_list(value: Any) -> list[Any]:
            if value is None:
                return []
            tolist = getattr(value, "tolist", None)
            if callable(tolist):
                return list(tolist())
            return list(value)

        node_ids_global = _as_list(getattr(batch, "node_ids_global", ()))
        senders = _as_list(getattr(batch, "senders", ()))
        receivers = _as_list(getattr(batch, "receivers", ()))
        edge_ids_global = _as_list(getattr(batch, "edge_ids_global", ()))
        edge_relation_ids = _as_list(getattr(batch, "edge_relation_ids", ()))

        external_nodes = [snapshot.external_node_id(int(global_id)) for global_id in node_ids_global]
        local_to_external = {index: _sanitize_id(external) for index, external in enumerate(external_nodes)}

        node_values: list[Any] = [{"id": external} for external in external_nodes]
        edge_values: list[Any] = []
        for position, (sender, receiver) in enumerate(zip(senders, receivers)):
            source = local_to_external.get(int(sender), _sanitize_id(sender))
            target = local_to_external.get(int(receiver), _sanitize_id(receiver))
            relation = ""
            if position < len(edge_relation_ids):
                relation = snapshot.external_relation_id(int(edge_relation_ids[position]))
            edge_id = (
                snapshot.external_edge_id(int(edge_ids_global[position]))
                if position < len(edge_ids_global) and hasattr(snapshot, "external_edge_id")
                else f"batch-edge-{position}"
            )
            properties: dict[str, Any] = {}
            if relation:
                properties["type"] = relation
            edge_values.append({"id": edge_id, "source": source, "target": target, "properties": properties})

        payload_meta = dict(meta or {})
        payload_meta["builder"] = "from_sampler_batch"
        payload_meta["batch_nodes"] = len(external_nodes)
        payload_meta["batch_edges"] = len(edge_values)
        return cls.from_nodes_edges(
            node_values,
            edge_values,
            max_nodes=max_nodes,
            max_edges=max_edges,
            meta=payload_meta,
        )

"""Materialized NetworkX conversion and hardened static GEXF loading.

Install ``gestaltdb[networkx]``. These helpers never close the caller's database.
See ``docs/networkx.rst`` for the mapping and partial-write contracts.
"""
from __future__ import annotations

import bz2
from copy import deepcopy
from dataclasses import dataclass, field
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
from typing import Any, Callable
from xml.etree import ElementTree

from gestaltdb.graphdb import (
    Edge, GraphDB, Node, _property_value_to_index_bytes,
    _property_value_to_range_index_bytes,
)
from gestaltdb.kvstores import (
    _checked_index_key, _index_key, _range_index_key, _typed_adjacency_key,
)

__all__ = ["ImportResult", "ImportWriteError", "from_networkx", "to_networkx", "load_gexf"]

_META = "__gestaltdb_networkx__"


def _networkx():
    try:
        import networkx
    except ImportError as exc:
        raise ImportError("NetworkX integration requires pip install 'gestaltdb[networkx]'") from exc
    return networkx


@dataclass
class ImportResult:
    """Import counts and mappings; graph attributes are returned, not persisted.

    ``edge_ids[(source, target, key)]`` contains one ID for directed edges and
    one or two IDs for bidirectional expansion. Simple graphs use key ``None``.
    """

    source_nodes: int
    source_edges: int
    imported_nodes: int = 0
    imported_edges: int = 0
    node_ids: dict = field(default_factory=dict)
    edge_ids: dict = field(default_factory=dict)
    graph_attributes: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    direction_conversion: str = "none"


class ImportWriteError(RuntimeError):
    """A write failed. Completed batches are in ``result``; the failing batch
    may also have written data. Quiesce other writers and inspect before retrying.
    """

    def __init__(self, result: ImportResult, phase: str, batch_start: int):
        super().__init__(f"{phase} batch at offset {batch_start} failed; destination may contain partial writes")
        self.result = result
        self.phase = phase
        self.batch_start = batch_start


def _key_token(value):
    # Tagged keys distinguish int/string IDs and preserve tuple multiedge keys.
    if type(value) in (str, int, bool) or value is None:
        return [type(value).__name__, value]
    if type(value) is float and math.isfinite(value):
        return ["float", value]
    if type(value) is tuple:
        return ["tuple", [_key_token(item) for item in value]]
    raise TypeError("NetworkX keys must be finite scalars or tuples; map custom node keys with node_id")


def _decode_key(token):
    if not isinstance(token, list) or len(token) != 2:
        raise ValueError("Invalid integration key metadata")
    kind, value = token
    if kind == "tuple":
        if not isinstance(value, list):
            raise ValueError("Invalid integration tuple metadata")
        return tuple(_decode_key(item) for item in value)
    types = {"str": str, "int": int, "bool": bool, "NoneType": type(None), "float": float}
    if kind not in types or type(value) is not types[kind]:
        raise ValueError("Invalid integration key metadata")
    if kind == "float" and not math.isfinite(value):
        raise ValueError("Invalid integration key metadata")
    return value


def _pop_export_metadata(attrs, fields):
    if _META not in attrs:
        return None
    meta = attrs.pop(_META)
    if not isinstance(meta, dict) or set(meta) != fields:
        raise ValueError(f"Reserved attribute {_META} conflicts with export metadata")
    _identifier(meta["id"])
    if "labels" in fields:
        _labels(meta["labels"])
    return meta


def _pop_stored_key(attrs, fallback):
    if _META not in attrs:
        return fallback
    meta = attrs.pop(_META)
    if not isinstance(meta, dict) or set(meta) != {"key"}:
        raise ValueError(f"Invalid stored {_META} metadata")
    return _decode_key(meta["key"])


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


def _identifier(value):
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 255:
        raise ValueError("Mapped IDs must be nonempty UTF-8 strings of at most 255 bytes")
    return value


def _portable(value):
    """Conservative, lossless common subset across all supported serializers."""
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _portable(item)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _portable(item)
        return
    raise TypeError("Attributes require string-keyed dictionaries, lists, and finite scalars; use attribute_transform for other values")


def _attributes(kind, attrs, transform):
    attrs = deepcopy(dict(attrs))
    if transform is not None:
        attrs = transform(kind, attrs)
    if type(attrs) is not dict:
        raise TypeError("attribute_transform must return a dictionary")
    _portable(attrs)
    return attrs


def _labels(value):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) and item for item in value):
        raise ValueError("Native labels must be a string or a list of nonempty strings")
    return list(value)


def _preflight_entity(target, entity):
    """Use the storage encoders to catch serializer/index errors before writes."""
    is_node = isinstance(entity, Node)
    target.entity_serializer.serialize(entity, "Node" if is_node else "Edge")
    eid = entity.get_id_bytes
    prefixes = []
    if is_node:
        for label in entity.labels:
            _index_key("node_label", [label.encode("utf-8")], eid)
        indexes = target.indexed_node_properties
        prefixes = [("node_property", [])] + [
            ("node_label_property", [label.encode("utf-8")]) for label in entity.labels
        ]
    else:
        indexes = target.indexed_edge_properties
        prefixes = [("edge_property", [])]
        relation = entity.properties.get("type")
        if relation is not None:
            _index_key("edge_type", [relation.encode("utf-8")], eid)
            for direction, endpoint in (("out", entity.source), ("in", entity.target)):
                _checked_index_key(_typed_adjacency_key(direction, endpoint.encode("utf-8"), relation, eid))
            prefixes.append(("edge_type_property", [relation.encode("utf-8")]))
    for name in indexes:
        if name not in entity.properties:
            continue
        value = entity.properties[name]
        exact = _property_value_to_index_bytes(value)
        ranged = _property_value_to_range_index_bytes(value)
        for index, prefix in prefixes:
            parts = prefix + [name.encode("utf-8")]
            _index_key(index, parts + [exact], eid)
            if ranged is not None:
                _range_index_key(index, parts, ranged, eid)


def from_networkx(
    graph,
    target: GraphDB,
    *,
    node_id: Callable[[Any], str] | None = None,
    edge_id_attribute: str = "id",
    node_labels_attribute: str | None = None,
    edge_type_attribute: str | None = "type",
    undirected: str = "reject",
    batch_size: int = 1000,
    attribute_transform: Callable[[str, dict], dict] | None = None,
) -> ImportResult:
    """Validate and import a NetworkX graph, rejecting destination ID collisions.

    All entities are prepared before writes. ``attribute_transform(kind, attrs)``
    explicitly converts unsupported properties; ``kind`` is node/edge/graph.
    Missing mapped label/type attributes mean no labels/type. A conflicting
    existing ``type`` property is rejected. No source attributes are removed.
    """
    nx = _networkx()
    if not isinstance(graph, nx.Graph):
        raise TypeError("graph must be a NetworkX graph")
    if type(batch_size) is not int or batch_size < 1:
        raise ValueError("batch_size must be a positive integer")
    if undirected not in ("reject", "bidirectional"):
        raise ValueError("undirected must be 'reject' or 'bidirectional'")
    if not graph.is_directed() and undirected == "reject":
        raise ValueError("Undirected input requires undirected='bidirectional'")
    result = ImportResult(graph.number_of_nodes(), graph.number_of_edges())
    result.graph_attributes = _attributes("graph", graph.graph, attribute_transform)
    if attribute_transform is not None:
        result.warnings.append("An explicit attribute_transform was applied; values may have changed")
    if not graph.is_directed():
        result.direction_conversion = "bidirectional"
        result.warnings.append("Undirected edges expanded into directed edges; self-loops written once")
    nodes, edges = [], []
    node_ids, edge_ids = set(), set()
    for key, raw in graph.nodes(data=True):
        attrs = deepcopy(dict(raw))
        exported = _pop_export_metadata(attrs, {"id", "labels"})
        if node_id is not None:
            identifier = _identifier(node_id(key))
            try:
                token = _key_token(key)
            except TypeError:
                token = _key_token(identifier)
                result.warnings.append(f"Custom node key replaced by mapped ID {identifier!r} on export")
        else:
            token = _key_token(key)
            identifier = _identifier(exported["id"] if exported else key if isinstance(key, str) else "nx:" + _digest(token))
        if identifier in node_ids or target.get_node(identifier.encode("utf-8")) is not None:
            raise ValueError(f"Node ID collision: {identifier!r}")
        node_ids.add(identifier)
        result.node_ids[key] = identifier
        attrs = _attributes("node", attrs, attribute_transform)
        if _META in attrs:
            raise ValueError(f"Reserved attribute: {_META}")
        labels = _labels(exported["labels"]) if exported else []
        if node_labels_attribute is not None and node_labels_attribute in attrs:
            labels = _labels(attrs[node_labels_attribute])
        attrs[_META] = {"key": token}
        nodes.append(Node(identifier, attrs, labels))
    source_edges = graph.edges(keys=True, data=True) if graph.is_multigraph() else (
        (u, v, None, data) for u, v, data in graph.edges(data=True)
    )
    for source, dest, key, raw in source_edges:
        attrs = deepcopy(dict(raw))
        exported = _pop_export_metadata(attrs, {"id"})
        attrs = _attributes("edge", attrs, attribute_transform)
        if _META in attrs:
            raise ValueError(f"Reserved attribute: {_META}")
        if edge_type_attribute is not None and edge_type_attribute in attrs:
            relation = attrs[edge_type_attribute]
            if not isinstance(relation, str) or not relation:
                raise ValueError("Relationship type must be a nonempty string")
            if "type" in attrs and attrs["type"] != relation:
                raise ValueError("Mapped relationship type conflicts with existing 'type'")
            attrs["type"] = relation
        if attrs.get("type") is not None and (not isinstance(attrs["type"], str) or not attrs["type"]):
            raise ValueError("Relationship type must be a nonempty string")
        token = _key_token(key)
        u, v = result.node_ids[source], result.node_ids[dest]
        if not graph.is_directed() and u > v:
            u, v = v, u
        explicit = exported["id"] if exported else attrs.get(edge_id_attribute)
        identifier = _identifier(explicit) if explicit is not None else "nx:e:" + _digest([u, v, token])
        pairs = [(u, v, identifier)]
        if not graph.is_directed() and source != dest:
            pairs.append((v, u, "nx:r:" + _digest([identifier, v, u])))
        mapped = []
        for a, b, eid in pairs:
            if eid in edge_ids or target.get_edge(eid.encode("utf-8")) is not None:
                raise ValueError(f"Edge ID collision: {eid!r}")
            edge_ids.add(eid)
            props = deepcopy(attrs)
            props[_META] = {"key": token}
            edges.append(Edge(eid, a, b, props))
            mapped.append(eid)
        result.edge_ids[source, dest, key] = tuple(mapped)
    # Exercise actual destination encoders before publishing anything.
    for entity in nodes + edges:
        _preflight_entity(target, entity)
    for phase, entities, writer in (
        ("nodes", nodes, target.put_nodes), ("edges", edges, target.put_edges_bulk),
    ):
        for offset in range(0, len(entities), batch_size):
            batch = entities[offset:offset + batch_size]
            try:
                writer(batch)
            except Exception as exc:
                raise ImportWriteError(result, phase, offset) from exc
            if phase == "nodes":
                result.imported_nodes += len(batch)
            else:
                result.imported_edges += len(batch)
    return result


def to_networkx(
    graph: GraphDB,
    *,
    node_ids=None,
    edge_ids=None,
    multigraph: bool = True,
    graph_attributes: dict | None = None,
):
    """Export current records to a MultiDiGraph (or lossless DiGraph).

    ``node_ids`` selects an induced subgraph. ``edge_ids`` selects edges, adding
    endpoints when no node selection is given. With both, endpoints must be in
    the node selection. Missing selected IDs and dangling edges raise errors.
    Caller-supplied graph attributes are copied; they are not read from storage.
    """
    nx = _networkx()
    output = nx.MultiDiGraph() if multigraph else nx.DiGraph()
    output.graph.update(deepcopy(graph_attributes or {}))
    selected = {graph.key_to_string(key) for key in node_ids} if node_ids is not None else None
    edge_keys = edge_ids if edge_ids is not None else graph.store.get_edge_keys_generator()
    edges = []
    for key in edge_keys:
        edge = graph.get_edge(graph.node_key_to_bytes(key))
        if edge is None:
            raise ValueError(f"Missing edge: {key!r}")
        if selected is not None and (edge.source not in selected or edge.target not in selected):
            if edge_ids is not None:
                raise ValueError("Selected edge endpoints are outside node selection")
            continue
        edges.append(edge)
    if selected is None:
        selected = {endpoint for edge in edges for endpoint in (edge.source, edge.target)} if edge_ids is not None else {
            graph.key_to_string(key) for key in graph.get_node_keys_generator()
        }
    mapped = {}
    for identifier in sorted(selected):
        node = graph.get_node(graph.node_key_to_bytes(identifier))
        if node is None:
            raise ValueError(f"Missing node: {identifier!r}")
        attrs = deepcopy(node.properties)
        key = _pop_stored_key(attrs, identifier)
        if key in output:
            raise ValueError("Stored NetworkX node keys collide")
        mapped[identifier] = key
        attrs[_META] = {"id": identifier, "labels": list(node.labels)}
        output.add_node(key)
        output.nodes[key].update(attrs)
    for edge in edges:
        if edge.source not in mapped or edge.target not in mapped:
            raise ValueError("Dangling edge endpoint")
        u, v = mapped[edge.source], mapped[edge.target]
        attrs = deepcopy(edge.properties)
        key = _pop_stored_key(attrs, edge.get_id)
        # NetworkX interprets key=None as allocation, so use the stable edge ID.
        if key is None:
            key = edge.get_id
        duplicate = output.has_edge(u, v, key) if multigraph else output.has_edge(u, v)
        if duplicate:
            raise ValueError("Export would collapse parallel edges or duplicate multigraph keys")
        attrs[_META] = {"id": edge.get_id}
        if multigraph:
            output.add_edge(u, v, key=key)
            output.edges[u, v, key].update(attrs)
        else:
            output.add_edge(u, v)
            output.edges[u, v].update(attrs)
    return output


def load_gexf(source, target: GraphDB, *, label_property: str = "label", max_bytes: int = 64 * 1024 * 1024, **kwargs) -> ImportResult:
    """Load static GEXF 1.1draft/1.2draft from a local path or binary stream.

    Paths ending in .gz/.bz2 are decompressed with a bounded read. Streams are
    caller-owned and must already be decompressed. No URLs or dynamic graphs.
    ``max_bytes`` bounds decompressed XML, not total parsing/graph memory.
    """
    nx = _networkx()
    try:
        from defusedxml import ElementTree as safe_xml
    except ImportError as exc:
        raise ImportError("GEXF loading requires pip install 'gestaltdb[networkx]'") from exc
    if type(max_bytes) is not int or max_bytes < 1:
        raise ValueError("max_bytes must be a positive integer")
    if not isinstance(label_property, str) or not label_property or label_property == _META:
        raise ValueError("label_property must be a nonempty, nonreserved string")
    if hasattr(source, "read"):
        data = source.read(max_bytes + 1)
    else:
        path = Path(source)
        opener = gzip.open if path.suffix == ".gz" else bz2.open if path.suffix == ".bz2" else open
        with opener(path, "rb") as stream:
            data = stream.read(max_bytes + 1)
    if not isinstance(data, bytes):
        raise TypeError("GEXF streams must be binary")
    if len(data) > max_bytes:
        raise ValueError("GEXF exceeds max_bytes decompressed XML limit")
    root = safe_xml.fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    namespaces = {"http://www.gexf.net/1.1draft", "http://www.gexf.net/1.2draft"}
    namespace = root.tag.partition("}")[0].removeprefix("{")
    if namespace not in namespaces or root.tag != f"{{{namespace}}}gexf":
        raise ValueError("Supported GEXF namespaces: 1.1draft and 1.2draft")
    ns = f"{{{namespace}}}"
    graphs = root.findall(ns + "graph")
    if len(graphs) != 1:
        raise ValueError("GEXF must contain exactly one graph")
    element = graphs[0]
    direction = element.get("defaultedgetype", "undirected")
    if direction not in ("directed", "undirected"):
        raise ValueError("Mixed GEXF direction is unsupported")
    for item in element.iter():
        local = item.tag.rsplit("}", 1)[-1]
        if item.get("mode", "static") != "static" or local in ("spell", "spells", "slice", "slices") or any(
            name in item.attrib for name in ("start", "end", "startopen", "endopen")
        ):
            raise ValueError("Dynamic GEXF is unsupported; no automatic temporal-history mapping")
        if local in ("parents", "parent", "hyperedges", "hyperedge") or "pid" in item.attrib:
            raise ValueError("Hierarchical GEXF and hyperedges are unsupported")
    _validate_gexf_structure(root, ns)
    nodes = element.findall(ns + "nodes/" + ns + "node")
    all_nodes = element.findall(".//" + ns + "node")
    if len(nodes) != len(all_nodes):
        raise ValueError("Nested GEXF nodes are unsupported")
    ids = [node.get("id") for node in nodes]
    if None in ids or len(ids) != len(set(ids)):
        raise ValueError("Missing or duplicate GEXF node IDs")
    ids = set(ids)
    edge_ids = set()
    xml_edges = element.findall(ns + "edges/" + ns + "edge")
    for edge in xml_edges:
        if edge.get("type", direction) != direction:
            raise ValueError("Mixed GEXF direction is unsupported")
        if edge.get("source") not in ids or edge.get("target") not in ids:
            raise ValueError("GEXF edge refers to an undeclared node")
        eid = edge.get("id")
        if eid is not None:
            if eid in edge_ids:
                raise ValueError("Duplicate GEXF edge ID")
            edge_ids.add(eid)
    # Only reparse a sanitized tree: no DTD/entity content can reach NetworkX.
    graph = nx.read_gexf(io.BytesIO(ElementTree.tostring(root)))
    if graph.number_of_edges() != len(xml_edges):
        raise ValueError("GEXF multigraph keys would collapse distinct edges")
    for _, attrs in graph.nodes(data=True):
        for name, value in graph.graph.get("node_default", {}).items():
            attrs.setdefault(name, deepcopy(value))
        if label_property != "label" and "label" in attrs:
            if label_property in attrs:
                raise ValueError("GEXF display label conflicts with label_property")
            attrs[label_property] = attrs.pop("label")
    for _, _, attrs in graph.edges(data=True):
        for name, value in graph.graph.get("edge_default", {}).items():
            attrs.setdefault(name, deepcopy(value))
    meta = root.find(ns + "meta")
    if meta is not None:
        graph.graph["gexf_meta"] = {
            "attributes": dict(meta.attrib),
            "elements": [{"name": child.tag.rsplit("}", 1)[-1], "text": child.text} for child in meta],
        }
    return from_networkx(graph, target, **kwargs)


def _validate_gexf_structure(root, ns):
    """Reject constructs the NetworkX reader would otherwise silently ignore."""
    children = {
        "gexf": {"meta", "graph"}, "meta": {"creator", "description", "keywords"},
        "graph": {"attributes", "nodes", "edges"}, "attributes": {"attribute"},
        "attribute": {"default"}, "nodes": {"node"}, "edges": {"edge"},
        "node": {"attvalues", "color", "size", "thickness", "shape", "position"},
        "edge": {"attvalues"}, "attvalues": {"attvalue"},
    }
    allowed_attrs = {
        "gexf": {"version", "{http://www.w3.org/2001/XMLSchema-instance}schemaLocation"},
        "meta": {"lastmodifieddate"}, "graph": {"name", "mode", "defaultedgetype", "timeformat"},
        "attributes": {"class", "mode"}, "attribute": {"id", "title", "type"},
        "nodes": {"count"}, "edges": {"count"}, "node": {"id", "label"},
        "edge": {"id", "source", "target", "label", "weight", "type"},
        "attvalue": {"for", "value"}, "color": {"r", "g", "b", "a"},
        "size": {"value"}, "thickness": {"value"}, "shape": {"value", "shape", "uri"},
        "position": {"x", "y", "z"},
    }
    viz_ns = ns[:-1] + "/viz}"
    for parent in root.iter():
        local = parent.tag.rsplit("}", 1)[-1]
        unknown = set(parent.attrib) - allowed_attrs.get(local, set())
        if unknown:
            raise ValueError(f"Unsupported GEXF {local} attributes: {sorted(unknown)}")
        seen = set()
        for child in parent:
            name = child.tag.rsplit("}", 1)[-1]
            expected_ns = viz_ns if name in ("color", "size", "thickness", "shape", "position") else ns
            if name not in children.get(local, set()) or child.tag != expected_ns + name:
                raise ValueError(f"Unsupported GEXF element {child.tag!r} inside {local}")
            if name in seen and name not in ("node", "edge", "attribute", "attvalue", "attributes"):
                raise ValueError(f"Duplicate GEXF {name} element")
            seen.add(name)
    graph = root.find(ns + "graph")
    for kind in ("node", "edge"):
        identifiers, titles = set(), set()
        for container in graph.findall(ns + "attributes"):
            if container.get("class") not in ("node", "edge"):
                raise ValueError("GEXF attribute class must be node or edge")
            if container.get("class") != kind:
                continue
            for attribute in container:
                identifier, title = attribute.get("id"), attribute.get("title")
                reserved = {"label", "viz", _META} if kind == "node" else {"id", "label", "weight", _META}
                if not identifier or not title or identifier in identifiers or title in titles or title in reserved:
                    raise ValueError("Missing, duplicate, or reserved GEXF attribute definition")
                identifiers.add(identifier)
                titles.add(title)
        for entity in graph.findall(ns + kind + "s/" + ns + kind):
            values = entity.findall(ns + "attvalues/" + ns + "attvalue")
            refs = [value.get("for") for value in values]
            if len(refs) != len(set(refs)):
                raise ValueError("Duplicate static GEXF attribute value")
            # NetworkX's shape reader uses 'shape', while the standard uses 'value'.
            shape = entity.find(viz_ns + "shape")
            if shape is not None and "value" in shape.attrib:
                if "shape" in shape.attrib and shape.get("shape") != shape.get("value"):
                    raise ValueError("Conflicting GEXF visualization shape")
                shape.set("shape", shape.get("value"))

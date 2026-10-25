"""Map parsed CDM records to GestaltDB nodes and edges.

Model (edge ``properties["type"]`` carries the typed-traversal signal):

* one ``CdmType`` node per schema (labels distinguish object/choice/enum/scalar)
* ``HAS_PROPERTY`` edge per object property pointing at another type node
* ``IN_NAMESPACE`` edge from each type to its fine-grained namespace node
* ``CHILD_NAMESPACE`` edges forming fine -> coarse -> root metadata hierarchy,
  so ``ns:cdm-event`` and ``ns:cdm-legaldocumentation`` always exist
* one ``CdmDataset`` node; every node/edge also carries ``parser_version``
"""

from __future__ import annotations

from dataclasses import dataclass, field

from gestaltdb.graphdb import Edge, GraphDB, Node

from .parser import PARSER_VERSION, CdmParseResult

TYPE_LABEL = "CdmType"
NAMESPACE_LABEL = "CdmNamespace"
DATASET_LABEL = "CdmDataset"

HAS_PROPERTY = "HAS_PROPERTY"
IN_NAMESPACE = "IN_NAMESPACE"
CHILD_NAMESPACE = "CHILD_NAMESPACE"
EXTENDS = "EXTENDS"


@dataclass
class CdmGraphPayload:
    """Builder output ready for :func:`load_into_graph`."""

    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    type_node_ids: dict[str, str] = field(default_factory=dict)
    stats: dict = field(default_factory=dict)


def _type_node_id(type_name: str, namespace_slug: str, used: set[str]) -> str:
    candidate = f"cdm:{type_name}"
    if candidate in used:
        candidate = f"cdm:{namespace_slug}:{type_name}"
    used.add(candidate)
    return candidate


def _kind_labels(kind: str) -> list[str]:
    mapping = {
        "object": [TYPE_LABEL, "CdmObject"],
        "choice": [TYPE_LABEL, "CdmChoice"],
        "enum": [TYPE_LABEL, "CdmEnum"],
        "scalar": [TYPE_LABEL, "CdmScalar"],
    }
    return mapping.get(kind, [TYPE_LABEL, "CdmUnknown"])


def build_graph_payload(
    result: CdmParseResult,
    source_url: str | None = None,
    parser_version: str = PARSER_VERSION,
    source: str | None = None,
    package_version: str | None = None,
) -> CdmGraphPayload:
    """Convert a parse result into nodes and edges."""
    from .parser import BASE_URL

    payload = CdmGraphPayload()
    used_ids: set[str] = set()
    ref_index: dict[str, str] = {}  # record key -> node_id
    name_index: dict[str, list[str]] = {}  # type_name -> record keys

    for key in sorted(result.records):
        record = result.records[key]
        node_id = _type_node_id(record.type_name, record.namespace_slug, used_ids)
        ref_index[key] = node_id
        name_index.setdefault(record.type_name, []).append(key)
        payload.type_node_ids[key] = node_id
        payload.nodes.append(
            Node(
                node_id=node_id,
                labels=_kind_labels(record.kind),
                properties={
                    "name": record.type_name,
                    "file": record.file,
                    "title": record.title,
                    "description": record.description,
                    "kind": record.kind,
                    "namespace": record.namespace_slug,
                    "coarse_namespace": record.coarse_namespace,
                    "namespace_anchor": record.namespace_anchor,
                    "required": list(record.required),
                    "property_count": len(record.properties),
                    "enum_values": list(record.enum_values),
                    "enum_details": [
                        {
                            "value": item.get("value"),
                            "title": item.get("title"),
                            "description": item.get("description"),
                        }
                        for item in record.enum_details
                    ],
                    "parser_version": parser_version,
                    "cdm_version": result.cdm_version,
                },
            )
        )

    # Namespace metadata nodes: fine-grained, coarse, and one root.
    fine_slugs = sorted({r.namespace_slug for r in result.records.values()})
    coarse_slugs = sorted({r.coarse_namespace for r in result.records.values()})
    for slug in fine_slugs:
        node_id = f"ns:{slug}"
        used_ids.add(node_id)
        payload.nodes.append(
            Node(
                node_id=node_id,
                labels=[NAMESPACE_LABEL, "CdmFineNamespace"],
                properties={
                    "name": slug,
                    "namespace": slug,
                    "level": "fine",
                    "parser_version": parser_version,
                    "cdm_version": result.cdm_version,
                },
            )
        )
    for slug in coarse_slugs:
        node_id = f"ns:{slug}"
        if node_id in used_ids:
            continue
        used_ids.add(node_id)
        payload.nodes.append(
            Node(
                node_id=node_id,
                labels=[NAMESPACE_LABEL, "CdmCoarseNamespace"],
                properties={
                    "name": slug,
                    "namespace": slug,
                    "level": "coarse",
                    "parser_version": parser_version,
                    "cdm_version": result.cdm_version,
                },
            )
        )
    root_id = "ns:cdm"
    used_ids.add(root_id)
    payload.nodes.append(
        Node(
            node_id=root_id,
            labels=[NAMESPACE_LABEL, "CdmRootNamespace"],
            properties={
                "name": "cdm",
                "namespace": "cdm",
                "level": "root",
                "description": "Root metadata node for the CDM schema tree.",
                "parser_version": parser_version,
                "cdm_version": result.cdm_version,
            },
        )
    )

    # Dataset node records provenance, including the parser version.
    resolved_source = source or result.source
    dataset_id = f"cdm:dataset:{result.cdm_version}"
    dataset_props = {
        "name": f"CDM {result.cdm_version}",
        "cdm_version": result.cdm_version,
        "parser_version": parser_version,
        "source": resolved_source,
        "source_url": source_url or BASE_URL,
        "schema_count": result.files_parsed,
        "description": "Provenance for this parsed CDM schema graph.",
    }
    if package_version is not None:
        dataset_props["cdm_package_version"] = package_version
    payload.nodes.append(
        Node(
            node_id=dataset_id,
            labels=[DATASET_LABEL, NAMESPACE_LABEL],
            properties=dataset_props,
        )
    )

    # Edges: type membership, namespace hierarchy, and property references.
    edge_seq = 0

    def next_edge_id(src: str, kind: str, dst: str) -> str:
        nonlocal edge_seq
        edge_seq += 1
        return f"cdm-e{edge_seq:05d}-{kind}"

    for key, record in result.records.items():
        src = ref_index[key]
        payload.edges.append(
            Edge(
                edge_id=next_edge_id(src, "ns", record.namespace_slug),
                source=src,
                target=f"ns:{record.namespace_slug}",
                properties={
                    "type": IN_NAMESPACE,
                    "parser_version": parser_version,
                    "cdm_version": result.cdm_version,
                },
            )
        )
    for slug in fine_slugs:
        parent = slug.rsplit("-", 1)[0] if "-" in slug else "cdm"
        coarse = next(
            (r.coarse_namespace for r in result.records.values() if r.namespace_slug == slug),
            None,
        )
        target = f"ns:{coarse}" if coarse else f"ns:{parent}"
        payload.edges.append(
            Edge(
                edge_id=next_edge_id(f"ns:{slug}", "child", target),
                source=f"ns:{slug}",
                target=target,
                properties={
                    "type": CHILD_NAMESPACE,
                    "parser_version": parser_version,
                    "cdm_version": result.cdm_version,
                },
            )
        )
    for slug in coarse_slugs:
        payload.edges.append(
            Edge(
                edge_id=next_edge_id(f"ns:{slug}", "child", root_id),
                source=f"ns:{slug}",
                target=root_id,
                properties={
                    "type": CHILD_NAMESPACE,
                    "parser_version": parser_version,
                    "cdm_version": result.cdm_version,
                },
            )
        )

    def resolve_target(prop) -> str | None:
        """Resolve a property to a target node id via ref_key, then name."""
        if prop.ref_key is not None and prop.ref_key in ref_index:
            return ref_index[prop.ref_key]
        if prop.ref_target:
            candidates = name_index.get(prop.ref_target, [])
            if len(candidates) == 1:
                return ref_index[candidates[0]]
        return None

    skipped_refs = 0
    for key, record in result.records.items():
        src = ref_index[key]
        for prop in record.properties:
            target = resolve_target(prop)
            if not prop.ref_target or target is None:
                skipped_refs += 1
                continue
            payload.edges.append(
                Edge(
                    edge_id=next_edge_id(src, "prop", prop.ref_target),
                    source=src,
                    target=target,
                    properties={
                        "type": HAS_PROPERTY,
                        "property_name": prop.name,
                        "description": prop.description,
                        "required": prop.required,
                        "is_array": prop.is_array,
                        "min_items": prop.min_items,
                        "max_items": prop.max_items,
                        "primitive_type": prop.primitive_type,
                        "ref_file": prop.ref_file,
                        "parser_version": parser_version,
                        "cdm_version": result.cdm_version,
                    },
                )
            )

    # Inheritance links (pydantic source): child EXTENDS base type.
    extends_edges = 0
    for key, record in result.records.items():
        src = ref_index[key]
        for base_name in record.base_types:
            candidates = name_index.get(base_name, [])
            if len(candidates) != 1:
                skipped_refs += 1
                continue
            payload.edges.append(
                Edge(
                    edge_id=next_edge_id(src, "extends", base_name),
                    source=src,
                    target=ref_index[candidates[0]],
                    properties={
                        "type": EXTENDS,
                        "base_type": base_name,
                        "parser_version": parser_version,
                        "cdm_version": result.cdm_version,
                    },
                )
            )
            extends_edges += 1

    has_property = sum(1 for e in payload.edges if e.properties.get("type") == HAS_PROPERTY)
    payload.stats = {
        "parser_version": parser_version,
        "cdm_version": result.cdm_version,
        "source": resolved_source,
        "type_nodes": len(result.records),
        "namespace_nodes": len(fine_slugs) + len(coarse_slugs) + 1,
        "node_count": len(payload.nodes),
        "edge_count": len(payload.edges),
        "has_property_edges": has_property,
        "extends_edges": extends_edges,
        "skipped_primitive_or_dangling": skipped_refs,
        "dangling_refs": len(result.dangling_refs),
    }
    return payload


def load_into_graph(
    graph: GraphDB,
    payload: CdmGraphPayload,
    create_indexes: bool = True,
) -> dict:
    """Write payload nodes/edges and create lookup indexes.

    Prefers the 0.14 ``graph.indexes`` DDL authority when present and falls
    back to the flat ``GraphDB.create_*_property_index`` wrappers (still
    valid in 0.14, required on 0.12) so the example runs on both APIs.
    """
    graph.put_nodes(payload.nodes)
    graph.put_edges_bulk(payload.edges)
    if create_indexes:
        indexes = getattr(graph, "indexes", None)
        if indexes is not None:
            for prop in ("name", "namespace", "coarse_namespace", "kind", "parser_version"):
                indexes.create_node_property_index(prop)
            for prop in ("property_name", "parser_version"):
                indexes.create_edge_property_index(prop)
        else:
            for prop in ("name", "namespace", "coarse_namespace", "kind", "parser_version"):
                graph.create_node_property_index(prop)
            for prop in ("property_name", "parser_version"):
                graph.create_edge_property_index(prop)
    return dict(payload.stats)

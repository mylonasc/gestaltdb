"""Whole inference-chain visualization (result-viz).

Composes a property-graph layer, an inference layer (ClaimExplanation,
proof Explanation, or inference audit), and an ontology layer into one
figure with the shared shape vocabulary: circles for data, triangles for
inferred conclusions, squares for schema; solid edges for stored and
structural relationships, dotted edges for inference steps. Standard
library only.
"""

from __future__ import annotations

from typing import Any, Mapping

from .reasoning import (
    INFERENCE_DASH,
    _binary_fact_id,
    claim_explanation_graph,
    inference_audit_graph,
    ontology_graph,
    proof_explanation_graph,
)

def _explanation_statements(explanation: Any) -> dict[str, dict[str, set[str]]]:
    """Map explanation node IDs to their subject/object/predicate terms.

    Proof facts contribute their triple; claim and audit nodes contribute
    their claim triple. Rule, absence, and support nodes carry no entity
    terms. Used only to place exact-equality cross-layer links.
    """
    found: dict[str, dict[str, set[str]]] = {}

    def record(node_id: str, subjects: Any, objects: Any, predicate: Any) -> None:
        terms = found.setdefault(node_id, {"subjects": set(), "objects": set(), "predicates": set()})
        for value in subjects or ():
            terms["subjects"].add(str(value))
        for value in objects or ():
            terms["objects"].add(str(value))
        if predicate is not None:
            terms["predicates"].add(str(predicate))

    if isinstance(explanation, Mapping) or (hasattr(explanation, "to_dict") and not hasattr(explanation, "nodes")):
        document = explanation.to_dict() if hasattr(explanation, "to_dict") else dict(explanation)
        for entry in document.get("nodes", ()):
            if not isinstance(entry, Mapping):
                continue
            claim = entry.get("claim", {})
            if isinstance(claim, Mapping) and claim.get("subject") is not None:
                record(str(entry.get("id", "")), [claim.get("subject")], [claim.get("object")], claim.get("predicate"))
        return found
    for node in getattr(explanation, "nodes", ()) or ():
        if hasattr(node, "fact"):
            fact = node.fact
            record(_binary_fact_id(str(getattr(fact, "subject", "?")), str(getattr(fact, "predicate", "?")),
                                   str(getattr(fact, "object", "?"))),
                   [getattr(fact, "subject", None)], [getattr(fact, "object", None)], getattr(fact, "predicate", None))
        elif str(getattr(node, "kind", "")) == "claim":
            claim = getattr(getattr(node, "value", None), "claim", None)
            if claim is not None:
                record(str(getattr(node, "node_id", "")), [getattr(claim, "subject", None)],
                       [getattr(claim, "object", None)], getattr(claim, "predicate", None))
    return found


def _is_proof_explanation(explanation: Any) -> bool:
    return hasattr(explanation, "root") and not hasattr(explanation, "claim_id")


def inference_chain_graph(
    nodes: Any,
    edges: Any,
    explanation: Any | None = None,
    ontology: Any | None = None,
    *,
    link_layers: bool = True,
    max_nodes: int = 2000,
    max_edges: int = 5000,
    absolute_max_nodes: int | None = None,
    absolute_max_edges: int | None = None,
    meta: Mapping[str, Any] | None = None,
) -> Any:
    """Build one figure spanning property graph, inference, and ontology layers.

    Circles mark the data layer (property-graph nodes plus asserted/input
    facts), triangles mark inferred conclusions, and squares mark schema
    (rules, absences, axiom nodes, ontology terms). Stored, structural, and
    lookup edges are solid; inference steps deriving a conclusion are dotted.

    With ``link_layers=True`` (the default), exact-equality cross-links join
    the layers without inventing mappings: an explanation statement whose
    subject or object equals a graph node ID gains a solid ``mentions`` edge
    to that node (the same convention as the current-graph projection, which
    uses node IDs as fact subjects and objects), and a statement whose
    predicate equals an ontology property term gains a solid ``typed_as``
    edge to that term. Pass ``link_layers=False`` to render the layers
    side by side with no cross-links.

    Each layer is capped independently with the given caps; sample the graph
    layer first for very large databases. Merging keeps the first occurrence
    of a duplicated ID in graph, explanation, then ontology order.
    """
    from .ir import TruncationInfo, VizEdge, VizGraph, VizNode

    if explanation is None and ontology is None:
        raise ValueError("inference_chain_graph needs an explanation or an ontology, not just a graph")
    if not isinstance(link_layers, bool):
        raise ValueError("link_layers must be a bool")
    caps = {"max_nodes": max_nodes, "max_edges": max_edges,
            "absolute_max_nodes": absolute_max_nodes, "absolute_max_edges": absolute_max_edges}

    layers: list[Any] = [VizGraph.from_nodes_edges(nodes, edges, meta={"builder": "chain:graph"}, **caps)]
    statements: dict[str, dict[str, set[str]]] = {}
    if explanation is not None:
        if isinstance(explanation, Mapping) or hasattr(explanation, "to_dict"):
            layers.append(inference_audit_graph(explanation, **caps))
        elif hasattr(explanation, "claim_id"):
            layers.append(claim_explanation_graph(explanation, **caps))
        elif _is_proof_explanation(explanation):
            layers.append(proof_explanation_graph(explanation, **caps))
        else:
            raise TypeError("explanation must be a ClaimExplanation, proof Explanation, or inference audit")
        statements = _explanation_statements(explanation)
    property_terms: set[str] = set()
    if ontology is not None:
        onto_viz = ontology_graph(ontology, **caps)
        property_terms = {node.id for node in onto_viz.nodes
                          if "property" in node.labels or "term" in node.labels}
        renamed_nodes = [node for node in onto_viz.nodes]
        layers.append(onto_viz)

    merged_nodes: dict[str, Any] = {}
    merged_edges: dict[str, Any] = {}
    for layer in layers:
        renaming = layer.meta.get("builder") == "from_ontology"
        for node in layer.nodes:
            node_id = "onto:" + node.id if renaming else node.id
            merged_nodes.setdefault(node_id, VizNode(id=node_id, labels=node.labels,
                                                      properties=dict(node.properties),
                                                      extra=dict(node.extra), shape=node.shape))
        for edge in layer.edges:
            source = "onto:" + edge.source if renaming else edge.source
            target = "onto:" + edge.target if renaming else edge.target
            merged_edges.setdefault(edge.id, VizEdge(id=edge.id, source=source, target=target, type=edge.type,
                                                      properties=dict(edge.properties),
                                                      extra=dict(edge.extra), dash=edge.dash))
    nodes_dropped = sum(layer.truncation.nodes_dropped for layer in layers)
    edges_dropped = sum(layer.truncation.edges_dropped for layer in layers)

    cross = 0
    if link_layers and statements:
        graph_ids = {node.id for node in layers[0].nodes}
        kept_ids = set(merged_nodes)
        for node_id, terms in sorted(statements.items()):
            if node_id not in kept_ids:
                continue
            for entity in sorted((terms["subjects"] | terms["objects"]) & graph_ids):
                link_id = node_id + " | mentions | " + entity
                if link_id not in merged_edges:
                    merged_edges[link_id] = VizEdge(
                        id=link_id, source=node_id, target=entity, type="mentions",
                        properties={"type": "mentions", "relationship": "mentions"})
                    cross += 1
            for predicate in sorted(terms["predicates"] & property_terms):
                target = "onto:" + predicate
                link_id = node_id + " | typed_as | " + target
                if link_id not in merged_edges and target in kept_ids:
                    merged_edges[link_id] = VizEdge(
                        id=link_id, source=node_id, target=target, type="typed_as",
                        properties={"type": "typed_as", "relationship": "typed_as"})
                    cross += 1

    merged_node_list = [merged_nodes[node_id] for node_id in sorted(merged_nodes)]
    merged_edge_list = sorted(merged_edges.values(), key=lambda edge: (edge.source, edge.target, edge.id))
    highlights: list[str] = []
    for layer in layers:
        highlights.extend(layer.highlight_nodes)
    payload_meta = dict(meta or {})
    payload_meta["builder"] = "from_inference_chain"
    payload_meta["suggested_layout"] = "tree"
    payload_meta["link_layers"] = link_layers
    payload_meta["layer_node_counts"] = [len(layer.nodes) for layer in layers]
    payload_meta["layer_edge_counts"] = [len(layer.edges) for layer in layers]
    payload_meta["cross_links"] = cross
    return VizGraph(
        nodes=tuple(merged_node_list),
        edges=tuple(merged_edge_list),
        highlight_nodes=tuple(sorted(set(highlights))),
        truncation=TruncationInfo(nodes_dropped=nodes_dropped, edges_dropped=edges_dropped),
        meta=payload_meta,
    )

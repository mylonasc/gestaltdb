"""Concept landscape for the onto-tutorial introduction (ch01).

Builds the domain map — property graphs, RDF/OWL, query languages,
ontology fragment, reasoning engine — as real gestaltdb ``Node``/``Edge``
objects so every figure is rendered by gestaltdb's own viz
(``gestaltdb.viz.api``) via ``save_svg``. Three progressive stages let ch01
grow the picture instead of dropping it whole:

* stage 1 — the property-graph world (store + query languages),
* stage 2 — the semantic overlay (RDF, OWL, ``gestalt-core-v1``),
* stage 3 — the reasoning layer (Datalog/Prolog, engine, consumers).
"""

from __future__ import annotations

from gestaltdb.graphdb import Edge, Node
from pathlib import Path
from xml.etree import ElementTree

# Fixed canvas positions (fitted to image by render_svg). Keep labels apart.
POSITIONS: dict[str, tuple[float, float]] = {
    # stage 1 — property world (left column)
    "graphdb": (0.0, 3.0),
    "pnode": (1.0, 4.2),
    "pedge": (1.0, 1.8),
    "cypher": (2.2, 4.4),
    "graphql": (0.0, 5.5),
    "sql": (2.2, 0.4),
    # stage 2 — semantic overlay (middle column)
    "rdf": (3.6, 3.0),
    "owl": (4.8, 3.8),
    "onto": (4.8, 2.2),
    "axiom": (6.0, 2.2),
    # stage 3 — reasoning layer (right column)
    "datalog": (7.2, 4.2),
    "prolog": (7.2, 5.2),
    "rule": (7.2, 3.0),
    "engine": (8.4, 2.0),
    "claims": (8.4, 0.4),
    "sampler": (11.2, 3.0),
    "handoff": (9.7, 2.4),
    "adapter": (5.0, 6.7),
}

_NODES: dict[str, tuple[str, str, str]] = {
    # id: (label, kind, layer)
    "graphdb": ("Property Graph DB", "store", "property"),
    "pnode": ("Labeled Node", "data", "property"),
    "pedge": ("Typed Edge", "data", "property"),
    "cypher": ("Cypher", "language", "property"),
    "graphql": ("GraphQL", "language", "property"),
    "sql": ("SQL / SQL-PGQ", "language", "property"),
    "rdf": ("RDF Triple", "data", "semantic"),
    "owl": ("OWL (RDF encoding)", "standard", "semantic"),
    "onto": ("Ontology (core-v1)", "model", "semantic"),
    "axiom": ("Axiom", "model", "semantic"),
    "datalog": ("Datalog", "language", "reasoning"),
    "prolog": ("Prolog", "language", "reasoning"),
    "rule": ("Rule / Fact", "model", "reasoning"),
    "engine": ("DatalogEngine", "engine", "reasoning"),
    "claims": ("Claims / Temporal", "store", "reasoning"),
    "sampler": ("Asserted-graph sampler", "consumer", "reasoning"),
    "handoff": ("Seed handoff", "adapter", "reasoning"),
    "adapter": ("Explicit graph adapter", "adapter", "reasoning"),
}

_EDGES: list[tuple[str, str, str, str, int]] = [
    # (edge_id, source, target, type, stage)
    ("e-stores-n", "graphdb", "pnode", "stores", 1),
    ("e-stores-e", "graphdb", "pedge", "stores", 1),
    ("e-cypher-q", "cypher", "graphdb", "queries", 1),
    ("e-graphql-q", "graphql", "graphdb", "may_use_via_resolvers", 1),
    ("e-sql-q", "sql", "graphdb", "graph_patterns_via_PGQ", 1),
    ("e-n-proj", "pnode", "rdf", "explicit_label_mapping", 2),
    ("e-e-proj", "pedge", "rdf", "explicit_term_mapping", 2),
    ("e-owl-ext", "owl", "rdf", "can_serialize_as", 2),
    ("e-onto-sel", "onto", "owl", "selected_axioms_only", 2),
    ("e-onto-has", "onto", "axiom", "contains", 2),
    ("e-ax-comp", "axiom", "rule", "compiles_to", 3),
    ("e-dl-def", "datalog", "rule", "defines", 3),
    ("e-pl-ext", "prolog", "datalog", "related_Horn_languages", 3),
    ("e-eng-eval", "engine", "datalog", "evaluates", 3),
    ("e-eng-inf", "claims", "engine", "evidence_via_adapter", 3),
    ("e-gdb-gr", "graphdb", "adapter", "pinned_records", 3),
    ("e-adapter", "adapter", "engine", "mapped_facts", 3),
    ("e-eng-feed", "engine", "handoff", "inferred_endpoints", 3),
    ("e-handoff", "handoff", "sampler", "seeds_not_inferred_edges", 3),
    ("e-cy-dl", "cypher", "datalog", "both_bind_variables", 3),
]

STAGE_BLURB = {
    1: "Stage 1 — the property-graph world: a store plus its query languages.",
    2: "Stage 2 — the semantic overlay: triples, OWL, and the selected fragment.",
    3: "Stage 3 — the reasoning layer: rules, the engine, and its consumers.",
}


def build_stage(stage: int) -> tuple[list[Node], list[Edge]]:
    """Return the (nodes, edges) visible at the given progressive stage."""
    if stage not in (1, 2, 3):
        raise ValueError("stage must be 1, 2, or 3")
    keep = {nid for nid, (_, _, _) in _NODES.items()
            if _layer_of(nid) <= stage}
    nodes = [
        Node(node_id=nid, labels=["Concept"],
             properties={"label": label, "kind": kind, "layer": layer})
        for nid, (label, kind, layer) in _NODES.items() if nid in keep
    ]
    edges = [
        Edge(edge_id=eid, source=s, target=t, properties={"type": rel})
        for eid, s, t, rel, es in _EDGES if es <= stage
    ]
    return nodes, edges


def _layer_of(nid: str) -> int:
    return {"property": 1, "semantic": 2, "reasoning": 3}[_NODES[nid][2]]


def stage_positions(stage: int) -> dict[str, tuple[float, float]]:
    nodes, _ = build_stage(stage)
    ids = {n.get_id for n in nodes}
    return {k: v for k, v in POSITIONS.items() if k in ids}


def subsumption_figure():
    """Generate the worked example's membership edges from actual inference."""
    from gestaltdb.ontology import Domain, Ontology, SubClassOf, TYPE_PREDICATE, compile_ontology
    from gestaltdb.reasoning import DatalogEngine, Fact, ReasoningLimits
    from gestaltdb.viz.api import VizOptions, visualize_nodes_edges

    plan = compile_ontology(Ontology("people", [
        Domain("worksFor", "Researcher"), SubClassOf("Researcher", "Person"),
    ]))
    result = DatalogEngine().evaluate(
        plan.program, [Fact("alice", "worksFor", "acme")], limits=ReasoningLimits())
    nodes = [
        Node(node_id=key, labels=[kind], properties={"label": key, "kind": kind})
        for key, kind in [("alice", "data"), ("acme", "data"),
                          ("worksFor", "schema"), ("Researcher", "schema"),
                          ("Person", "schema")]
    ]
    relations = [("alice", "acme", "asserted: worksFor"),
                 ("worksFor", "Researcher", "schema: domain"),
                 ("Researcher", "Person", "schema: subClassOf")]
    relations.extend((fact.subject, fact.object, "inferred: rdf:type")
                     for fact in result.match(predicate=TYPE_PREDICATE))
    edges = [Edge(edge_id=f"example-{index}", source=source, target=target,
                  properties={"type": relation})
             for index, (source, target, relation) in enumerate(relations)]
    figure = visualize_nodes_edges(nodes, edges, options=VizOptions(
        node_label_property="label", node_color_property="kind", edge_labels="all",
        title="Asserted fact, schema axioms, and computed memberships"))
    positions = {"alice": (0, 3), "acme": (4, 3), "worksFor": (0, 0),
                 "Researcher": (2, 0), "Person": (4, 0)}
    return figure, positions


def save_tutorial_svg(figure, destination, *, positions, width=1200, height=800):
    """Use GestaltDB geometry with larger, portable labels for documentation.

    Some image converters ignore SVG paint-order and draw the white text halo
    over the glyphs. Remove that halo rather than depending on browser-only
    paint-order behavior. Keep this presentation adjustment outside the library.
    """
    document = ElementTree.fromstring(figure.as_svg(
        positions=positions, width=width, height=height))
    for element in document.iter("{http://www.w3.org/2000/svg}text"):
        element.set("stroke", "none")
        is_node = element.get("text-anchor") == "start"
        element.set("font-size", "16" if is_node else "13")
        if is_node:
            # Put names under their nodes, away from horizontal edge captions.
            element.set("x", str(float(element.get("x")) - 10))
            element.set("y", str(float(element.get("y")) + 20))
            element.set("text-anchor", "middle")
    ElementTree.register_namespace("", "http://www.w3.org/2000/svg")
    Path(destination).write_text(ElementTree.tostring(document, encoding="unicode") + "\n")


def render_all(output_dir: str = "docs/onto-tutorial/figures") -> list[str]:
    """Regenerate all four committed SVGs, including evaluated memberships."""
    from gestaltdb.viz.api import VizOptions, visualize_nodes_edges

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for stage in (1, 2, 3):
        nodes, edges = build_stage(stage)
        fig = visualize_nodes_edges(
            nodes, edges,
            options=VizOptions(
                node_label_property="label", edge_labels="all",
                edge_label_property="type", node_color_property="kind",
                title=f"Onto-tutorial landscape — stage {stage}"))
        dest = out / f"landscape-stage{stage}.svg"
        save_tutorial_svg(fig, dest, width=1500, height=900, positions=stage_positions(stage))
        written.append(str(dest))
    figure, positions = subsumption_figure()
    dest = out / "subsumption-example.svg"
    save_tutorial_svg(figure, dest, positions=positions)
    written.append(str(dest))
    return written


if __name__ == "__main__":  # `uv run python docs/onto-tutorial/toolkit/landscape.py`
    for path in render_all():
        print("wrote", path)

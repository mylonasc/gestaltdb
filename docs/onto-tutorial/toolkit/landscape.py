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

# Fixed canvas positions (fitted to image by render_svg). Keep labels apart.
POSITIONS: dict[str, tuple[float, float]] = {
    # stage 1 — property world (left column)
    "graphdb": (0.0, 3.0),
    "pnode": (1.0, 4.2),
    "pedge": (1.0, 1.8),
    "cypher": (2.2, 4.4),
    "graphql": (2.2, 3.0),
    "sql": (2.2, 1.6),
    # stage 2 — semantic overlay (middle column)
    "rdf": (3.6, 3.0),
    "owl": (4.8, 3.8),
    "onto": (4.8, 2.2),
    "axiom": (6.0, 2.2),
    # stage 3 — reasoning layer (right column)
    "datalog": (7.2, 4.2),
    "prolog": (7.2, 5.2),
    "rule": (7.2, 3.0),
    "engine": (8.4, 3.0),
    "claims": (8.4, 1.6),
    "sampler": (9.6, 3.0),
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
    "owl": ("OWL", "standard", "semantic"),
    "onto": ("Ontology (gestalt-core-v1)", "model", "semantic"),
    "axiom": ("Axiom", "model", "semantic"),
    "datalog": ("Datalog", "language", "reasoning"),
    "prolog": ("Prolog", "language", "reasoning"),
    "rule": ("Rule / Fact", "model", "reasoning"),
    "engine": ("DatalogEngine", "engine", "reasoning"),
    "claims": ("Claims / Temporal", "store", "reasoning"),
    "sampler": ("SamplerSnapshot", "consumer", "reasoning"),
}

_EDGES: list[tuple[str, str, str, str, int]] = [
    # (edge_id, source, target, type, stage)
    ("e-stores-n", "graphdb", "pnode", "stores", 1),
    ("e-stores-e", "graphdb", "pedge", "stores", 1),
    ("e-cypher-q", "cypher", "graphdb", "queries", 1),
    ("e-graphql-q", "graphql", "graphdb", "queries", 1),
    ("e-sql-q", "sql", "graphdb", "queries", 1),
    ("e-n-proj", "pnode", "rdf", "projects_to", 2),
    ("e-e-proj", "pedge", "rdf", "projects_to", 2),
    ("e-owl-ext", "owl", "rdf", "extends", 2),
    ("e-onto-sel", "onto", "owl", "selects_from", 2),
    ("e-onto-has", "onto", "axiom", "contains", 2),
    ("e-ax-comp", "axiom", "rule", "compiles_to", 3),
    ("e-dl-def", "datalog", "rule", "defines", 3),
    ("e-pl-ext", "prolog", "datalog", "extends", 3),
    ("e-eng-eval", "engine", "datalog", "evaluates", 3),
    ("e-eng-inf", "engine", "claims", "infers_over", 3),
    ("e-gdb-gr", "graphdb", "engine", "grounds", 3),
    ("e-eng-feed", "engine", "sampler", "feeds", 3),
    ("e-cy-dl", "cypher", "datalog", "shares_roots", 3),
]

STAGE_BLURB = {
    1: "Stage 1 — the property-graph world: a store plus its query languages.",
    2: "Stage 2 — the semantic overlay: triples, OWL, and the selected fragment.",
    3: "Stage 3 — the reasoning layer: rules, the engine, and its consumers.",
}


def build_stage(stage: int) -> tuple[list[Node], list[Edge]]:
    """Return the (nodes, edges) visible at the given progressive stage."""
    assert stage in (1, 2, 3)
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


def render_all(output_dir: str = "docs/onto-tutorial/figures") -> list[str]:
    """Render stages 1–3 to SVG with gestaltdb's own viz; return paths."""
    from pathlib import Path

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
        fig.save_svg(str(dest), positions=stage_positions(stage))
        written.append(str(dest))
    return written


if __name__ == "__main__":  # `uv run python docs/onto-tutorial/toolkit/landscape.py`
    for path in render_all():
        print("wrote", path)

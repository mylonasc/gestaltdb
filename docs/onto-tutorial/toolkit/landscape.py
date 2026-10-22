"""Concept landscape for the onto-tutorial introduction (ch01).

Builds the domain map — property graphs, RDF/OWL, query languages,
ontology fragment, reasoning engine — as real gestaltdb ``Node``/``Edge``
objects, laid out by Graphviz dot with label-aware spline routing.
Three progressive stages let ch01
grow the picture instead of dropping it whole:

* stage 1 — the property-graph world (store + query languages),
* stage 2 — the semantic overlay (RDF, OWL, ``gestalt-core-v1``),
* stage 3 — the reasoning layer (Datalog/Prolog, engine, consumers).
"""

from __future__ import annotations

from gestaltdb.graphdb import Edge, Node
from html import escape
import json
from pathlib import Path
import shutil
import subprocess
import textwrap
from xml.etree import ElementTree

PALETTE = {
    "property": ("#eaf2ff", "#4164a5", "Property graphs & application languages"),
    "semantic": ("#f2ecff", "#7753a6", "Semantic models & schema axioms"),
    "reasoning": ("#e5f5ee", "#26745b", "Reasoning & explicit consumption"),
    "schema": ("#f2ecff", "#7753a6", "Schema: the rulebook"),
    "data": ("#eaf2ff", "#4164a5", "Data: the assertion"),
}

# Short reading labels keep cross-layer edges from making the canvas too wide.
# The underlying type strings remain unchanged for the inspectable graph model.
RELATION_LABELS = {
    "may_use_via_resolvers": "via resolvers",
    "graph_patterns_via_PGQ": "PGQ patterns",
    "explicit_label_mapping": "label mapping",
    "explicit_term_mapping": "term mapping",
    "can_serialize_as": "RDF encoding",
    "selected_axioms_only": "selected axioms",
    "related_Horn_languages": "Horn family",
    "evidence_via_adapter": "via adapter",
    "seeds_not_inferred_edges": "seeds only",
    "both_bind_variables": "variable binding",
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
    1: "1 · Property graphs and languages",
    2: "2 · Adding the semantic overlay",
    3: "3 · Reasoning and explicit consumers",
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


def build_subsumption() -> tuple[list[Node], list[Edge]]:
    """Generate the worked example's membership edges from actual inference."""
    from gestaltdb.ontology import Domain, Ontology, SubClassOf, TYPE_PREDICATE, compile_ontology
    from gestaltdb.reasoning import DatalogEngine, Fact, ReasoningLimits

    plan = compile_ontology(Ontology("people", [
        Domain("worksFor", "Researcher"), SubClassOf("Researcher", "Person"),
    ]))
    result = DatalogEngine().evaluate(
        plan.program, [Fact("alice", "worksFor", "acme")], limits=ReasoningLimits())
    nodes = [
        Node(node_id=key, labels=[kind], properties={"label": key, "kind": kind, "layer": kind})
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
    return nodes, edges


def _lines(value: str, width: int) -> str:
    """Escape labels before inserting intentional Graphviz HTML line breaks."""
    return '<BR/>'.join(escape(line) for line in textwrap.wrap(
        value, width=width, break_long_words=False, break_on_hyphens=False))


def graph_source(nodes: list[Node], edges: list[Edge], *, title: str) -> str:
    """A layered, crossing-minimized graph; dot owns coordinates and routing.

    Clusters encode conceptual layers, not runtime support. Back-edges do not
    force a cyclic ranking; their arrow direction is nevertheless preserved.
    """
    def quote(value):
        return json.dumps(value, ensure_ascii=False)
    lines = ['digraph tutorial {',
             'graph [rankdir=TB, splines=spline, nodesep=0.45, ranksep=0.65, '
             'pad=0.35, bgcolor="white", compound=true, outputorder=edgesfirst, '
             'fontname="DejaVu Sans", fontsize=22, labelloc=t, '
             f'label={quote(title)}];',
             'node [shape=box, style="rounded,filled", penwidth=2.2, '
             'fontname="DejaVu Sans", fontcolor="#172b4d", margin="0.22,0.16", '
             'width=2.2, height=0.85];',
             'edge [color="#64748b", penwidth=2.3, arrowsize=1.05, '
             'fontname="DejaVu Sans", fontsize=15, fontcolor="#334155"];']
    ids = {node.get_id for node in nodes}
    if any(edge.source not in ids or edge.target not in ids for edge in edges):
        raise ValueError("every illustrated edge must have both endpoints")
    for layer, (fill, stroke, caption) in PALETTE.items():
        members = [node for node in nodes if node.properties["layer"] == layer]
        if not members:
            continue
        lines.extend([f'subgraph cluster_{layer} {{',
                      f'label={quote(caption)}; color={quote(stroke)}; '
                      'style="rounded"; penwidth=1.4; margin=24; fontsize=18;'])
        for node in members:
            label = ('<<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="0" CELLPADDING="3">'
                     f'<TR><TD><FONT POINT-SIZE="12" COLOR="{stroke}">'
                     f'{escape(node.properties["kind"].upper())}</FONT></TD></TR>'
                     '<TR><TD><FONT POINT-SIZE="24"><B>'
                     f'{_lines(node.properties["label"], 17)}</B></FONT></TD></TR></TABLE>>')
            lines.append(f'{quote(node.get_id)} [id={quote("concept-" + node.get_id)}, '
                         f'fillcolor={quote(fill)}, color={quote(stroke)}, label={label}];')
        lines.append('}')
    for edge in edges:
        relation = edge.properties["type"]
        inferred = relation.startswith("inferred:")
        # These compare technologies rather than introducing execution stages.
        unconstrained = edge.get_id in {"e-onto-sel", "e-cy-dl", "e-eng-eval"}
        label = ('<<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="0" '
                 'CELLPADDING="4" BGCOLOR="white"><TR><TD>'
                 f'{_lines(RELATION_LABELS.get(relation, relation.replace("_", " ")), 16)}</TD></TR></TABLE>>')
        lines.append(f'{quote(edge.source)} -> {quote(edge.target)} '
                     f'[id={quote("relation-" + edge.get_id)}, label={label}, '
                     f'constraint={str(not unconstrained).lower()}, '
                     f'color={quote("#19835c" if inferred else "#64748b")}, '
                     f'style={quote("dashed" if inferred else "solid")}];')
    # Rank whole conceptual layers from top to bottom instead of letting
    # long comparison edges spread the overview into a thin horizontal strip.
    if "onto" in ids:
        for source in ("pnode", "pedge"):
            lines.append(f'{quote(source)} -> "onto" [style=invis, weight=20];')
    if "adapter" in ids:
        for source in ("rdf", "axiom"):
            lines.append(f'{quote(source)} -> "adapter" [style=invis, weight=20];')
    lines.append('}')
    return '\n'.join(lines)


def save_tutorial_svg(nodes, edges, destination, *, title):
    """Run the optional build-time Graphviz executable, never a shell string."""
    dot = shutil.which("dot")
    if dot is None:
        raise RuntimeError("Install Graphviz (the 'dot' executable) to regenerate figures; "
                           "building the docs from committed SVGs does not require it.")
    result = subprocess.run([dot, "-Tsvg"], input=graph_source(nodes, edges, title=title),
                            text=True, capture_output=True, check=True, timeout=30)
    document = ElementTree.fromstring(result.stdout)
    document.set("role", "img")
    document.set("aria-label", title)
    # Responsive SVG with a native viewBox; text stays selectable and searchable.
    document.set("width", document.get("viewBox").split()[2])
    document.set("height", document.get("viewBox").split()[3])
    ElementTree.register_namespace("", "http://www.w3.org/2000/svg")
    Path(destination).write_text(ElementTree.tostring(document, encoding="unicode") + "\n",
                                 encoding="utf-8")


def render_all(output_dir: str = "docs/onto-tutorial/figures") -> list[str]:
    """Regenerate all four committed SVGs, including evaluated memberships."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for stage in (1, 2, 3):
        nodes, edges = build_stage(stage)
        dest = out / f"landscape-stage{stage}.svg"
        save_tutorial_svg(nodes, edges, dest, title=STAGE_BLURB[stage])
        written.append(str(dest))
    nodes, edges = build_subsumption()
    dest = out / "subsumption-example.svg"
    save_tutorial_svg(nodes, edges, dest,
                      title="One assertion, two inferred memberships")
    written.append(str(dest))
    return written


if __name__ == "__main__":  # `uv run python docs/onto-tutorial/toolkit/landscape.py`
    for path in render_all():
        print("wrote", path)

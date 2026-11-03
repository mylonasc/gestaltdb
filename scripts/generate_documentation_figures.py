"""Rebuild the small example SVGs through GestaltDB's public visualization API.

Run with ``uv run python scripts/generate_documentation_figures.py``.
Use --check to verify committed artifacts without writing. No backend, Graphviz,
browser, npm build or optional visualization dependency is required.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from gestaltdb.graphdb import Edge, Node
from gestaltdb.reasoning import Atom, DatalogEngine, Fact, Program, ReasoningLimits, Rule, Variable
from gestaltdb.viz.api import VizOptions, visualize_nodes_edges, visualize_proof_explanation


ROOT = Path(__file__).resolve().parents[1]


def figures() -> dict[str, str]:
    """Use the same entities/rule as the quickstart and Cypher/reasoning tutorials."""
    options = VizOptions(node_label_property="name", edge_labels="all", edge_label_property="label")
    basic = visualize_nodes_edges(
        [Node("drug-1", labels=["Drug"], properties={"name": "Aspirin"}),
         Node("protein-1", labels=["Protein"], properties={"name": "PTGS1"})],
        [Edge("binding-1", "drug-1", "protein-1", properties={"type": "binds", "label": "binds (0.9)"})],
        options=replace(options, title="Aspirin binds PTGS1"),
    )
    cypher = visualize_nodes_edges(
        [Node("d1", labels=["Drug"], properties={"name": "Aspirin"}),
         Node("d2", labels=["Drug"], properties={"name": "Ibuprofen"}),
         Node("p1", labels=["Protein"], properties={"name": "PTGS1"}),
         Node("p2", labels=["Protein"], properties={"name": "PTGS2"})],
        [Edge("e1", "d1", "p1", properties={"type": "binds", "label": "binds (0.9)"}),
         Edge("e2", "d1", "p2", properties={"type": "binds", "label": "binds (0.7)"}),
         Edge("e3", "d2", "p1", properties={"type": "binds", "label": "binds (0.95)"})],
        options=replace(options, title="Cypher tutorial: drugs and protein targets"),
    )
    x, y, z = Variable("x"), Variable("y"), Variable("z")
    program = Program([Rule("candidate", [Atom(x, "binds", y), Atom(y, "inPathway", z)],
                            Atom(x, "candidateFor", z))])
    result = DatalogEngine().evaluate(
        program, [Fact("aspirin", "binds", "ptgs1"), Fact("ptgs1", "inPathway", "pathway-A")],
        limits=ReasoningLimits(), collect_proofs=True,
    )
    explanation = result.explain(Fact("aspirin", "candidateFor", "pathway-A"))
    assert not explanation.truncated
    proof = visualize_proof_explanation(
        explanation, options=VizOptions(node_label_property="label", edge_labels="all", label_max_length=100,
                                        title="One candidate-rule witness"),
    )
    return {
        "property-graph-example.svg": basic.as_svg(
            width=700, height=220, positions={"drug-1": (0, 0), "protein-1": (4, 0)},
        ),
        "cypher-example.svg": cypher.as_svg(
            width=900, height=360, positions={"d1": (0, 0), "d2": (0, 2), "p1": (4, 2), "p2": (4, 0)},
        ),
        "reasoning-witness.svg": proof.as_svg(width=1000, height=360),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/_static/examples")
    args = parser.parse_args()
    if not args.check:
        args.output.mkdir(parents=True, exist_ok=True)
    mismatches = []
    for name, content in figures().items():
        path = args.output / name
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                mismatches.append(name)
        else:
            path.write_text(content, encoding="utf-8")
    if mismatches:
        print("Rebuild documentation figures: " + ", ".join(mismatches))
        return 1
    print("Documentation figures match." if args.check else "Generated three example SVGs with gestaltdb.viz.api.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

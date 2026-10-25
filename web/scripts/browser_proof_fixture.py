"""Emit a proof-tree offline artifact used by tree-layout browser tests."""
from gestaltdb.reasoning.engine import DatalogEngine
from gestaltdb.reasoning.model import Atom, Fact, Program, ReasoningLimits, Rule, Variable
from gestaltdb.viz.api import VizOptions, visualize_proof_explanation

x, y, z = Variable("X"), Variable("Y"), Variable("Z")
program = Program([
    Rule("parent-ancestor", [Atom(x, "parent", y)], Atom(x, "ancestor", y)),
    Rule("transitive", [Atom(x, "ancestor", y), Atom(y, "ancestor", z)], Atom(x, "ancestor", z)),
])
result = DatalogEngine().evaluate(
    program,
    [Fact("a", "parent", "b"), Fact("b", "parent", "c")],
    limits=ReasoningLimits(),
    collect_proofs=True,
)
figure = visualize_proof_explanation(
    result.explain(Fact("a", "ancestor", "c")),
    options=VizOptions(node_label_property="label", edge_labels="all"),
)
print(figure.as_html())

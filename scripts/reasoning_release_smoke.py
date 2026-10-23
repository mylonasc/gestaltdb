"""Installed-distribution gate; run with python -I, outside the checkout.

create-legacy writes a fixture using the published baseline's APIs only.
check opens that fixture using the candidate and checks reasoning transitions.
core checks standalone demand evaluation with no backend or optional engine.
"""

import argparse
from importlib.metadata import version
from pathlib import Path
import sys


def check_core():
    from gestaltdb.reasoning import (
        BoundGoal, GenericEngine, GenericProgram, GenericRule, TypedAtom, TypedFact, answer,
    )
    from gestaltdb.reasoning.model import ReasoningLimits, Variable

    x, y, z = Variable("x"), Variable("y"), Variable("z")
    program = GenericProgram([
        GenericRule("base", [TypedAtom("edge", [x, y])], [], TypedAtom("reach", [x, y])),
        GenericRule("step", [TypedAtom("reach", [x, y]), TypedAtom("edge", [y, z])], [],
                    TypedAtom("reach", [x, z])),
    ])
    facts = [TypedFact("edge", ["a", "b"]), TypedFact("edge", ["b", "c"]),
             TypedFact("edge", ["u", "v"])]
    closure = GenericEngine().evaluate(program, facts, limits=ReasoningLimits())
    selected = answer(program, facts, BoundGoal("reach", ["a", y]), limits=ReasoningLimits())
    expected = {fact for fact in closure.facts if fact.predicate == "reach" and fact.values[0] == "a"}
    assert set(selected.facts) == expected == {
        TypedFact("reach", ["a", "b"]), TypedFact("reach", ["a", "c"]),
    }
    assert not {"lmdb", "plyvel", "pyrex", "polars", "pyarrow", "rdflib", "clingo"}.intersection(sys.modules)


def create_legacy(path):
    from gestaltdb.graphdb import Edge, GraphDB, Node

    graph = GraphDB.create(path, backend="lmdb", serializer="json")
    try:
        graph.put_node(Node(node_id="a", labels=["Person"], properties={"name": "Alice"}))
        graph.put_node(Node(node_id="b", labels=["Group"], properties={}))
        graph.put_edge(Edge(edge_id="e", source="a", target="b", properties={"type": "MEMBER"}))
        graph.assert_claim(subject="a", predicate="memberOf", object="b", agent="registry",
                           polarity="positive", valid=(0, 100))
    finally:
        graph.close()


def check_persisted(path):
    from gestaltdb.graphdb import GraphDB
    from gestaltdb.reasoning.adapters.claims import ClaimMapping, ClaimSelection
    from gestaltdb.reasoning.adapters.sessions import SessionScope, advance_session, start_session
    from gestaltdb.reasoning.catalog import CatalogEntry, RuleCatalog
    from gestaltdb.reasoning.maintenance import rematerialize
    from gestaltdb.reasoning.model import ReasoningLimits, Variable
    from gestaltdb.reasoning.typed import GenericProgram, GenericRule, TypedAtom, TypedFact

    x, y = Variable("x"), Variable("y")
    program = GenericProgram([
        GenericRule("member", [TypedAtom("memberOf", [x, y])], [], TypedAtom("person", [x])),
    ])
    catalog = RuleCatalog(tuple(
        CatalogEntry(rule.rule_id, "v1", GenericProgram([rule]).digest, 1) for rule in program.rules))
    scope = SessionScope(world="default", valid_time_us=50,
                         selection=ClaimSelection(), mapping=ClaimMapping())
    graph = GraphDB.open(path)
    try:
        assert graph.get_node(b"a").properties["name"] == "Alice"
        assert graph.get_edge(b"e").properties["type"] == "MEMBER"
        session = start_session(graph, program, catalog, scope=scope, limits=ReasoningLimits())
        assert TypedFact("person", ["a"]) in session.facts
        first = graph.assert_claim(subject="c", predicate="memberOf", object="b", agent="registry",
                                   polarity="positive", valid=(0, 100))
        session = advance_session(graph, session, limits=ReasoningLimits()).session
        assert TypedFact("person", ["c"]) in session.facts
        graph.correct_claim(first.logical_id, supersedes_version_id=first.version_id, valid=(0, 100))
        session = advance_session(graph, session, limits=ReasoningLimits()).session
        assert TypedFact("person", ["c"]) in session.facts
        graph.retract_claim(first.logical_id, valid=(0, 100))
        session = advance_session(graph, session, limits=ReasoningLimits()).session
        expected = rematerialize(program, [TypedFact("memberOf", ["a", "b"])], limits=ReasoningLimits())
        assert set(session.facts) == set(expected.facts)
    finally:
        graph.close()
    graph = GraphDB.open(path)
    try:
        resumed = start_session(graph, program, catalog, scope=scope, limits=ReasoningLimits())
        assert set(resumed.facts) == set(expected.facts)
    finally:
        graph.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["core", "create-legacy", "check"])
    parser.add_argument("--database", type=Path)
    args = parser.parse_args()
    import gestaltdb

    # Never let the source checkout accidentally stand in for the built wheel.
    assert "site-packages" in Path(gestaltdb.__file__).parts, gestaltdb.__file__
    if args.mode == "core":
        check_core()
    else:
        if args.database is None:
            parser.error("--database is required for persisted checks")
        if args.mode == "create-legacy":
            create_legacy(args.database)
        else:
            check_persisted(args.database)
    print(f"PASS {args.mode}: gestaltdb {version('gestaltdb')}")


if __name__ == "__main__":
    main()

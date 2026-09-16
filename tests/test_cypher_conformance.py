"""openCypher conformance map for the GestaltDB Cypher engine ([cypher-03]).

``SUPPORTED`` pins one representative query per implemented clause with its
exact columns and records. ``UNSUPPORTED`` pins every out-of-scope clause to
a located ``ValueError`` so future stages ([cypher-04]..[cypher-21]) flip
entries deliberately instead of silently changing rejection behavior.

Each entry carries the stage-issue tag (``[cypher-NN]``) that owns it:
supported entries name the issue that implemented them, unsupported entries
name the issue that will implement them.
"""

from __future__ import annotations

import re

import pytest

from gestaltdb.cypher import execute
from gestaltdb.cypher_ast import PathValue
from gestaltdb.graphdb import Edge, Node
from tests.test_cypher import FakeCypherGraph

STAGE_ISSUES = {f"[cypher-{number:02d}]" for number in range(1, 22)}


def _conformance_graph() -> FakeCypherGraph:
    """Build the deterministic two-node fixture for conformance queries."""
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a", labels=["Person"], properties={"age": 30}))
    graph.put_node(Node(node_id="b", labels=["Person"], properties={"age": 40}))
    graph.put_edge(
        Edge(edge_id="e1", source="a", target="b", properties={"type": "KNOWS", "score": 0.9})
    )
    return graph


def _canon(value: object) -> object:
    """Normalize entities to stable IDs so records compare deterministically."""
    if isinstance(value, PathValue):
        return {
            "nodes": [_canon(node) for node in value.nodes],
            "edges": [_canon(edge) for edge in value.edges],
        }
    if isinstance(value, Node):
        return f"node:{value.get_id}"
    if isinstance(value, Edge):
        return f"edge:{value.get_id}"
    if isinstance(value, list):
        return [_canon(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_canon(item) for item in value)
    if isinstance(value, dict):
        return {key: _canon(item) for key, item in value.items()}
    return value


# (feature, stage issue, query, expected columns, expected records).
SUPPORTED: list[tuple[str, str, str, tuple[str, ...], list[dict[str, object]]]] = [
    ("node-label-scan", "[cypher-02]", "MATCH (n:Person) RETURN n.id",
     ("n.id",), [{"n.id": "a"}, {"n.id": "b"}]),
    ("node-multi-label-scan", "[cypher-02]", "MATCH (n:Person:Person) RETURN n.id",
     ("n.id",), [{"n.id": "a"}, {"n.id": "b"}]),
    ("node-inline-property-map", "[cypher-02]", "MATCH (n:Person {age: 30}) RETURN n.id",
     ("n.id",), [{"n.id": "a"}]),
    ("node-all-scan", "[cypher-02]", "MATCH (n) RETURN n.id",
     ("n.id",), [{"n.id": "a"}, {"n.id": "b"}]),
    ("anchored-typed-traversal", "[cypher-02]", 'MATCH (a {id: "a"})-[:KNOWS]->(b) RETURN b.id',
     ("b.id",), [{"b.id": "b"}]),
    ("unanchored-typed-scan", "[cypher-02]", "MATCH (a)-[r:KNOWS]->(b) RETURN a.id, r.id, b.id",
     ("a.id", "r.id", "b.id"), [{"a.id": "a", "r.id": "e1", "b.id": "b"}]),
    ("relationship-type-alternatives", "[cypher-02]", "MATCH (a)-[r:KNOWS|OTHER]->(b) RETURN r.id",
     ("r.id",), [{"r.id": "e1"}]),
    ("untyped-expansion", "[cypher-02]", "MATCH (a)-->(b) RETURN a.id, b.id",
     ("a.id", "b.id"), [{"a.id": "a", "b.id": "b"}]),
    ("comma-cartesian-product", "[cypher-02]", "MATCH (a:Person), (b:Person) RETURN a.id, b.id",
     ("a.id", "b.id"),
     [{"a.id": "a", "b.id": "a"}, {"a.id": "a", "b.id": "b"},
      {"a.id": "b", "b.id": "a"}, {"a.id": "b", "b.id": "b"}]),
    ("chained-match-join", "[cypher-02]", "MATCH (a:Person) MATCH (a)-[:KNOWS]->(b) RETURN a.id, b.id",
     ("a.id", "b.id"), [{"a.id": "a", "b.id": "b"}]),
    ("where-comparison", "[cypher-02]", "MATCH (n:Person) WHERE n.age >= 35 RETURN n.id",
     ("n.id",), [{"n.id": "b"}]),
    ("where-and-range", "[cypher-02]", "MATCH (n:Person) WHERE n.age > 20 AND n.age < 35 RETURN n.id",
     ("n.id",), [{"n.id": "a"}]),
    ("where-in-list", "[cypher-02]", "MATCH (n) WHERE n.age IN [30, 31] RETURN n.id",
     ("n.id",), [{"n.id": "a"}]),
    ("where-is-null", "[cypher-02]", "MATCH (n) WHERE n.missing IS NULL RETURN n.id",
     ("n.id",), [{"n.id": "a"}, {"n.id": "b"}]),
    ("where-is-not-null", "[cypher-02]", "MATCH (n) WHERE n.age IS NOT NULL RETURN n.id",
     ("n.id",), [{"n.id": "a"}, {"n.id": "b"}]),
    ("where-not-equal", "[cypher-02]", "MATCH (n:Person) WHERE n.age <> 30 RETURN n.id",
     ("n.id",), [{"n.id": "b"}]),
    ("where-not", "[cypher-02]", "MATCH (n:Person) WHERE NOT n.age = 30 RETURN n.id",
     ("n.id",), [{"n.id": "b"}]),
    ("return-alias-order", "[cypher-02]", "MATCH (n:Person) RETURN n.id AS id ORDER BY id",
     ("id",), [{"id": "a"}, {"id": "b"}]),
    ("return-distinct-order", "[cypher-02]", "MATCH (n:Person) RETURN DISTINCT n.age ORDER BY n.age",
     ("n.age",), [{"n.age": 30}, {"n.age": 40}]),
    ("return-skip-limit", "[cypher-02]", "MATCH (n:Person) RETURN n.id ORDER BY n.id SKIP 1 LIMIT 1",
     ("n.id",), [{"n.id": "b"}]),
    ("return-star", "[cypher-02]", "MATCH (n:Person) RETURN *",
     ("n",), [{"n": "node:a"}, {"n": "node:b"}]),
    ("with-scope-replacement", "[cypher-02]", "MATCH (p:Person) WITH p AS q RETURN q.id",
     ("q.id",), [{"q.id": "a"}, {"q.id": "b"}]),
    ("with-order-limit", "[cypher-02]", "MATCH (p:Person) WITH p ORDER BY p.age DESC LIMIT 1 RETURN p.id",
     ("p.id",), [{"p.id": "b"}]),
    ("aggregate-count-star", "[cypher-02]", "MATCH (n:Person) RETURN count(*)",
     ("count(*)",), [{"count(*)": 2}]),
    ("aggregate-grouped-count", "[cypher-02]",
     "MATCH (n:Person) RETURN n.age AS age, count(*) AS total ORDER BY age",
     ("age", "total"), [{"age": 30, "total": 1}, {"age": 40, "total": 1}]),
    ("aggregate-collect", "[cypher-02]", "MATCH (n:Person) RETURN collect(n.id)",
     ("collect(n.id)",), [{"collect(n.id)": ["a", "b"]}]),
    ("aggregate-numeric", "[cypher-02]",
     "MATCH (n:Person) RETURN sum(n.age), avg(n.age), min(n.age), max(n.age)",
     ("sum(n.age)", "avg(n.age)", "min(n.age)", "max(n.age)"),
     [{"sum(n.age)": 70, "avg(n.age)": 35.0, "min(n.age)": 30, "max(n.age)": 40}]),
    ("reverse-traversal", "[cypher-02]", "MATCH (a)<-[:KNOWS]-(b) RETURN a.id, b.id",
     ("a.id", "b.id"), [{"a.id": "b", "b.id": "a"}]),
    ("undirected-traversal", "[cypher-02]", "MATCH (a)-[:KNOWS]-(b) RETURN a.id, b.id",
     ("a.id", "b.id"), [{"a.id": "a", "b.id": "b"}, {"a.id": "b", "b.id": "a"}]),
    ("projection-arithmetic", "[cypher-02]", "MATCH (n:Person) RETURN n.age + 1 AS next",
     ("next",), [{"next": 31}, {"next": 41}]),
    ("case-expression", "[cypher-04]",
     "MATCH (n:Person) RETURN CASE WHEN n.age >= 35 THEN 'senior' ELSE 'junior' END",
     ('(CASE WHEN (n.age >= 35) THEN "senior" ELSE "junior")',),
     [{'(CASE WHEN (n.age >= 35) THEN "senior" ELSE "junior")': "junior"},
      {'(CASE WHEN (n.age >= 35) THEN "senior" ELSE "junior")': "senior"}]),
    ("list-comprehension", "[cypher-04]",
     "MATCH (n:Person) RETURN [x IN [1, 2] WHERE x > 1 | x * 10]",
     ("[x IN [1, 2] WHERE (x > 1) | (x * 10)]",),
     [{"[x IN [1, 2] WHERE (x > 1) | (x * 10)]": [20]},
      {"[x IN [1, 2] WHERE (x > 1) | (x * 10)]": [20]}]),
    ("map-projection", "[cypher-04]", "MATCH (n:Person) RETURN n{.age}",
     ("n{.age}",), [{"n{.age}": {"age": 30}}, {"n{.age}": {"age": 40}}]),
    ("quantified-predicate", "[cypher-04]",
     "MATCH (n:Person) RETURN all(x IN [1, 2] WHERE x > 0)",
     ("all(x IN [1, 2] WHERE (x > 0))",),
     [{"all(x IN [1, 2] WHERE (x > 0))": True},
      {"all(x IN [1, 2] WHERE (x > 0))": True}]),
    ("reduce", "[cypher-04]",
     "MATCH (n:Person) RETURN reduce(s = 0, x IN [1, 2, 3] | s + x)",
     ("reduce(s = 0, x IN [1, 2, 3] | (s + x))",),
     [{"reduce(s = 0, x IN [1, 2, 3] | (s + x))": 6},
      {"reduce(s = 0, x IN [1, 2, 3] | (s + x))": 6}]),
    ("subscript", "[cypher-04]", "MATCH (n:Person) RETURN [1, 2, 3][1]",
     ("[1, 2, 3][1]",), [{"[1, 2, 3][1]": 2}, {"[1, 2, 3][1]": 2}]),
    ("slice", "[cypher-04]", "MATCH (n:Person) RETURN [1, 2, 3][1..2]",
     ("[1, 2, 3][1..2]",), [{"[1, 2, 3][1..2]": [2]}, {"[1, 2, 3][1..2]": [2]}]),
    ("exists-property", "[cypher-04]", "MATCH (n:Person) RETURN exists(n.age)",
     ("exists(n.age)",), [{"exists(n.age)": True}, {"exists(n.age)": True}]),
    ("scalar-function", "[cypher-05]", "MATCH (n:Person) RETURN toString(n.age)",
     ("tostring(n.age)",), [{"tostring(n.age)": "30"}, {"tostring(n.age)": "40"}]),
    ("optional-match", "[cypher-07]",
     "MATCH (n:Person) OPTIONAL MATCH (n)-[:KNOWS]->(m) RETURN n.id, m.id",
     ("n.id", "m.id"),
     [{"n.id": "a", "m.id": "b"}, {"n.id": "b", "m.id": None}]),
    ("unwind", "[cypher-08]", "UNWIND [1, 2] AS x RETURN x",
     ("x",), [{"x": 1}, {"x": 2}]),
    ("union", "[cypher-09]",
     "MATCH (n:Person) WHERE n.age = 30 RETURN n.id AS id UNION MATCH (n:Person) RETURN n.id AS id",
     ("id",), [{"id": "a"}, {"id": "b"}]),
    ("union-all", "[cypher-09]",
     "MATCH (n:Person) WHERE n.age = 30 RETURN n.id AS id UNION ALL MATCH (n:Person) WHERE n.age = 30 RETURN n.id AS id",
     ("id",), [{"id": "a"}, {"id": "a"}]),
    ("call-subquery", "[cypher-10]",
     "MATCH (n:Person) WHERE n.age = 30 CALL { MATCH (m:Person) WHERE m.age = 40 RETURN m AS o } RETURN n.id, o.id",
     ("n.id", "o.id"), [{"n.id": "a", "o.id": "b"}]),
    ("relationship-property-map", "[cypher-11]",
     "MATCH (a)-[r:KNOWS {score: 0.9}]->(b) RETURN r.id",
     ("r.id",), [{"r.id": "e1"}]),
    ("variable-length-path", "[cypher-12]",
     "MATCH (a)-[*1..3]->(b) RETURN a.id, b.id",
     ("a.id", "b.id"), [{"a.id": "a", "b.id": "b"}]),
    ("shortest-path", "[cypher-13]",
     'MATCH SHORTEST (x {id: "a"})-[*]->(y) RETURN y.id',
     ("y.id",), [{"y.id": "b"}]),
    ("shortest-path-function", "[cypher-13]",
     'MATCH (x {id: "a"}), (y {id: "b"}) RETURN shortestPath((x)-[*]->(y)) AS path',
     ("path",), [{"path": {"nodes": ["node:a", "node:b"], "edges": ["edge:e1"]}}]),
    ("path-binding", "[cypher-14]",
     "MATCH p = (a)-[r:KNOWS]->(b) RETURN length(p) AS len",
     ("len",), [{"len": 1}]),
    ("create", "[cypher-15]",
     'CREATE (n:Person {id: "c", age: 50}) RETURN n.id',
     ("n.id",), [{"n.id": "c"}]),
    ("set", "[cypher-15]",
     'MATCH (n {id: "a"}) SET n.age = 31 RETURN n.age',
     ("n.age",), [{"n.age": 31}]),
    ("delete", "[cypher-16]",
     'MATCH (n {id: "b"}) DETACH DELETE n RETURN n.id',
     ("n.id",), [{"n.id": "b"}]),
    ("merge", "[cypher-17]",
     'MERGE (n:Person {id: "c"}) RETURN n.id',
     ("n.id",), [{"n.id": "c"}]),
    ("foreach", "[cypher-18]",
     "MATCH (n:Person) WHERE n.age = 30 FOREACH (x IN [1] | SET n.tagged = true) RETURN n.id",
     ("n.id",), [{"n.id": "a"}]),
    ("show-constraints", "[cypher-19]", "SHOW CONSTRAINTS",
     ("name", "type", "label", "property"), []),
    ("three-valued-empty-membership", "[cypher-20]",
     "UNWIND [0] AS x RETURN null IN [] AS value", ("value",), [{"value": False}]),
]

# (feature, implementing stage issue, query, expected error substring).
UNSUPPORTED: list[tuple[str, str, str, str]] = [
    ("aggregate-mixed-with-scalar", "[cypher-21]", "MATCH (n) RETURN n.age + count(*)",
     "must be top-level"),
]


@pytest.mark.parametrize(
    ("feature", "issue", "query", "columns", "records"),
    SUPPORTED,
    ids=[entry[0] for entry in SUPPORTED],
)
def test_supported_clause_executes(feature, issue, query, columns, records):
    """Supported clauses execute with exact columns and records."""
    del feature, issue
    result = execute(_conformance_graph(), query)

    assert result.columns == columns
    assert [_canon(record) for record in result.records] == records


@pytest.mark.parametrize(
    ("feature", "issue", "query", "message"),
    UNSUPPORTED,
    ids=[entry[0] for entry in UNSUPPORTED],
)
def test_unsupported_clause_rejected_with_location(feature, issue, query, message):
    """Unsupported clauses fail with a located error, never silently."""
    del feature, issue
    with pytest.raises(ValueError, match=re.escape(message)) as excinfo:
        execute(_conformance_graph(), query)

    error = excinfo.value
    assert error.line == 1
    assert error.column >= 1
    assert error.offset >= 0
    assert error.source == query


def test_conformance_entries_reference_known_stage_issues():
    """Every entry traces to a stage issue from the completeness plan."""
    tagged = [(entry[0], entry[1]) for entry in SUPPORTED]
    tagged += [(entry[0], entry[1]) for entry in UNSUPPORTED]

    assert tagged
    for feature, issue in tagged:
        assert issue in STAGE_ISSUES, f"{feature} references unknown {issue}"

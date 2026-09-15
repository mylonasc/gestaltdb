"""Scalar function coverage for the GestaltDB Cypher engine ([cypher-05]).

Covers the function registry: per-function results, null propagation, type
errors, arity validation, ``DISTINCT`` restrictions, case-insensitivity, and
interaction with grouping, ordering, and ``WHERE``.
"""

import pytest

from gestaltdb.cypher import execute
from gestaltdb.graphdb import Edge, Node
from tests.test_cypher import FakeCypherGraph


def _function_graph() -> FakeCypherGraph:
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="n1", labels=["Person"], properties={"age": 30, "name": "  Ada ", "tags": ["x", "y"]}))
    graph.put_node(Node(node_id="n2", labels=["Person"], properties={"age": 40, "name": "Bob", "tags": []}))
    graph.put_edge(Edge(edge_id="e1", source="n1", target="n2", properties={"type": "KNOWS", "score": 0.9}))
    return graph


def _values(query, parameters=None):
    records = execute(_function_graph(), query, parameters=parameters).records
    assert len(records[0]) == 1
    column = next(iter(records[0]))
    return [record[column] for record in records]


def test_entity_functions():
    graph = _function_graph()

    assert _values("MATCH (n:Person) RETURN id(n) AS v") == ["n1", "n2"]
    assert _values("MATCH (n:Person) RETURN elementId(n) AS v") == ["n1", "n2"]
    assert _values("MATCH (n:Person) RETURN labels(n) AS v") == [["Person"], ["Person"]]
    assert _values("MATCH (n:Person) RETURN properties(n).age AS v") == [30, 40]
    assert _values("MATCH (n:Person) RETURN keys(n) AS v") == [
        ["age", "name", "tags"], ["age", "name", "tags"]
    ]
    assert execute(graph, "MATCH (a)-[r:KNOWS]->(b) RETURN type(r) AS t").records == [{"t": "KNOWS"}]
    assert execute(graph, "MATCH (a)-[r:KNOWS]->(b) RETURN startNode(r).id AS s, endNode(r).id AS e").records == [
        {"s": "n1", "e": "n2"}
    ]
    assert execute(graph, "MATCH (a)-[r:KNOWS]->(b) WITH startNode(r) AS s RETURN s.name AS v").records == [
        {"v": "  Ada "}
    ]


def test_entity_function_type_errors():
    graph = _function_graph()

    with pytest.raises(TypeError, match=r"id\(\) expects a node or relationship"):
        execute(graph, "MATCH (n:Person) RETURN id(n.age) AS v")
    with pytest.raises(TypeError, match=r"type\(\) expects a relationship"):
        execute(graph, "MATCH (n:Person) RETURN type(n) AS v")
    with pytest.raises(TypeError, match=r"labels\(\) expects a node"):
        execute(graph, "MATCH (a)-[r:KNOWS]->(b) RETURN labels(r) AS v")
    with pytest.raises(TypeError, match=r"startNode\(\) expects a relationship"):
        execute(graph, "MATCH (n:Person) RETURN startNode(n) AS v")


def test_coalesce_returns_first_non_null():
    assert _values("MATCH (n:Person) RETURN coalesce(n.missing, n.age, 0) AS v") == [30, 40]
    assert _values("MATCH (n:Person) RETURN coalesce(n.missing) AS v") == [None, None]


def test_list_functions():
    assert _values("MATCH (n:Person) RETURN head(n.tags) AS v") == ["x", None]
    assert _values("MATCH (n:Person) RETURN last(n.tags) AS v") == ["y", None]
    assert _values("MATCH (n:Person) RETURN tail(n.tags) AS v") == [["y"], []]
    assert _values("MATCH (n:Person) RETURN size(n.tags) AS v") == [2, 0]
    assert _values("MATCH (n:Person) RETURN length(n.tags) AS v") == [2, 0]
    assert _values("MATCH (n:Person) RETURN size(n.name) AS v") == [6, 3]
    assert _values("MATCH (n:Person) RETURN reverse(n.tags) AS v") == [["y", "x"], []]
    assert _values("MATCH (n:Person) RETURN reverse(n.name) AS v") == [" adA  ", "boB"]
    assert _values("MATCH (n:Person) RETURN range(1, 3) AS v") == [[1, 2, 3], [1, 2, 3]]
    assert _values("MATCH (n:Person) RETURN range(1, 5, 2) AS v") == [[1, 3, 5], [1, 3, 5]]
    assert _values("MATCH (n:Person) RETURN range(3, 1, -1) AS v") == [[3, 2, 1], [3, 2, 1]]


def test_list_function_errors():
    graph = _function_graph()

    with pytest.raises(TypeError, match=r"head\(\) expects list"):
        execute(graph, "MATCH (n:Person) RETURN head(n.age) AS v")
    with pytest.raises(TypeError, match=r"size\(\) expects a list, map, or string"):
        execute(graph, "MATCH (n:Person) RETURN size(n.age) AS v")
    with pytest.raises(ValueError, match=r"range\(\) step cannot be zero"):
        execute(graph, "MATCH (n:Person) RETURN range(1, 3, 0) AS v")
    with pytest.raises(TypeError, match="range"):
        execute(graph, "MATCH (n:Person) RETURN range(1.5, 3) AS v")


def test_conversion_functions():
    assert _values("MATCH (n:Person) RETURN toString(n.age) AS v") == ["30", "40"]
    assert _values("MATCH (n:Person) RETURN toString(n.age > 30) AS v") == ["false", "true"]
    assert _values("MATCH (n:Person) RETURN toInteger('42') AS v") == [42, 42]
    assert _values("MATCH (n:Person) RETURN toInteger('nope') AS v") == [None, None]
    assert _values("MATCH (n:Person) RETURN toInteger(3.9) AS v") == [3, 3]
    assert _values("MATCH (n:Person) RETURN toFloat('1.5') AS v") == [1.5, 1.5]
    assert _values("MATCH (n:Person) RETURN toFloat('nope') AS v") == [None, None]
    assert _values("MATCH (n:Person) RETURN toBoolean('true') AS v") == [True, True]
    assert _values("MATCH (n:Person) RETURN toBoolean('nope') AS v") == [None, None]
    assert _values("MATCH (n:Person) RETURN toBoolean(n.missing) AS v") == [None, None]


def test_string_functions():
    assert _values("MATCH (n:Person) RETURN trim(n.name) AS v") == ["Ada", "Bob"]
    assert _values("MATCH (n:Person) RETURN lTrim(n.name) AS v") == ["Ada ", "Bob"]
    assert _values("MATCH (n:Person) RETURN rTrim(n.name) AS v") == ["  Ada", "Bob"]
    assert _values("MATCH (n:Person) RETURN toUpper(n.name) AS v") == ["  ADA ", "BOB"]
    assert _values("MATCH (n:Person) RETURN toLower(n.name) AS v") == ["  ada ", "bob"]
    assert _values("MATCH (n:Person) RETURN replace(n.name, ' ', '-') AS v") == ["--Ada-", "Bob"]
    assert _values("MATCH (n:Person) RETURN split(n.name, ' ') AS v") == [
        ["", "", "Ada", ""], ["Bob"]
    ]
    assert _values("MATCH (n:Person) RETURN substring(n.name, 2, 3) AS v") == ["Ada", "b"]
    assert _values("MATCH (n:Person) RETURN substring(n.name, 2) AS v") == ["Ada ", "b"]
    assert _values("MATCH (n:Person) RETURN left(n.name, 3) AS v") == ["  A", "Bob"]
    assert _values("MATCH (n:Person) RETURN right(n.name, 3) AS v") == ["da ", "Bob"]


def test_string_function_errors():
    graph = _function_graph()

    with pytest.raises(TypeError, match=r"trim\(\) expects str"):
        execute(graph, "MATCH (n:Person) RETURN trim(n.age) AS v")
    with pytest.raises(TypeError, match=r"substring\(\) expects"):
        execute(graph, "MATCH (n:Person) RETURN substring(n.name, 'x') AS v")


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        ("abs(n.age - 35)", [5, 5]),
        ("ceil(n.score)", [3, 3]),
        ("floor(n.score)", [2, 2]),
        ("sqrt(16.0)", [4.0, 4.0]),
        ("pow(2, 10)", [1024, 1024]),
        ("round(2.5)", [3, 3]),
        ("round(-2.5)", [-3, -3]),
        ("round(2.675, 2)", [2.68, 2.68]),
    ],
)
def test_math_functions(call, expected):
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="n1", properties={"age": 30, "score": 2.2}))
    graph.put_node(Node(node_id="n2", properties={"age": 40, "score": 2.7}))

    records = execute(graph, f"MATCH (n) RETURN {call} AS v").records

    assert [record["v"] for record in records] == expected


def test_math_function_errors():
    graph = _function_graph()

    with pytest.raises(TypeError, match=r"abs\(\) expects numeric"):
        execute(graph, "MATCH (n:Person) RETURN abs(n.name) AS v")
    with pytest.raises(TypeError, match=r"abs\(\) expects numeric"):
        execute(graph, "MATCH (n:Person) RETURN abs(n.age > 1) AS v")
    with pytest.raises(ValueError, match="math domain|negative"):
        execute(graph, "MATCH (n:Person) RETURN sqrt(-1) AS v")


def test_extended_math_functions():
    graph = _function_graph()

    assert _values("MATCH (n:Person) RETURN sign(n.age - 30) AS v") == [0, 1]
    assert _values("MATCH (n:Person) RETURN sign(30 - n.age) AS v") == [0, -1]
    assert _values("MATCH (n:Person) RETURN exp(0) AS v") == [1.0, 1.0]
    assert _values("MATCH (n:Person) RETURN log10(100) AS v") == [2.0, 2.0]
    assert _values("MATCH (n:Person) RETURN log(1) AS v") == [0.0, 0.0]
    assert _values("MATCH (n:Person) RETURN sin(0) AS v") == [0.0, 0.0]
    assert _values("MATCH (n:Person) RETURN cos(0) AS v") == [1.0, 1.0]
    assert _values("MATCH (n:Person) RETURN tan(0) AS v") == [0.0, 0.0]
    assert _values("MATCH (n:Person) RETURN pi() AS v") == [3.141592653589793] * 2
    assert _values("MATCH (n:Person) RETURN e() AS v") == [2.718281828459045] * 2
    assert _values("MATCH (n:Person) RETURN sign(n.missing) AS v") == [None, None]

    uuids = _values("MATCH (n:Person) RETURN randomUUID() AS v")
    assert len(uuids) == 2
    assert all(isinstance(value, str) and len(value) == 36 for value in uuids)


def test_size_accepts_maps():
    assert _values("MATCH (n:Person) RETURN size(properties(n)) AS v") == [3, 3]
    assert _values("MATCH (n:Person) RETURN size({a: 1, b: 2}) AS v") == [2, 2]

    with pytest.raises(TypeError, match=r"length\(\) expects a list or string"):
        execute(_function_graph(), "MATCH (n:Person) RETURN length({a: 1}) AS v")


def test_rand_returns_unit_floats():
    values = _values("MATCH (n:Person) RETURN rand() AS v")

    assert len(values) == 2
    assert all(isinstance(value, float) and 0.0 <= value < 1.0 for value in values)


def test_null_inputs_propagate_to_null():
    graph = _function_graph()
    queries = [
        "toString(n.missing)", "toInteger(n.missing)", "toFloat(n.missing)",
        "toBoolean(n.missing)", "trim(n.missing)", "toUpper(n.missing)",
        "replace(n.missing, 'a', 'b')", "split(n.missing, ' ')",
        "substring(n.missing, 1)", "left(n.missing, 1)", "right(n.missing, 1)",
        "abs(n.missing)", "ceil(n.missing)", "sqrt(n.missing)", "pow(n.missing, 2)",
        "head(n.missing)", "size(n.missing)", "reverse(n.missing)", "tail(n.missing)",
        "keys(n.missing)", "labels(n.missing)", "type(n.missing)", "id(n.missing)",
        "properties(n.missing)", "substring(n.name, n.missing)",
    ]

    for query in queries:
        assert _values(f"MATCH (n:Person) RETURN {query} AS v") == [None, None], query


def test_arity_validation():
    graph = _function_graph()
    cases = [
        ("MATCH (n) RETURN abs() AS v", "abs expects exactly 1 argument"),
        ("MATCH (n) RETURN abs(n.age, 1) AS v", "abs expects exactly 1 argument"),
        ("MATCH (n) RETURN substring(n.name) AS v", "substring expects 2 to 3 arguments"),
        ("MATCH (n) RETURN rand(1) AS v", "rand expects exactly 0 argument"),
        ("MATCH (n) RETURN coalesce() AS v", "coalesce expects at least 1 argument"),
        ("MATCH (n) RETURN range(1) AS v", "range expects 2 to 3 arguments"),
        ("MATCH (n) RETURN replace(n.name, 'a') AS v", "replace expects exactly 3 argument"),
    ]

    for query, message in cases:
        with pytest.raises(ValueError, match=message.replace("(", r"\(").replace(")", r"\)")):
            execute(graph, query)


def test_distinct_only_for_aggregates():
    graph = _function_graph()

    with pytest.raises(ValueError, match="DISTINCT is only supported for aggregate"):
        execute(graph, "MATCH (n:Person) RETURN toString(DISTINCT n.age) AS v")
    assert execute(graph, "MATCH (n:Person) RETURN count(DISTINCT n.age) AS v").records == [{"v": 2}]


def test_unknown_and_case_insensitive_names():
    graph = _function_graph()

    with pytest.raises(ValueError, match="Unsupported function: nofunc"):
        execute(graph, "MATCH (n:Person) RETURN nofunc(n.age) AS v")
    assert _values("MATCH (n:Person) RETURN TOSTRING(n.age) AS v") == ["30", "40"]
    assert _values("MATCH (n:Person) RETURN Size(n.tags) AS v") == [2, 0]


def test_scalar_functions_group_like_other_non_aggregates():
    records = execute(
        _function_graph(),
        "MATCH (n:Person) RETURN toString(n.age) AS label, count(*) AS total ORDER BY label",
    ).records

    assert records == [
        {"label": "30", "total": 1},
        {"label": "40", "total": 1},
    ]


def test_scalar_functions_in_where_and_order_by():
    graph = _function_graph()

    assert execute(graph, "MATCH (n:Person) WHERE size(n.tags) > 0 RETURN n.id").records == [
        {"n.id": "n1"}
    ]
    assert execute(graph, "MATCH (n:Person) RETURN n.id AS id ORDER BY toLower(n.name)").records == [
        {"id": "n1"}, {"id": "n2"}
    ]


def test_aggregates_nested_in_scalars_stay_rejected():
    graph = _function_graph()

    with pytest.raises(ValueError, match="top-level"):
        execute(graph, "MATCH (n:Person) RETURN toString(count(*)) AS v")
    with pytest.raises(ValueError, match="Aggregates cannot be used in WHERE"):
        execute(graph, "MATCH (n:Person) WHERE toString(count(*)) = 'x' RETURN n.id")


def test_property_access_on_computed_values():
    graph = _function_graph()

    assert execute(graph, "MATCH (n:Person) RETURN properties(n).age AS v").records == [
        {"v": 30}, {"v": 40}
    ]
    assert execute(graph, "MATCH (n:Person) RETURN head([{a: 1}]).a AS v").records == [
        {"v": 1}, {"v": 1}
    ]

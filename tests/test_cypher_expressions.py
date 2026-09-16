"""Extended expression coverage for the GestaltDB Cypher engine ([cypher-04]).

Covers ``CASE``, subscripts/slices, list comprehensions, ``reduce``, map
projections, quantified predicates, and ``exists``: parsing, evaluation,
null semantics, scope validation, and rejection of misplaced aggregates.
"""

import pytest

from gestaltdb.cypher import execute, parse
from gestaltdb.graphdb import Node
from tests.test_cypher import FakeCypherGraph


def _expr_graph() -> FakeCypherGraph:
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="n1", labels=["Person"], properties={"age": 30, "tags": ["a", "b"]}))
    graph.put_node(Node(node_id="n2", labels=["Person"], properties={"age": 40, "tags": []}))
    graph.put_node(Node(node_id="n3", labels=["Person"], properties={"tags": ["c"]}))
    return graph


def _records(graph, query, parameters=None):
    return execute(graph, query, parameters=parameters).records


def test_case_generic_selects_first_true_branch():
    records = _records(
        _expr_graph(),
        "MATCH (n:Person) RETURN CASE WHEN n.age >= 35 THEN 'senior' WHEN n.age >= 30 THEN 'mid' ELSE 'junior' END AS band",
    )

    assert [record["band"] for record in records] == ["mid", "senior", "junior"]


def test_case_without_else_returns_null():
    records = _records(
        _expr_graph(),
        "MATCH (n:Person) RETURN CASE WHEN n.age > 100 THEN 1 END AS v",
    )

    assert [record["v"] for record in records] == [None, None, None]


def test_case_null_condition_never_matches():
    records = _records(
        _expr_graph(),
        "MATCH (n:Person) RETURN CASE WHEN n.missing THEN 1 ELSE 0 END AS v",
    )

    assert [record["v"] for record in records] == [0, 0, 0]


def test_case_simple_matches_by_equality():
    records = _records(
        _expr_graph(),
        "MATCH (n:Person) RETURN CASE n.age WHEN 30 THEN 'thirty' WHEN 40 THEN 'forty' ELSE 'other' END AS label",
    )

    assert [record["label"] for record in records] == ["thirty", "forty", "other"]


def test_case_simple_null_operand_falls_through():
    records = _records(
        _expr_graph(),
        "MATCH (n:Person) RETURN CASE n.missing WHEN 1 THEN 'x' END AS v",
    )

    assert [record["v"] for record in records] == [None, None, None]


def test_case_in_where_and_order_by():
    graph = _expr_graph()

    assert _records(
        graph, "MATCH (n:Person) WHERE CASE WHEN n.age >= 35 THEN true ELSE false END RETURN n.id"
    ) == [{"n.id": "n2"}]
    assert _records(
        graph,
        "MATCH (n:Person) RETURN n.id AS id ORDER BY CASE WHEN n.age >= 35 THEN 0 ELSE 1 END, id",
    ) == [{"id": "n2"}, {"id": "n1"}, {"id": "n3"}]


def test_subscript_list_index_and_out_of_range():
    records = _records(_expr_graph(), "MATCH (n:Person) RETURN n.tags[0] AS first, n.tags[5] AS far")

    assert [(record["first"], record["far"]) for record in records] == [("a", None), (None, None), ("c", None)]


def test_subscript_negative_index_returns_null():
    records = _records(_expr_graph(), "MATCH (n:Person) RETURN n.tags[-1] AS last")

    assert [record["last"] for record in records] == [None, None, None]


def test_subscript_map_and_dynamic_node_property():
    graph = _expr_graph()

    assert _records(graph, "MATCH (n:Person) RETURN {a: 1}['a'] AS v") == [
        {"v": 1}, {"v": 1}, {"v": 1}
    ]
    assert _records(graph, "MATCH (n:Person) RETURN n[$prop] AS v", parameters={"prop": "age"}) == [
        {"v": 30}, {"v": 40}, {"v": None}
    ]
    assert _records(graph, "MATCH (n:Person) RETURN n.tags[$i] AS v", parameters={"i": 0}) == [
        {"v": "a"}, {"v": None}, {"v": "c"}
    ]


def test_subscript_type_errors():
    graph = _expr_graph()

    with pytest.raises(TypeError, match="List index must be an integer"):
        execute(graph, "MATCH (n:Person) RETURN n.tags['0'] AS v")
    with pytest.raises(TypeError, match="Map key must be a string"):
        execute(graph, "MATCH (n:Person) RETURN {a: 1}[0] AS v")
    with pytest.raises(TypeError, match="Cannot subscript value"):
        execute(graph, "MATCH (n:Person) RETURN n.age[0] AS v")


def test_slice_bounds_and_clamping():
    records = _records(
        _expr_graph(), "MATCH (n:Person) RETURN n.tags[0..1] AS head, n.tags[1..] AS tail, n.tags[..10] AS all"
    )

    assert [(record["head"], record["tail"], record["all"]) for record in records] == [
        (["a"], ["b"], ["a", "b"]),
        ([], [], []),
        (["c"], [], ["c"]),
    ]


def test_slice_null_and_type_errors():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="n", properties={"tags": None}))

    assert execute(graph, "MATCH (n) RETURN n.tags[0..1] AS v").records == [{"v": None}]
    with pytest.raises(TypeError, match="Slice expects a list value"):
        execute(_expr_graph(), "MATCH (n:Person) RETURN n.age[0..1] AS v")


def test_list_comprehension_filter_and_map():
    records = _records(
        _expr_graph(), "MATCH (n:Person) RETURN [t IN n.tags WHERE t <> 'a' | t + '!'] AS v"
    )

    assert [record["v"] for record in records] == [["b!"], [], ["c!"]]


def test_list_comprehension_identity_and_null_input():
    graph = _expr_graph()

    assert _records(graph, "MATCH (n:Person) RETURN [t IN n.tags] AS v") == [
        {"v": ["a", "b"]}, {"v": []}, {"v": ["c"]}
    ]
    assert execute(
        FakeCypherGraph(), "MATCH (n) RETURN [x IN n.missing | x] AS v"
    ).records == []


def test_list_comprehension_null_iterable_returns_null():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="n", properties={"tags": None}))

    assert execute(graph, "MATCH (n) RETURN [t IN n.tags | t] AS v").records == [{"v": None}]


def test_list_comprehension_nested_and_shadowed_variable():
    graph = _expr_graph()

    assert _records(graph, "MATCH (n:Person) RETURN [y IN [[1, 2]] | [x IN y | x + 1]] AS v") == [
        {"v": [[2, 3]]}
    ] * 3
    assert _records(graph, "MATCH (n:Person) RETURN [n IN [1, 2] | n] AS v") == [
        {"v": [1, 2]}
    ] * 3


def test_reduce_accumulates_left_to_right():
    records = _records(
        _expr_graph(), "MATCH (n:Person) RETURN reduce(s = 0, x IN [1, 2, 3] | s + x) AS total"
    )

    assert [record["total"] for record in records] == [6, 6, 6]


def test_reduce_over_node_property_and_null():
    graph = _expr_graph()

    assert _records(graph, "MATCH (n:Person) RETURN reduce(s = '', t IN n.tags | s + t) AS v") == [
        {"v": "ab"}, {"v": ""}, {"v": "c"}
    ]
    empty = FakeCypherGraph()
    empty.put_node(Node(node_id="n", properties={"tags": None}))
    assert execute(empty, "MATCH (n) RETURN reduce(s = 0, t IN n.tags | s) AS v").records == [{"v": None}]


def test_map_projection_wildcard_selected_and_alias():
    records = _records(
        _expr_graph(), "MATCH (n:Person) RETURN n{.*, decade: n.age} AS m"
    )

    assert [record["m"] for record in records] == [
        {"age": 30, "tags": ["a", "b"], "decade": 30},
        {"age": 40, "tags": [], "decade": 40},
        {"tags": ["c"], "decade": None},
    ]


def test_map_projection_missing_property_is_null():
    records = _records(_expr_graph(), "MATCH (n:Person) RETURN n{.missing} AS m")

    assert [record["m"] for record in records] == [{"missing": None}] * 3


def test_map_projection_type_errors_and_null():
    graph = _expr_graph()

    with pytest.raises(TypeError, match="Map projection expects a map or node value"):
        execute(graph, "MATCH (n:Person) WITH n.age AS x RETURN x{.a} AS m")
    with pytest.raises(ValueError, match="Unsupported Cypher query"):
        execute(graph, "MATCH (n:Person) RETURN n.age{.a} AS m")
    null_graph = FakeCypherGraph()
    null_graph.put_node(Node(node_id="n"))
    assert execute(null_graph, "MATCH (n) RETURN n{.a} AS m").records == [{"m": {"a": None}}]
    assert execute(null_graph, "MATCH (n) WITH n.missing AS x RETURN x{.a} AS m").records == [
        {"m": None}
    ]


def test_quantified_predicates_truth_tables():
    graph = _expr_graph()
    query = (
        "MATCH (n:Person) RETURN "
        "all(x IN [1, 2] WHERE x > 0) AS a, "
        "any(x IN [1, 2] WHERE x > 1) AS b, "
        "none(x IN [1, 2] WHERE x > 5) AS c, "
        "single(x IN [1, 2] WHERE x > 1) AS d"
    )

    assert _records(graph, query) == [{"a": True, "b": True, "c": True, "d": True}] * 3


def test_quantified_empty_and_null_inputs():
    graph = _expr_graph()
    query = (
        "MATCH (n:Person) RETURN "
        "all(x IN [] WHERE x > 0) AS a, "
        "any(x IN [] WHERE x > 0) AS b, "
        "none(x IN [] WHERE x > 0) AS c, "
        "single(x IN [] WHERE x > 0) AS d, "
        "all(x IN n.missing WHERE x > 0) AS e"
    )

    assert _records(graph, query) == [
        {"a": True, "b": False, "c": True, "d": False, "e": None}
    ] * 3


def test_quantified_null_element_semantics():
    graph = _expr_graph()
    query = (
        "MATCH (n:Person) RETURN "
        "all(x IN [1, n.missing] WHERE x > 0) AS a, "
        "single(x IN [1, n.missing] WHERE x > 0) AS b, "
        "single(x IN [1, 2] WHERE x > 0) AS c"
    )

    assert _records(graph, query) == [{"a": None, "b": None, "c": False}] * 3


def test_quantified_and_exists_in_where():
    graph = _expr_graph()

    assert _records(
        graph, "MATCH (n:Person) WHERE all(x IN n.tags WHERE x <> 'z') RETURN n.id"
    ) == [{"n.id": "n1"}, {"n.id": "n2"}, {"n.id": "n3"}]
    assert _records(graph, "MATCH (n:Person) WHERE exists(n.age) RETURN n.id") == [
        {"n.id": "n1"}, {"n.id": "n2"}
    ]


def test_exists_property_true_and_false():
    records = _records(_expr_graph(), "MATCH (n:Person) RETURN exists(n.age) AS has_age")

    assert [record["has_age"] for record in records] == [True, True, False]


def test_comprehension_variable_is_local_to_scope():
    graph = _expr_graph()

    assert _records(graph, "MATCH (n:Person) RETURN [n IN [1, 2] | n] AS v") == [
        {"v": [1, 2]}
    ] * 3
    with pytest.raises(ValueError, match="unbound variable"):
        execute(graph, "MATCH (n:Person) RETURN [x IN [1] | y] AS v")


def test_aggregates_in_extended_projection_expressions():
    graph = _expr_graph()

    assert execute(graph, "MATCH (n:Person) RETURN [x IN [1] | count(*)] AS v").records == [
        {"v": [3]}
    ]
    assert execute(
        graph, "MATCH (n:Person) RETURN CASE WHEN count(*) > 0 THEN 1 ELSE 0 END AS v"
    ).records == [{"v": 1}]


def test_legacy_parse_rejects_extended_expressions():
    for query in (
        "MATCH (n) RETURN CASE WHEN true THEN 1 END AS v",
        "MATCH (n) RETURN n.tags[0] AS v",
        "MATCH (n) RETURN [x IN [1] | x] AS v",
        "MATCH (n) RETURN reduce(s = 0, x IN [1] | s) AS v",
        "MATCH (n) RETURN n{.a} AS v",
        "MATCH (n) RETURN all(x IN [1] WHERE true) AS v",
        "MATCH (n) RETURN exists(n.a) AS v",
    ):
        with pytest.raises(ValueError, match="use parse_ast"):
            parse(query)


def test_pattern_property_maps_reject_extended_expressions():
    with pytest.raises(ValueError, match="Invalid Cypher literal"):
        execute(_expr_graph(), "MATCH (n:Person {age: CASE WHEN true THEN 30 END}) RETURN n.id")


def test_grouping_and_distinct_over_extended_expressions():
    graph = _expr_graph()

    assert _records(
        graph,
        "MATCH (n:Person) RETURN CASE WHEN n.age >= 35 THEN 's' ELSE 'j' END AS band, count(*) AS total ORDER BY band",
    ) == [{"band": "j", "total": 2}, {"band": "s", "total": 1}]
    assert _records(graph, "MATCH (n:Person) RETURN DISTINCT n{.age} AS m") == [
        {"m": {"age": 30}}, {"m": {"age": 40}}, {"m": {"age": None}}
    ]

"""Error typing matrix for the GestaltDB Cypher engine ([cypher-06]).

Pins the boundary between the three failure modes: ``None`` propagation for
missing values, ``TypeError`` for mistyped operands, and ``ValueError`` (plus
narrow domain errors) for illegal values. Layered error classes
(syntax/semantic ``ValueError`` subclasses) are pinned alongside runtime
behavior.
"""

import re

import pytest

from gestaltdb.cypher import execute
from gestaltdb.cypher_errors import CypherSemanticError, CypherSyntaxError
from gestaltdb.graphdb import Node
from tests.test_cypher import FakeCypherGraph


def _graph() -> FakeCypherGraph:
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="n1", labels=["Person"], properties={"age": 30, "name": "Ada"}))
    graph.put_node(Node(node_id="n2", labels=["Person"], properties={"age": 40}))
    return graph


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("1 + n.missing", None),
        ("n.missing * 2", None),
        ("-n.missing", None),
        ("n.missing AND true", None),
        ("n.missing OR false", None),
        ("NOT n.missing", None),
        ("n.missing = 1", None),
        ("n.missing <> 1", None),
        ("n.missing IN [1]", None),
        ("n.missing IS NULL", True),
        ("[1, n.missing][1]", None),
        ("n.missing[0]", None),
        ("n.missing[0..2]", None),
        ("n.tags[0..1]", None),
        ("CASE WHEN n.missing THEN 1 ELSE 0 END", 0),
        ("CASE n.missing WHEN 1 THEN 'x' END", None),
        ("coalesce(n.missing, n.age)", 30),
        ("size(n.missing)", None),
        ("head(n.missing)", None),
        ("toString(n.missing)", None),
        ("toInteger('nope')", None),
        ("abs(n.missing)", None),
        ("[x IN n.missing | x]", None),
        ("all(x IN n.missing WHERE true)", None),
    ],
)
def test_null_inputs_propagate_to_null(expression, expected):
    records = execute(_graph(), f"MATCH (n:Person {{age: 30}}) RETURN {expression} AS v").records

    assert records == [{"v": expected}]


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ("MATCH (n) RETURN 1 + 'a' AS v", r"\+ expects two strings or two numbers"),
        ("MATCH (n) RETURN 'a' - 'b' AS v", "expects numeric operands"),
        ("MATCH (n) RETURN -'a' AS v", "expects numeric operands"),
        ("MATCH (n) RETURN 1 < 'a' AS v", "Cannot compare int and str"),
        ("MATCH (n) RETURN 'a' IN 'b' AS v", "IN expects a list value"),
        ("MATCH (n) RETURN 1 IN 2 AS v", "IN expects a list value"),
        ("MATCH (n) RETURN n.age STARTS WITH 1 AS v", "STARTS WITH expects string operands"),
        ("MATCH (n) RETURN n.age =~ 1 AS v", "=~ expects string operands"),
        ("MATCH (n) RETURN abs('x') AS v", r"abs\(\) expects numeric"),
        ("MATCH (n) RETURN abs(true) AS v", r"abs\(\) expects numeric"),
        ("MATCH (n) RETURN size(1) AS v", r"size\(\) expects a list, map, or string"),
        ("MATCH (n) RETURN head(1) AS v", r"head\(\) expects list"),
        ("MATCH (n) RETURN keys(1) AS v", r"keys\(\) expects a map, node, or relationship"),
        ("MATCH (n) RETURN properties(1) AS v", r"properties\(\) expects"),
        ("MATCH (n) RETURN toString([1]) AS v", r"toString\(\) expects"),
        ("MATCH (n) RETURN split(1, ',') AS v", r"split\(\) expects str"),
        ("MATCH (n) RETURN substring('a', 'x') AS v", r"substring\(\) expects"),
        ("MATCH (n) RETURN reverse(1) AS v", r"reverse\(\) expects a list or string"),
        ("MATCH (n) RETURN tail(1) AS v", r"tail\(\) expects list"),
        ("MATCH (n) RETURN [x IN 1 | x] AS v", "Comprehension expects a list value"),
        ("MATCH (n) RETURN reduce(s = 0, x IN 1 | s) AS v", "Reduce expects a list value"),
        ("MATCH (n) RETURN all(x IN 1 WHERE true) AS v", "expects a list value"),
        ("MATCH (n) RETURN 1[0] AS v", "Cannot subscript value of type int"),
        ("MATCH (n) RETURN [1][true] AS v", "List index must be an integer"),
        ("MATCH (n) RETURN {a: 1}[0] AS v", "Map key must be a string"),
        ("MATCH (n) RETURN [1][0..'x'] AS v", "Slice bounds must be integers"),
        ("MATCH (n) RETURN 1[0..1] AS v", "Slice expects a list value"),
    ],
)
def test_mistyped_operands_raise_type_error(query, message):
    with pytest.raises(TypeError, match=message):
        execute(_graph(), query)


@pytest.mark.parametrize(
    ("query", "error"),
    [
        ("MATCH (n) RETURN 1 / 0 AS v", ZeroDivisionError),
        ("MATCH (n) RETURN 1.0 / 0.0 AS v", ZeroDivisionError),
        ("MATCH (n) RETURN 1 % 0 AS v", ZeroDivisionError),
        ("MATCH (n) RETURN sqrt(-1) AS v", ValueError),
        ("MATCH (n) RETURN log(0) AS v", ValueError),
        ("MATCH (n) RETURN log10(-2) AS v", ValueError),
        ("MATCH (n) RETURN range(1, 3, 0) AS v", ValueError),
    ],
)
def test_illegal_values_raise_domain_errors(query, error):
    with pytest.raises(error):
        execute(_graph(), query)


def test_invalid_regex_raises_re_error():
    with pytest.raises(re.error):
        execute(_graph(), "MATCH (n) RETURN n.name =~ '[' AS v")


def test_boolean_context_strictness_boundary():
    graph = _graph()

    with pytest.raises(TypeError, match="Expected boolean expression"):
        execute(graph, "MATCH (n:Person) WHERE 1 RETURN n.id")
    with pytest.raises(TypeError, match="Expected boolean expression"):
        execute(graph, "MATCH (n:Person) WHERE 1 AND true RETURN n.id")


def test_layered_error_types():
    graph = _graph()

    with pytest.raises(CypherSyntaxError):
        execute(graph, "MATCH (n) RETURN")
    with pytest.raises(CypherSemanticError):
        execute(graph, "MATCH (n) RETURN m.id")
    with pytest.raises(ValueError, match="Unsupported function: nofunc"):
        execute(graph, "MATCH (n) RETURN nofunc(1) AS v")

    assert issubclass(CypherSyntaxError, ValueError)
    assert issubclass(CypherSemanticError, ValueError)

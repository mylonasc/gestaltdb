"""Behavior at the parser boundaries whose annotations were tightened."""

import pytest

from gestaltdb.query_engine.cypher.ast import (
    MapExpression, MapProjectionExpression, Parameter, ReturnClause,
)
from gestaltdb.query_engine.cypher.errors import CypherSemanticError, CypherSyntaxError
from gestaltdb.query_engine.cypher.parser import (
    _ASTBuilder, _located_error, parse_ast, parse_literal,
)


def test_plain_and_expression_containers_keep_their_runtime_shapes():
    builder = _ASTBuilder()
    assert builder.list_literal([1, {"nested": [2]}]) == [1, {"nested": [2]}]
    assert builder.map_literal([("nested", [1, 2])]) == {"nested": [1, 2]}
    dynamic = builder.map_literal([("value", builder.variable(["n"]))])
    assert isinstance(dynamic, MapExpression)
    query = parse_ast("MATCH (n) RETURN {nested: [1, 2]} AS m")
    assert isinstance(query.clauses[-1], ReturnClause)
    assert query.clauses[-1].items[0].expression == {"nested": [1, 2]}


def test_map_projection_parts_and_parameterized_pagination():
    query = parse_ast("MATCH (n) RETURN n{.*, .name, key: $value} AS m SKIP $skip LIMIT $limit")
    projection = query.clauses[-1]
    assert isinstance(projection, ReturnClause)
    expression = projection.items[0].expression
    assert isinstance(expression, MapProjectionExpression)
    assert expression.items == (("all",), ("property", "name"), ("alias", "key", Parameter("value")))
    assert projection.skip == Parameter("skip")
    assert projection.limit == Parameter("limit")


@pytest.mark.parametrize("error_type", [CypherSyntaxError, CypherSemanticError])
def test_located_error_preserves_requested_class_and_location(error_type):
    error = _located_error(error_type, "bad", "RETURN\nmissing", "missing")
    assert type(error) is error_type
    assert (error.line, error.column, error.offset) == (2, 1, 7)


def test_legacy_literal_eval_and_null_argument_filter_are_not_changed():
    # These existing boundaries intentionally differ from the expression grammar.
    assert parse_literal("{1, 2}") == {1, 2}
    assert parse_literal("(1, 2)") == (1, 2)
    assert _ASTBuilder().call_arguments([None, 1]) == ("arguments", (1,))

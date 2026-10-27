"""Precise normalized parser outputs, with negative contract checks."""

from typing_extensions import assert_type

from gestaltdb.query_engine.cypher.ast import (
    Expression, MapExpression, MapProjectionExpression, MapProjectionPart, Parameter, Query,
    SourceSpan, Variable,
)
from gestaltdb.query_engine.cypher.errors import CypherSemanticError, CypherSyntaxError
from gestaltdb.query_engine.cypher.parser import (
    _ASTBuilder, _located_error, parse_literal, split_top_level_args,
)
from gestaltdb.query_engine.cypher._parser_types import MatchOptions, PendingProjection


def expressions(builder: _ASTBuilder) -> None:
    assert_type(builder.variable(["n"]), Variable)
    assert_type(builder.parameter(["value"]), Parameter)
    assert_type(builder.parenthesized([Variable("n")]), Expression)
    assert_type(builder.map_literal([("name", "Ada")]), dict[str, Expression] | MapExpression)


def public_helpers() -> None:
    assert_type(parse_literal("{1, 2}"), object)
    assert_type(split_top_level_args("1, [2,3]"), list[str])
    assert_type(_located_error(CypherSyntaxError, "bad", "RETURN"), CypherSyntaxError)
    assert_type(_located_error(CypherSemanticError, "bad", "RETURN"), CypherSemanticError)
    part: MapProjectionPart = ("alias", "value", Parameter("value"))
    assert_type(MapProjectionExpression("n", (part,)).items, tuple[MapProjectionPart, ...])
    part = ("property",)  # type: ignore[assignment]
    pending: PendingProjection = {}
    pending["limit"] = Parameter("limit")
    pending["order"] = 1  # type: ignore[typeddict-item]
    Query((Variable("n"),), "")  # type: ignore[arg-type]


def normalized_options(options: MatchOptions) -> None:
    assert_type(options["where"], Expression)
    assert_type(options["skip"], int | Parameter | None)
    assert_type(SourceSpan(0, 1, 1, 1, 1, 2).start_offset, int)

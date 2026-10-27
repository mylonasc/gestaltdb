"""Contracts at the grammar transformer and canonical-query boundary.

Positional heterogeneous Lark child lists remain local dynamic seams. Tagged
outputs and normalized projection state have precise, existing record shapes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol, TypeAlias, TypedDict, TypeVar

from .ast import (
    AnchoredPatternClause, CreateConstraint, DropConstraint, Expression,
    MatchQuery, MultiMatchQuery, NodePatternClause, NodeScanQuery, OrderItem,
    Parameter, PathPatternClause, RelationshipPatternClause, RelationshipScanQuery,
    ShowConstraints, ShowIndexes,
)
from .errors import CypherSemanticError, CypherSyntaxError

if TYPE_CHECKING:
    from .parser import _MatchPatterns, _ParsedMatch, _ParsedPart, _Projection


Pagination: TypeAlias = int | Parameter | None
PaginationKey: TypeAlias = Literal["skip", "limit"]
PropertyPair: TypeAlias = tuple[str, Expression]
LabelItem: TypeAlias = tuple[bool, str]
Relationship: TypeAlias = tuple[str | None, tuple[str, ...], tuple[tuple[str, object], ...], tuple[int, int | None]]
PostfixSuffix: TypeAlias = tuple[Literal["index"], Expression] | tuple[Literal["slice"], Expression | None, Expression | None]
YieldItem: TypeAlias = tuple[Literal["yield"], str, str]
RawProcedure: TypeAlias = tuple[Literal["procedure"], str, tuple[Expression, ...], tuple[YieldItem, ...], tuple[str, ...], Pagination]
RawUnion: TypeAlias = "tuple[Literal['union'], tuple[_ParsedMatch | bool, ...]]"
ConstraintCommand: TypeAlias = CreateConstraint | DropConstraint | ShowConstraints | ShowIndexes
ParsedSource: TypeAlias = "_ParsedMatch | RawProcedure | RawUnion | ConstraintCommand"
ParsedItem: TypeAlias = "_MatchPatterns | _ParsedPart"
ProjectionValue: TypeAlias = "tuple[tuple[_Projection, ...], bool]"
LegacyPattern: TypeAlias = NodePatternClause | RelationshipPatternClause | AnchoredPatternClause | PathPatternClause
LegacyQuery: TypeAlias = MatchQuery | NodeScanQuery | RelationshipScanQuery | MultiMatchQuery
ErrorT = TypeVar("ErrorT", CypherSyntaxError, CypherSemanticError)


class SourceMeta(Protocol):
    start_pos: int
    end_pos: int
    line: int
    column: int
    end_line: int
    end_column: int


class PendingProjection(TypedDict, total=False):
    order: tuple[OrderItem, ...]
    skip: Pagination
    limit: Pagination
    modifiers: list[_ParsedPart]


class MatchOptions(TypedDict):
    returns: tuple[str, ...]
    where: Expression | None
    projections: tuple[str, ...]
    projection_expressions: tuple[Expression, ...]
    order_by: tuple[OrderItem, ...]
    skip: Pagination
    limit: Pagination
    distinct: bool

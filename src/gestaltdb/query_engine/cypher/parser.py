"""Grammar-based parser for the supported GestaltDB Cypher subset."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Any, Iterator, Literal, Sequence, cast

from lark import Lark, Token, Transformer, UnexpectedInput, v_args
from lark.exceptions import VisitError

from .ast import (
    AnchoredPatternClause,
    AndExpression,
    ArithmeticExpression,
    CaseExpression,
    Clause,
    ComparisonExpression,
    CreateClause,
    CreateConstraint,
    DeleteClause,
    DropConstraint,
    EntailsCall,
    ExistsExpression,
    Expression,
    ForeachClause,
    FunctionCall,
    InExpression,
    ListComprehension,
    ListExpression,
    MapExpression,
    MapProjectionExpression,
    MatchClause,
    MatchQuery,
    MergeClause,
    MultiMatchQuery,
    NodePattern,
    NodePatternClause,
    NodeScanQuery,
    NotExpression,
    NullPredicate,
    OptionalMatchClause,
    OrderItem,
    OrExpression,
    Parameter,
    PathPatternClause,
    PathSelector,
    PatternHop,
    ProjectionItem,
    PropertyAccessExpression,
    PropertyRef,
    QuantifiedPredicate,
    Query,
    ReduceExpression,
    RelationshipPatternClause,
    RelationshipScanQuery,
    RemoveClause,
    RemoveItem,
    RemoveLabels,
    RemoveProperty,
    ReturnClause,
    SampleTypedPathsCall,
    SetClause,
    SetItem,
    SetLabels,
    SetMerge,
    SetProperty,
    SetReplace,
    ShortestPathExpression,
    ShowIndexes,
    ShowConstraints,
    SliceExpression,
    SourceSpan,
    StringPredicate,
    SubqueryClause,
    SubscriptExpression,
    TemporalQualifier,
    TraversalHop,
    UnaryExpression,
    UnionQuery,
    UnwindClause,
    Variable,
    WhereClause,
    Wildcard,
    WithClause,
    XorExpression,
)
from .errors import CypherSemanticError, CypherSyntaxError
from ._parser_types import ErrorT, LegacyPattern, LegacyQuery, MatchOptions, Pagination, PaginationKey, ParsedSource, PendingProjection, RawProcedure, RawUnion, SourceMeta
from .semantics import QueryAnalysis, analyze_query, render_projection

_GRAMMAR = r"""
 ?start: query ";"?
 ?query: match_query | procedure_call | union_query | constraint_command
 union_query: match_query (union_operator match_query)+
 union_operator: "UNION"i "ALL"i -> union_all
               | "UNION"i -> union_distinct
 ?constraint_command: create_constraint | drop_constraint | show_constraints | show_indexes
 create_constraint: "CREATE"i "CONSTRAINT"i symbolic_name? "FOR"i "(" symbolic_name ":" symbolic_name ")" "REQUIRE"i symbolic_name "." symbolic_name "IS"i constraint_kind
 constraint_kind: "UNIQUE"i -> unique_kind
                | "NOT"i "NULL"i -> exists_kind
 drop_constraint: "DROP"i "CONSTRAINT"i symbolic_name
 show_constraints: "SHOW"i "CONSTRAINTS"i
 show_indexes: "SHOW"i ("INDEX"i | "INDEXES"i)

  match_query: (match_clause | optional_match_clause | unwind_clause | call_subquery | create_clause | set_clause | remove_clause | delete_clause | merge_clause | foreach_clause) (match_clause | optional_match_clause | where_clause | unwind_clause | call_subquery | create_clause | set_clause | remove_clause | delete_clause | merge_clause | foreach_clause | with_section)* return_full
  match_clause: "MATCH"i shortest_selector? pattern ("," pattern)* temporal_qualifier*
  optional_match_clause: "OPTIONAL"i "MATCH"i shortest_selector? pattern ("," pattern)* temporal_qualifier*
  temporal_qualifier: "FOR"i "VALID_TIME"i "AS"i "OF"i expression -> valid_time_qualifier
                    | "FOR"i "SYSTEM_TIME"i "AS"i "OF"i expression -> system_time_qualifier
  shortest_selector: QUANTIFIER "SHORTEST"i -> shortest_quantified
                   | "SHORTEST"i INTEGER? -> shortest_k
  create_clause: "CREATE"i pattern ("," pattern)*
  set_clause: "SET"i set_item ("," set_item)*
  set_item: symbolic_name "." symbolic_name COMP_OP expression -> set_property
          | symbolic_name PLUS_EQ expression -> set_merge
          | symbolic_name COMP_OP expression -> set_replace
          | symbolic_name (":" symbolic_name)+ -> set_labels
  remove_clause: "REMOVE"i remove_item ("," remove_item)*
  remove_item: symbolic_name "." symbolic_name -> remove_property
             | symbolic_name (":" symbolic_name)+ -> remove_labels
  delete_clause: "DETACH"i "DELETE"i expression ("," expression)* -> detach_delete
               | "DELETE"i expression ("," expression)* -> plain_delete
  merge_clause: "MERGE"i pattern ("," pattern)* merge_action*
  merge_action: "ON"i "CREATE"i "SET"i set_item ("," set_item)* -> on_create_action
              | "ON"i "MATCH"i "SET"i set_item ("," set_item)* -> on_match_action
  foreach_clause: "FOREACH"i "(" symbolic_name "IN"i expression "|" foreach_body ")"
  foreach_body: (match_clause | optional_match_clause | where_clause | unwind_clause | call_subquery | create_clause | set_clause | remove_clause | delete_clause | merge_clause | foreach_clause)+
  unwind_clause: "UNWIND"i expression "AS"i symbolic_name
  call_subquery: "CALL"i "{" match_query "}"
 where_clause: "WHERE"i expression
 with_section: with_clause where_clause? order_clause? skip_clause? limit_clause?
 with_clause: "WITH"i DISTINCT? return_items
 return_full: return_clause order_clause? skip_clause? limit_clause?
 return_clause: "RETURN"i DISTINCT? return_items
 return_items: return_item ("," return_item)*
 return_item: projection_expression ("AS"i symbolic_name)?
 ?projection_expression: STAR -> star_projection
                       | expression
 order_clause: "ORDER"i "BY"i order_item ("," order_item)*
 order_item: expression ORDER_DIRECTION?
skip_clause: "SKIP"i pagination_value
limit_clause: "LIMIT"i pagination_value
?pagination_value: INTEGER -> integer
                 | parameter

procedure_call: "CALL"i qualified_name "(" call_arguments ")" "YIELD"i yield_items "RETURN"i procedure_returns limit_clause?
qualified_name: symbolic_name ("." symbolic_name)*
yield_items: yield_item ("," yield_item)*
yield_item: symbolic_name ("AS"i symbolic_name)?
procedure_returns: symbolic_name ("," symbolic_name)*
call_arguments: [expression ("," expression)*]

  pattern: node_pattern traversal_hop*
         | named_pattern
  named_pattern: symbolic_name COMP_OP pattern
  node_pattern: "(" symbolic_name? labels? properties? ")"
  labels: ":" label_conjunction ("|" ":"? label_conjunction)*
  label_conjunction: label_item (":" label_item)*
  label_item: "!" symbolic_name -> neg_label
            | symbolic_name -> pos_label
properties: "{" [property_pair ("," property_pair)*] "}"
property_pair: symbolic_name ":" expression
?traversal_hop: "-" relationship? "->" node_pattern -> out_hop
              | "<-" relationship? "-" node_pattern -> in_hop
              | "-" relationship? "-" node_pattern -> any_hop
  relationship: "[" symbolic_name? rel_type_spec? var_length? properties? "]"
  rel_type_spec: ":" rel_types
  rel_types: rel_name ("|" rel_name)*
  rel_name: REL_NAME | BACKTICK_NAME
  var_length: STAR INTEGER ".." INTEGER -> var_bounded
            | STAR INTEGER ".." -> var_lower
            | STAR ".." INTEGER -> var_upper
            | STAR INTEGER -> var_exact
            | STAR -> var_unbounded

?expression: or_expr
?or_expr: xor_expr ("OR"i xor_expr)* -> or_expression
?xor_expr: and_expr ("XOR"i and_expr)* -> xor_expression
?and_expr: not_expr ("AND"i not_expr)* -> and_expression
?not_expr: "NOT"i not_expr -> not_expression
         | comparison
?comparison: additive
           | additive COMP_OP additive -> comparison_expression
           | additive "IN"i additive -> in_expression
           | additive "IS"i "NULL"i -> is_null
           | additive "IS"i "NOT"i "NULL"i -> is_not_null
           | additive "STARTS"i "WITH"i additive -> starts_with
           | additive "ENDS"i "WITH"i additive -> ends_with
           | additive "CONTAINS"i additive -> contains
 ?additive: multiplicative (ADD_OP multiplicative)* -> arithmetic_expression
 ?multiplicative: unary ((STAR | MUL_OP) unary)* -> arithmetic_expression
 ?unary: ADD_OP unary -> unary_expression
       | postfix
  ?postfix: atom (subscript_suffix)*
  subscript_suffix: "[" expression "]" -> index_suffix
                  | "[" expression ".." expression "]" -> slice_both
                  | "[" expression ".." "]" -> slice_from
                  | "[" ".." expression "]" -> slice_to
  ?atom: property_ref
       | parameter
       | literal
       | variable
       | count_star
       | function_call
       | property_access
       | shortest_single
       | shortest_all
       | case_expression
       | list_comprehension
       | quantified_predicate
       | exists_expression
       | map_projection
       | reduce_call
       | list_literal
       | map_literal
       | "(" expression ")" -> parenthesized
  ?case_expression: case_simple | case_generic
  case_simple: "CASE"i expression when_clause+ else_clause? "END"i
  case_generic: "CASE"i when_clause+ else_clause? "END"i
  when_clause: "WHEN"i expression "THEN"i expression
  else_clause: "ELSE"i expression
  list_comprehension: "[" symbolic_name "IN"i expression comp_where? comp_yield? "]"
  comp_where: "WHERE"i expression
  comp_yield: "|" expression
  quantified_predicate: QUANTIFIER "(" symbolic_name "IN"i expression "WHERE"i expression ")"
  exists_expression: "EXISTS"i "(" expression ")"
  map_projection: symbolic_name "{" map_projection_item ("," map_projection_item)* "}"
  map_projection_item: "." STAR -> proj_all
                     | "." symbolic_name -> proj_property
                     | symbolic_name ":" expression -> proj_alias
  reduce_call: "REDUCE"i "(" symbolic_name COMP_OP expression "," symbolic_name "IN"i expression "|" expression ")"
  count_star: symbolic_name "(" STAR ")"
  function_call: symbolic_name "(" DISTINCT? [expression ("," expression)*] ")"
  property_access: (function_call | map_projection | reduce_call) "." symbolic_name
  shortest_single: SHORTEST_PATH "(" pattern ")"
  shortest_all: ALL_SHORTEST_PATHS "(" pattern ")"
property_ref: symbolic_name "." symbolic_name
variable: symbolic_name
parameter: "$" symbolic_name
list_literal: "[" [expression ("," expression)*] "]"
map_literal: "{" [map_pair ("," map_pair)*] "}"
map_pair: map_key ":" expression
?map_key: symbolic_name | STRING -> string
?literal: STRING -> string
        | NUMBER -> number
        | "true"i -> true
        | "false"i -> false
        | "null"i -> null

 symbolic_name: NAME | BACKTICK_NAME
 DISTINCT.2: /DISTINCT/i
 SHORTEST_PATH.3: /SHORTESTPATH/i
 ALL_SHORTEST_PATHS.3: /ALLSHORTESTPATHS/i
 QUANTIFIER.2: /(ALL|ANY|NONE|SINGLE)/i
ORDER_DIRECTION.2: /ASC|DESC/i
 COMP_OP: "=~" | "<>" | "!=" | "<=" | ">=" | "=" | "<" | ">"
 PLUS_EQ: "+="
 ADD_OP: "+" | "-"
 MUL_OP: "/" | "%"
STAR: "*"
INTEGER: /[0-9]+/
 NUMBER: /(?:[0-9]+\.[0-9]+|\.[0-9]+|[0-9]+)(?:[eE][+-]?[0-9]+)?/
STRING: /"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'/s
BACKTICK_NAME: /`(?:``|[^`])*`/
NAME: /[^\W\d]\w*/u
REL_NAME: /[^\W\d][\w-]*/u

%import common.WS
%ignore WS
%ignore /\/\/[^\n\r]*/
%ignore /\/\*(?s:.*?)\*\//
"""


_PARSER = Lark(_GRAMMAR, parser="lalr", lexer="contextual", propagate_positions=True, maybe_placeholders=False)
_PARAMETER_RE = re.compile(r"^\$(?P<name>[^\W\d]\w*)$", re.UNICODE)


_NodePattern = NodePattern
_Hop = PatternHop
_Pattern = PathPatternClause


@dataclass(frozen=True)
class _Labels:
    values: tuple[str, ...]
    expression: tuple[tuple[frozenset[str], frozenset[str]], ...] | None = None


@dataclass(frozen=True)
class _Properties:
    values: tuple[tuple[str, object], ...]


@dataclass(frozen=True)
class _RelationshipTypes:
    values: tuple[str, ...]


@dataclass(frozen=True)
class _VarLength:
    min_length: int
    max_length: int | None


@dataclass(frozen=True)
class _MatchPatterns:
    values: tuple[_Pattern, ...]
    span: SourceSpan
    optional: bool = False
    selector: PathSelector | None = None
    qualifiers: tuple[TemporalQualifier, ...] = ()


@dataclass(frozen=True)
class _MergeAction:
    kind: str
    items: tuple[SetItem, ...]


@dataclass(frozen=True)
class _Projection:
    expression: Expression
    alias: str | None = None
    span: SourceSpan | None = None


@dataclass(frozen=True)
class _CaseWhen:
    condition: Expression
    value: Expression


@dataclass(frozen=True)
class _CaseElse:
    value: Expression


@dataclass(frozen=True)
class _CompWhere:
    value: Expression


@dataclass(frozen=True)
class _CompYield:
    value: Expression


@dataclass(frozen=True)
class _ParsedPart:
    kind: str
    value: object
    span: SourceSpan


@dataclass(frozen=True)
class _ParsedMatch:
    pattern_groups: tuple[tuple[_Pattern, ...], ...]
    where: Expression | None
    projections: tuple[_Projection, ...]
    distinct: bool
    order_by: tuple[OrderItem, ...]
    skip: int | Parameter | None
    limit: int | Parameter | None
    match_groups: tuple[_MatchPatterns, ...]
    where_part: _ParsedPart | None
    return_part: _ParsedPart
    modifier_parts: tuple[_ParsedPart, ...]
    sequence: tuple[object, ...] = ()


class _ASTBuilder(Transformer[Token, object]):
    def symbolic_name(self, children: list[Token]) -> str:
        text = str(children[0])
        return text[1:-1].replace("``", "`") if text.startswith("`") else text

    def rel_name(self, children: list[Token]) -> str:
        return self.symbolic_name(children)

    def string(self, children: list[Token]) -> str:
        return _decode_string(str(children[0]))

    def number(self, children: list[Token]) -> int | float:
        text = str(children[0])
        return float(text) if any(char in text for char in ".eE") else int(text)

    def integer(self, children: list[Token]) -> int:
        return int(children[0])

    def true(self, _children: list[Token]) -> bool:
        return True

    def false(self, _children: list[Token]) -> bool:
        return False

    def null(self, _children: list[Token]) -> None:
        return None

    def variable(self, children: list[str]) -> Variable:
        return Variable(children[0])

    def property_ref(self, children: list[str]) -> PropertyRef:
        return PropertyRef(children[0], children[1])

    def property_access(self, children: list[Any]) -> PropertyAccessExpression:
        return PropertyAccessExpression(children[0], children[1])

    def parameter(self, children: list[str]) -> Parameter:
        return Parameter(children[0])

    def list_literal(self, children: list[Expression]) -> list[Expression] | ListExpression:
        values = list(children)
        return values if all(_is_plain_value(value) for value in values) else ListExpression(tuple(values))

    def property_pair(self, children: list[Any]) -> tuple[str, Expression]:
        return children[0], children[1]

    def map_literal(self, children: list[tuple[str, Expression] | None]) -> dict[str, Expression] | MapExpression:
        items = tuple(item for item in children if item is not None)
        return dict(items) if all(_is_plain_value(value) for _, value in items) else MapExpression(items)

    def map_pair(self, children: list[Any]) -> tuple[str, Expression]:
        return children[0], children[1]

    def parenthesized(self, children: list[Expression]) -> Expression:
        return children[0]

    def when_clause(self, children: list[Expression]) -> _CaseWhen:
        return _CaseWhen(children[0], children[1])

    def else_clause(self, children: list[Expression]) -> _CaseElse:
        return _CaseElse(children[0])

    def case_simple(self, children: list[Any]) -> CaseExpression:
        operand = children[0]
        rest = children[1:]
        return CaseExpression(
            tuple((item.condition, item.value) for item in rest if isinstance(item, _CaseWhen)),
            next((item.value for item in rest if isinstance(item, _CaseElse)), None),
            operand,
        )

    def case_generic(self, children: list[_CaseWhen | _CaseElse]) -> CaseExpression:
        return CaseExpression(
            tuple((item.condition, item.value) for item in children if isinstance(item, _CaseWhen)),
            next((item.value for item in children if isinstance(item, _CaseElse)), None),
        )

    def comp_where(self, children: list[Expression]) -> _CompWhere:
        return _CompWhere(children[0])

    def comp_yield(self, children: list[Expression]) -> _CompYield:
        return _CompYield(children[0])

    def list_comprehension(self, children: list[Any]) -> ListComprehension:
        return ListComprehension(
            children[0],
            children[1],
            next((item.value for item in children[2:] if isinstance(item, _CompWhere)), None),
            next((item.value for item in children[2:] if isinstance(item, _CompYield)), None),
        )

    def quantified_predicate(self, children: list[Any]) -> QuantifiedPredicate:
        return QuantifiedPredicate(str(children[0]).lower(), children[1], children[2], children[3])

    def exists_expression(self, children: list[Expression]) -> ExistsExpression:
        return ExistsExpression(children[0])

    def proj_all(self, _children: list[Token]) -> tuple[Literal["all"]]:
        return ("all",)

    def proj_property(self, children: list[str]) -> tuple[Literal["property"], str]:
        return ("property", children[0])

    def proj_alias(self, children: list[Any]) -> tuple[Literal["alias"], str, Expression]:
        return ("alias", children[0], children[1])

    def map_projection(self, children: list[Any]) -> MapProjectionExpression:
        return MapProjectionExpression(children[0], tuple(children[1:]))

    @v_args(meta=True)
    def reduce_call(self, meta: SourceMeta, children: list[Any]) -> ReduceExpression:
        parts = [child for child in children if not isinstance(child, Token)]
        operator = next(child for child in children if isinstance(child, Token))
        if str(operator) != "=":
            raise CypherSyntaxError(
                "REDUCE accumulator must use =",
                line=meta.line,
                column=meta.column,
                offset=meta.start_pos,
                source="",
            )
        return ReduceExpression(parts[0], parts[1], parts[2], parts[3], parts[4])

    def index_suffix(self, children: list[Expression]) -> tuple[Literal["index"], Expression]:
        return ("index", children[0])

    def slice_both(self, children: list[Expression]) -> tuple[Literal["slice"], Expression, Expression]:
        return ("slice", children[0], children[1])

    def slice_from(self, children: list[Expression]) -> tuple[Literal["slice"], Expression, None]:
        return ("slice", children[0], None)

    def slice_to(self, children: list[Expression]) -> tuple[Literal["slice"], None, Expression]:
        return ("slice", None, children[0])

    def postfix(self, children: list[Any]) -> Expression:
        node: Expression = children[0]
        for suffix in children[1:]:
            if suffix[0] == "index":
                node = SubscriptExpression(node, suffix[1])
            else:
                node = SliceExpression(node, suffix[1], suffix[2])
        return node

    @v_args(meta=True)
    def count_star(self, meta: SourceMeta, children: list[Token]) -> FunctionCall:
        span = _source_span(meta)
        return FunctionCall(str(children[0]).lower(), (Wildcard(span=span),), False, span=span)

    @v_args(meta=True)
    def function_call(self, meta: SourceMeta, children: list[Expression | Token]) -> FunctionCall:
        name = str(children[0]).lower()
        distinct = False
        arguments: list[Expression] = []
        for child in children[1:]:
            if isinstance(child, Token):
                distinct = True
            else:
                arguments.append(child)
        return FunctionCall(name, tuple(arguments), distinct, span=_source_span(meta))

    def arithmetic_expression(self, children: list[Expression]) -> Expression:
        if len(children) == 1:
            return children[0]
        expression = children[0]
        for index in range(1, len(children), 2):
            expression = ArithmeticExpression(expression, str(children[index]), children[index + 1])
        return expression

    def unary_expression(self, children: list[Expression]) -> UnaryExpression:
        return UnaryExpression(str(children[0]), children[1])

    def comparison_expression(self, children: list[Expression]) -> ComparisonExpression:
        return ComparisonExpression(children[0], str(children[1]), children[2])

    def in_expression(self, children: list[Expression]) -> InExpression:
        return InExpression(children[0], children[1])

    def is_null(self, children: list[Expression]) -> NullPredicate:
        return NullPredicate(children[0])

    def is_not_null(self, children: list[Expression]) -> NullPredicate:
        return NullPredicate(children[0], negated=True)

    def starts_with(self, children: list[Expression]) -> StringPredicate:
        return StringPredicate(children[0], "STARTS WITH", children[1])

    def ends_with(self, children: list[Expression]) -> StringPredicate:
        return StringPredicate(children[0], "ENDS WITH", children[1])

    def contains(self, children: list[Expression]) -> StringPredicate:
        return StringPredicate(children[0], "CONTAINS", children[1])

    def not_expression(self, children: list[Expression]) -> NotExpression:
        return NotExpression(children[0])

    def and_expression(self, children: list[Expression]) -> Expression:
        return children[0] if len(children) == 1 else AndExpression(tuple(children))

    def xor_expression(self, children: list[Expression]) -> Expression:
        return children[0] if len(children) == 1 else XorExpression(tuple(children))

    def or_expression(self, children: list[Expression]) -> Expression:
        return children[0] if len(children) == 1 else OrExpression(tuple(children))

    def labels(self, children: list[tuple[tuple[bool, str], ...]]) -> _Labels:
        conjunctions = [tuple(child) for child in children]
        if len(conjunctions) == 1 and all(not negated for negated, _ in conjunctions[0]):
            return _Labels(tuple(name for _, name in conjunctions[0]), None)
        return _Labels(
            (),
            tuple(
                (
                    frozenset(name for negated, name in conjunction if not negated),
                    frozenset(name for negated, name in conjunction if negated),
                )
                for conjunction in conjunctions
            ),
        )

    def label_conjunction(self, children: list[tuple[bool, str]]) -> tuple[tuple[bool, str], ...]:
        return tuple(children)

    def neg_label(self, children: list[str]) -> tuple[bool, str]:
        return (True, children[0])

    def pos_label(self, children: list[str]) -> tuple[bool, str]:
        return (False, children[0])

    def properties(self, children: list[tuple[str, Expression]]) -> _Properties:
        return _Properties(tuple(children))

    def node_pattern(self, children: list[str | _Labels | _Properties]) -> NodePattern:
        variable = next((item for item in children if isinstance(item, str)), None)
        labels = next((item for item in children if isinstance(item, _Labels)), _Labels((), None))
        properties = next((item.values for item in children if isinstance(item, _Properties)), ())
        return _NodePattern(variable, labels.values, properties, labels.expression)

    @v_args(meta=True)
    def named_pattern(self, meta: SourceMeta, children: list[object]) -> PathPatternClause:
        operator = next(child for child in children if isinstance(child, Token))
        if str(operator) != "=":
            raise CypherSyntaxError(
                "Path binding must use =",
                line=meta.line,
                column=meta.column,
                offset=meta.start_pos,
                source="",
            )
        variable = next(item for item in children if isinstance(item, str))
        pattern = next(item for item in children if isinstance(item, _Pattern))
        if pattern.name is not None:
            raise CypherSyntaxError(
                "Path patterns cannot be nested",
                line=meta.line,
                column=meta.column,
                offset=meta.start_pos,
                source="",
            )
        return _Pattern(pattern.source, pattern.hops, variable)

    def rel_types(self, children: list[str]) -> _RelationshipTypes:
        return _RelationshipTypes(tuple(children))

    def rel_type_spec(self, children: list[_RelationshipTypes]) -> _RelationshipTypes:
        return children[0]

    def relationship(self, children: list[object]) -> tuple[str | None, tuple[str, ...], tuple[tuple[str, object], ...], tuple[int, int | None]]:
        rel_var = next((item for item in children if isinstance(item, str)), None)
        edge_types = next((item.values for item in children if isinstance(item, _RelationshipTypes)), ())
        properties = next((item.values for item in children if isinstance(item, _Properties)), ())
        bounds = next(
            ((item.min_length, item.max_length) for item in children if isinstance(item, _VarLength)),
            (1, 1),
        )
        return rel_var, edge_types, properties, bounds

    def _var_bounds(self, children: list[Token]) -> list[int]:
        return [int(child) for child in children if isinstance(child, Token) and child.type == "INTEGER"]

    def var_bounded(self, children: list[Token]) -> _VarLength:
        bounds = self._var_bounds(children)
        return _VarLength(bounds[0], bounds[1])

    def var_lower(self, children: list[Token]) -> _VarLength:
        bounds = self._var_bounds(children)
        return _VarLength(bounds[0], None)

    def var_upper(self, children: list[Token]) -> _VarLength:
        bounds = self._var_bounds(children)
        return _VarLength(1, bounds[0])

    def var_exact(self, children: list[Token]) -> _VarLength:
        bounds = self._var_bounds(children)
        return _VarLength(bounds[0], bounds[0])

    def var_unbounded(self, _children: list[Token]) -> _VarLength:
        return _VarLength(1, None)

    def out_hop(self, children: list[Any]) -> PatternHop:
        relationship, node = (children if len(children) == 2 else ((None, (), (), (1, 1)), children[0]))
        return _Hop(relationship[0], relationship[1], node, "out", relationship[2], relationship[3][0], relationship[3][1])

    def in_hop(self, children: list[Any]) -> PatternHop:
        relationship, node = (children if len(children) == 2 else ((None, (), (), (1, 1)), children[0]))
        return _Hop(relationship[0], relationship[1], node, "in", relationship[2], relationship[3][0], relationship[3][1])

    def any_hop(self, children: list[Any]) -> PatternHop:
        relationship, node = (children if len(children) == 2 else ((None, (), (), (1, 1)), children[0]))
        return _Hop(relationship[0], relationship[1], node, "any", relationship[2], relationship[3][0], relationship[3][1])

    def pattern(self, children: list[Any]) -> PathPatternClause:
        if len(children) == 1 and isinstance(children[0], _Pattern):
            return children[0]
        return _Pattern(children[0], tuple(children[1:]))

    @v_args(meta=True)
    def match_clause(self, meta: SourceMeta, children: list[object]) -> _MatchPatterns:
        selector = next((item for item in children if isinstance(item, PathSelector)), None)
        return _MatchPatterns(
            tuple(item for item in children if isinstance(item, _Pattern)),
            _source_span(meta),
            False,
            selector,
            tuple(item for item in children if isinstance(item, TemporalQualifier)),
        )

    @v_args(meta=True)
    def optional_match_clause(self, meta: SourceMeta, children: list[object]) -> _MatchPatterns:
        selector = next((item for item in children if isinstance(item, PathSelector)), None)
        return _MatchPatterns(
            tuple(item for item in children if isinstance(item, _Pattern)),
            _source_span(meta),
            True,
            selector,
            tuple(item for item in children if isinstance(item, TemporalQualifier)),
        )

    @v_args(meta=True)
    def valid_time_qualifier(self, meta: SourceMeta, children: list[Expression]) -> TemporalQualifier:
        return TemporalQualifier("valid", children[0], span=_source_span(meta))

    @v_args(meta=True)
    def system_time_qualifier(self, meta: SourceMeta, children: list[Expression]) -> TemporalQualifier:
        return TemporalQualifier("system", children[0], span=_source_span(meta))

    def shortest_quantified(self, children: list[Token]) -> PathSelector:
        return PathSelector(cast(Literal["any", "all"], str(children[0]).lower()), None)

    def shortest_k(self, children: list[Token]) -> PathSelector:
        limit = next(
            (child for child in children if isinstance(child, Token) and child.type == "INTEGER"),
            None,
        )
        return PathSelector("any", int(limit) if limit is not None else 1)

    def shortest_single(self, children: list[object]) -> ShortestPathExpression:
        pattern = next(child for child in children if isinstance(child, _Pattern))
        return ShortestPathExpression(pattern, False)

    def shortest_all(self, children: list[object]) -> ShortestPathExpression:
        pattern = next(child for child in children if isinstance(child, _Pattern))
        return ShortestPathExpression(pattern, True)

    @v_args(meta=True)
    def unwind_clause(self, meta: SourceMeta, children: list[Any]) -> _ParsedPart:
        return _ParsedPart("unwind", (children[0], children[1]), _source_span(meta))

    @v_args(meta=True)
    def call_subquery(self, meta: SourceMeta, children: list[_ParsedMatch]) -> _ParsedPart:
        return _ParsedPart("subquery", children[0], _source_span(meta))

    @v_args(meta=True)
    def create_clause(self, meta: SourceMeta, children: list[PathPatternClause]) -> _ParsedPart:
        return _ParsedPart("create", tuple(children), _source_span(meta))

    @v_args(meta=True)
    def set_property(self, meta: SourceMeta, children: list[Any]) -> SetProperty:
        operator = next(child for child in children if isinstance(child, Token))
        if str(operator) != "=":
            raise CypherSyntaxError(
                "SET property assignment must use =",
                line=meta.line,
                column=meta.column,
                offset=meta.start_pos,
                source="",
            )
        parts = [child for child in children if not isinstance(child, Token)]
        return SetProperty(parts[0], parts[1], parts[2])

    def set_merge(self, children: list[Any]) -> SetMerge:
        parts = [child for child in children if not isinstance(child, Token)]
        return SetMerge(parts[0], parts[1])

    @v_args(meta=True)
    def set_replace(self, meta: SourceMeta, children: list[Any]) -> SetReplace:
        operator = next(child for child in children if isinstance(child, Token))
        if str(operator) != "=":
            raise CypherSyntaxError(
                "SET map assignment must use =",
                line=meta.line,
                column=meta.column,
                offset=meta.start_pos,
                source="",
            )
        parts = [child for child in children if not isinstance(child, Token)]
        return SetReplace(parts[0], parts[1])

    def set_labels(self, children: list[str]) -> SetLabels:
        names = [child for child in children if isinstance(child, str)]
        return SetLabels(names[0], tuple(names[1:]))

    @v_args(meta=True)
    def set_clause(self, meta: SourceMeta, children: list[SetItem]) -> _ParsedPart:
        return _ParsedPart("set", tuple(children), _source_span(meta))

    def remove_property(self, children: list[str]) -> RemoveProperty:
        return RemoveProperty(children[0], children[1])

    def remove_labels(self, children: list[str]) -> RemoveLabels:
        names = [child for child in children if isinstance(child, str)]
        return RemoveLabels(names[0], tuple(names[1:]))

    @v_args(meta=True)
    def remove_clause(self, meta: SourceMeta, children: list[RemoveItem]) -> _ParsedPart:
        return _ParsedPart("remove", tuple(children), _source_span(meta))

    @v_args(meta=True)
    def detach_delete(self, meta: SourceMeta, children: list[Expression]) -> _ParsedPart:
        return _ParsedPart("delete", (True, tuple(children)), _source_span(meta))

    @v_args(meta=True)
    def plain_delete(self, meta: SourceMeta, children: list[Expression]) -> _ParsedPart:
        return _ParsedPart("delete", (False, tuple(children)), _source_span(meta))

    def on_create_action(self, children: list[SetItem]) -> _MergeAction:
        return _MergeAction("create", tuple(cast(Sequence[SetItem], children)))

    def on_match_action(self, children: list[SetItem]) -> _MergeAction:
        return _MergeAction("match", tuple(cast(Sequence[SetItem], children)))

    @v_args(meta=True)
    def merge_clause(self, meta: SourceMeta, children: list[object]) -> _ParsedPart:
        patterns = tuple(item for item in children if isinstance(item, _Pattern))
        actions = [item for item in children if isinstance(item, _MergeAction)]
        return _ParsedPart(
            "merge",
            (patterns, actions),
            _source_span(meta),
        )

    @v_args(meta=True)
    def foreach_clause(self, meta: SourceMeta, children: list[Any]) -> _ParsedPart:
        return _ParsedPart("foreach", (children[0], children[1], children[-1]), _source_span(meta))

    def foreach_body(self, children: list[_ParsedPart]) -> tuple[_ParsedPart, ...]:
        return tuple(children)

    def unique_kind(self, _children: list[Token]) -> Literal["unique"]:
        return "unique"

    def exists_kind(self, _children: list[Token]) -> Literal["exists"]:
        return "exists"

    def create_constraint(self, children: list[str]) -> CreateConstraint:
        parts = [child for child in children if isinstance(child, str)]
        kind = parts[-1]
        names = parts[:-1]
        if len(names) == 5:
            name, variable, label, require_variable, prop = names
        else:
            name = None
            variable, label, require_variable, prop = names
        if require_variable != variable:
            raise CypherSyntaxError(
                "Constraint variable must match the pattern variable",
                line=1,
                column=1,
                offset=0,
                source="",
            )
        return CreateConstraint(label, prop, cast(Literal["unique", "exists"], kind), name)

    def drop_constraint(self, children: list[str]) -> DropConstraint:
        return DropConstraint(children[0])

    def show_constraints(self, _children: list[Token]) -> ShowConstraints:
        return ShowConstraints()

    def show_indexes(self, _children: list[Token]) -> ShowIndexes:
        return ShowIndexes()

    def union_all(self, _children: list[Token]) -> bool:
        return True

    def union_distinct(self, _children: list[Token]) -> bool:
        return False

    def union_query(self, children: list[_ParsedMatch | bool]) -> RawUnion:
        return ("union", tuple(children))

    @v_args(meta=True)
    def where_clause(self, meta: SourceMeta, children: list[Expression]) -> _ParsedPart:
        return _ParsedPart("where", children[0], _source_span(meta))

    def star_projection(self, _children: list[Token]) -> str:
        return "*"

    @v_args(meta=True)
    def return_item(self, meta: SourceMeta, children: list[Any]) -> _Projection:
        return _Projection(
            children[0],
            children[1] if len(children) > 1 else None,
            _source_span(meta),
        )

    def return_items(self, children: list[_Projection]) -> tuple[_Projection, ...]:
        return tuple(children)

    @v_args(meta=True)
    def return_clause(self, meta: SourceMeta, children: list[Any]) -> _ParsedPart:
        distinct = bool(children and str(children[0]).upper() == "DISTINCT")
        return _ParsedPart("return", (children[-1], distinct), _source_span(meta))

    def order_item(self, children: list[Expression]) -> OrderItem:
        expression = children[0]
        direction = str(children[1]).upper() if len(children) > 1 else "ASC"
        return OrderItem(render_projection(expression), direction == "DESC", expression)

    @v_args(meta=True)
    def order_clause(self, meta: SourceMeta, children: list[OrderItem]) -> _ParsedPart:
        return _ParsedPart("order", tuple(children), _source_span(meta))

    @v_args(meta=True)
    def skip_clause(self, meta: SourceMeta, children: list[int | Parameter]) -> _ParsedPart:
        return _ParsedPart("skip", children[0], _source_span(meta))

    @v_args(meta=True)
    def limit_clause(self, meta: SourceMeta, children: list[int | Parameter]) -> _ParsedPart:
        return _ParsedPart("limit", children[0], _source_span(meta))

    def call_arguments(self, children: list[Expression]) -> tuple[Literal["arguments"], tuple[Expression, ...]]:
        return ("arguments", tuple(item for item in children if item is not None))

    @v_args(meta=True)
    def with_clause(self, meta: SourceMeta, children: list[Any]) -> _ParsedPart:
        distinct = bool(children and str(children[0]).upper() == "DISTINCT")
        return _ParsedPart("with", (children[-1], distinct), _source_span(meta))

    def with_section(self, children: list[_ParsedPart]) -> tuple[_ParsedPart, ...]:
        return tuple(children)

    def return_full(self, children: list[_ParsedPart]) -> tuple[_ParsedPart, ...]:
        return tuple(children)

    def match_query(self, children: list[object]) -> _ParsedMatch:
        sequence: list[object] = []
        for item in children:
            if isinstance(item, tuple) and item and all(isinstance(part, _ParsedPart) for part in item):
                sequence.extend(item)
            else:
                sequence.append(item)
        match_groups = tuple(item for item in sequence if isinstance(item, _MatchPatterns))
        parts = tuple(item for item in sequence if isinstance(item, _ParsedPart))
        where_part = next((item for item in parts if item.kind == "where"), None)
        return_part = next(item for item in parts if item.kind == "return")
        return_items, distinct = cast("tuple[tuple[_Projection, ...], bool]", return_part.value)
        modifier_parts = tuple(
            item for item in parts if item.kind in {"order", "skip", "limit"}
        )
        order_by = next(
            (item.value for item in modifier_parts if item.kind == "order"), ()
        )
        skip = next(
            (item.value for item in modifier_parts if item.kind == "skip"), None
        )
        limit = next(
            (item.value for item in modifier_parts if item.kind == "limit"), None
        )
        return _ParsedMatch(
            tuple(item.values for item in match_groups),
            cast(Expression, where_part.value) if where_part is not None else None,
            return_items,
            distinct,
            cast(tuple[OrderItem, ...], order_by),
            cast(Pagination, skip),
            cast(Pagination, limit),
            match_groups,
            where_part,
            return_part,
            modifier_parts,
            tuple(sequence),
        )

    def qualified_name(self, children: list[str]) -> str:
        return ".".join(children)

    def yield_item(self, children: list[str]) -> tuple[Literal["yield"], str, str]:
        return ("yield", children[0], children[-1])

    def yield_items(self, children: list[tuple[Literal["yield"], str, str]]) -> tuple[Literal["yields"], tuple[tuple[Literal["yield"], str, str], ...]]:
        return ("yields", tuple(children))

    def procedure_returns(self, children: list[str]) -> tuple[Literal["returns"], tuple[str, ...]]:
        return ("returns", tuple(children))

    def procedure_call(self, children: list[Any]) -> RawProcedure:
        limit = next(
            (
                item.value
                for item in children
                if isinstance(item, _ParsedPart) and item.kind == "limit"
            ),
            None,
        )
        name = next(item for item in children if isinstance(item, str))
        arguments = next(item[1] for item in children if isinstance(item, tuple) and item and item[0] == "arguments")
        yielded = next(item[1] for item in children if isinstance(item, tuple) and item and item[0] == "yields")
        returns = next(item[1] for item in children if isinstance(item, tuple) and item and item[0] == "returns")
        return ("procedure", name, arguments, yielded, returns, cast(Pagination, limit))


_BUILDER = _ASTBuilder()

def parse(query: str) -> MatchQuery | SampleTypedPathsCall | EntailsCall | NodeScanQuery | RelationshipScanQuery | MultiMatchQuery:
    """Parse the supported Cypher subset into runtime-compatible AST objects."""
    parsed = _parse_source(query)

    if isinstance(parsed, tuple) and parsed and parsed[0] == "procedure":
        return _build_sample_call(parsed, query)
    if isinstance(parsed, (CreateConstraint, DropConstraint, ShowConstraints, ShowIndexes)):
        raise _located_error(
            CypherSemanticError,
            "Query cannot be represented by legacy parse(); use parse_ast()",
            query,
        )
    if isinstance(parsed, tuple) and parsed and parsed[0] == "union":
        raise _located_error(
            CypherSemanticError,
            "Query cannot be represented by legacy parse(); use parse_ast()",
            query,
        )
    canonical = _build_canonical_query(parsed, query)
    if any(isinstance(clause, (WithClause, OptionalMatchClause, UnwindClause, SubqueryClause, CreateClause, SetClause, RemoveClause, DeleteClause, MergeClause, ForeachClause)) for clause in canonical.clauses):
        raise _located_error(
            CypherSemanticError,
            "Query cannot be represented by legacy parse(); use parse_ast()",
            query,
        )
    if any(getattr(clause, "selector", None) is not None for clause in canonical.clauses):
        raise _located_error(
            CypherSemanticError,
            "Query cannot be represented by legacy parse(); use parse_ast()",
            query,
        )
    if any(getattr(clause, "qualifiers", ()) for clause in canonical.clauses):
        raise _located_error(
            CypherSemanticError,
            "Temporal qualifiers cannot be represented by legacy parse(); use parse_ast()",
            query,
        )
    if _canonical_uses_functions(canonical) or _canonical_uses_extended_expressions(canonical):
        raise _located_error(
            CypherSemanticError,
            "Query cannot be represented by legacy parse(); use parse_ast()",
            query,
        )
    analysis = analyze_query(canonical)
    return _build_match_query(parsed, query, analysis)


def parse_ast(query: str) -> Query | SampleTypedPathsCall | EntailsCall | UnionQuery | CreateConstraint | DropConstraint | ShowConstraints | ShowIndexes:
    """Parse the supported Cypher subset into the canonical clause AST."""
    parsed = _parse_source(query)
    if isinstance(parsed, tuple) and parsed and parsed[0] == "procedure":
        return _build_sample_call(parsed, query)
    if isinstance(parsed, (CreateConstraint, DropConstraint, ShowConstraints, ShowIndexes)):
        return parsed
    if isinstance(parsed, tuple) and parsed and parsed[0] == "union":
        union_canonical = _build_union_query(parsed, query)
        _validate_temporal_contract(union_canonical, query)
        return union_canonical

    canonical = _build_canonical_query(parsed, query)
    analyze_query(canonical)
    _validate_temporal_contract(canonical, query)
    return canonical


def _walk_queries(value: Query | UnionQuery) -> Iterator[Query]:
    """Yield a canonical query and all nested subquery/FOREACH queries."""
    queries = value.branches if isinstance(value, UnionQuery) else (value,)
    for query in queries:
        yield query
        for clause in query.clauses:
            if isinstance(clause, SubqueryClause):
                yield from _walk_queries(clause.query)
            elif isinstance(clause, ForeachClause):
                yield from _walk_queries(Query(clause.body, query.source))


def _validate_temporal_contract(value: Query | UnionQuery, source: str) -> None:
    """Validate query-wide qualifier dependencies and read-only semantics."""
    qualifiers = [
        qualifier
        for query in _walk_queries(value)
        for clause in query.clauses
        if isinstance(clause, (MatchClause, OptionalMatchClause))
        for qualifier in clause.qualifiers
    ]
    if not qualifiers:
        return
    if any(qualifier.kind == "system" for qualifier in qualifiers) and not any(
        qualifier.kind == "valid" for qualifier in qualifiers
    ):
        raise _located_error(
            CypherSemanticError,
            "FOR SYSTEM_TIME requires FOR VALID_TIME in the same query",
            source,
        )
    write_types = (CreateClause, SetClause, RemoveClause, DeleteClause, MergeClause, ForeachClause)
    if any(
        isinstance(clause, write_types)
        for query in _walk_queries(value)
        for clause in query.clauses
    ):
        raise _located_error(
            CypherSemanticError,
            "Temporal-qualified queries are read-only",
            source,
        )


def _build_union_query(parsed: RawUnion, query: str) -> UnionQuery:
    """Build independently analyzed branch queries for a ``UNION``."""
    branches_raw: list[_ParsedMatch] = []
    flags: list[bool] = []
    for item in parsed[1]:
        if isinstance(item, bool):
            flags.append(item)
        else:
            branches_raw.append(item)
    branches = tuple(_build_canonical_query(branch, query) for branch in branches_raw)
    for branch in branches:
        if any(isinstance(clause, (CreateClause, SetClause, RemoveClause, DeleteClause, MergeClause, ForeachClause)) for clause in branch.clauses):
            raise _located_error(
                CypherSemanticError, "UNION branches must be read-only", query
            )
        analyze_query(branch)
    return UnionQuery(branches, tuple(flags), query, span=_query_span(query))


def _build_canonical_query(parsed: _ParsedMatch, query: str) -> Query:
    for patterns in parsed.pattern_groups:
        for pattern in patterns:
            _validate_pattern_literals(pattern, query)
    clauses: list[Clause] = []
    pending: PendingProjection = {}
    pending_span: SourceSpan | None = None
    pending_kind: str | None = None
    pending_value: object | None = None

    def projection_items(items: tuple[_Projection, ...]) -> tuple[ProjectionItem, ...]:
        return tuple(
            ProjectionItem(
                Wildcard(span=item.span) if item.expression == "*" else item.expression,
                item.alias,
                span=item.span,
            )
            for item in items
        )

    def close_projection() -> None:
        nonlocal pending, pending_span, pending_kind, pending_value
        if pending_kind is None:
            return
        items, distinct = cast("tuple[tuple[_Projection, ...], bool]", pending_value)
        span = _merge_spans(
            cast(SourceSpan, pending_span),
            *(part.span for part in pending["modifiers"]),
        )
        clause_type = WithClause if pending_kind == "with" else ReturnClause
        clauses.append(
            clause_type(
                items=projection_items(items),
                distinct=distinct,
                order_by=pending["order"],
                skip=pending["skip"],
                limit=pending["limit"],
                span=span,
            )
        )
        pending = {}
        pending_span = None
        pending_kind = None
        pending_value = None

    for item in parsed.sequence:
        if isinstance(item, _MatchPatterns):
            if pending_kind == "return":
                raise _located_error(CypherSemanticError, "RETURN must be the final clause", query)
            close_projection()
            if item.optional:
                clauses.append(OptionalMatchClause(item.values, span=item.span, selector=item.selector, qualifiers=item.qualifiers))
            else:
                clauses.append(MatchClause(item.values, span=item.span, selector=item.selector, qualifiers=item.qualifiers))
        elif isinstance(item, _ParsedPart) and item.kind == "where":
            # A WHERE following WITH belongs to that WITH stage, so the open
            # projection must be closed first to preserve textual order.
            close_projection()
            clauses.append(WhereClause(cast(Expression, item.value), span=item.span))
        elif isinstance(item, _ParsedPart) and item.kind == "unwind":
            close_projection()
            expression, variable = cast("tuple[Expression, str]", item.value)
            clauses.append(UnwindClause(expression, variable, span=item.span))
        elif isinstance(item, _ParsedPart) and item.kind == "subquery":
            close_projection()
            clauses.append(SubqueryClause(_build_canonical_query(cast(_ParsedMatch, item.value), query), span=item.span))
        elif isinstance(item, _ParsedPart) and item.kind == "create":
            if pending_kind == "return":
                raise _located_error(CypherSemanticError, "RETURN must be the final clause", query)
            close_projection()
            clauses.append(CreateClause(cast(tuple[PathPatternClause, ...], item.value), span=item.span))
        elif isinstance(item, _ParsedPart) and item.kind in ("set", "remove"):
            if pending_kind == "return":
                raise _located_error(CypherSemanticError, "RETURN must be the final clause", query)
            close_projection()
            if item.kind == "set":
                clauses.append(SetClause(cast(tuple[SetItem, ...], item.value), span=item.span))
            else:
                clauses.append(RemoveClause(cast(tuple[RemoveItem, ...], item.value), span=item.span))
        elif isinstance(item, _ParsedPart) and item.kind == "delete":
            if pending_kind == "return":
                raise _located_error(CypherSemanticError, "RETURN must be the final clause", query)
            close_projection()
            detach, expressions = cast("tuple[bool, tuple[Expression, ...]]", item.value)
            clauses.append(DeleteClause(expressions, detach, span=item.span))
        elif isinstance(item, _ParsedPart) and item.kind == "merge":
            if pending_kind == "return":
                raise _located_error(CypherSemanticError, "RETURN must be the final clause", query)
            close_projection()
            patterns, actions = cast("tuple[tuple[PathPatternClause, ...], list[_MergeAction]]", item.value)
            on_create = tuple(
                set_item for action in actions if action.kind == "create" for set_item in action.items
            )
            on_match = tuple(
                set_item for action in actions if action.kind == "match" for set_item in action.items
            )
            clauses.append(MergeClause(patterns, on_create, on_match, span=item.span))
        elif isinstance(item, _ParsedPart) and item.kind == "foreach":
            if pending_kind == "return":
                raise _located_error(CypherSemanticError, "RETURN must be the final clause", query)
            close_projection()
            variable, iterable, body = cast("tuple[str, Expression, tuple[object, ...]]", item.value)
            for sub in body:
                if isinstance(sub, _MatchPatterns):
                    for pattern in sub.values:
                        _validate_pattern_literals(pattern, query)
            inner = _build_canonical_query(
                _ParsedMatch(
                    (), None, (), False, (), None, None, (), None,
                    _ParsedPart("return", ((), False), item.span), (), tuple(body),
                ),
                query,
            )
            clauses.append(ForeachClause(variable, iterable, inner.clauses, span=item.span))
        elif isinstance(item, _ParsedPart) and item.kind in ("with", "return"):
            close_projection()
            pending_kind = item.kind
            pending_value = item.value
            pending_span = item.span
            pending = {"order": (), "skip": None, "limit": None, "modifiers": []}
        elif isinstance(item, _ParsedPart) and item.kind in ("order", "skip", "limit"):
            if pending_kind is None:
                raise _located_error(CypherSemanticError, "ORDER BY, SKIP, and LIMIT must follow WITH or RETURN", query)
            if item.kind == "order" and pending["order"] != ():
                raise _located_error(CypherSemanticError, "Duplicate ORDER BY clause", query)
            if item.kind in ("skip", "limit") and pending[cast(PaginationKey, item.kind)] is not None:
                raise _located_error(CypherSemanticError, f"Duplicate {item.kind.upper()} clause", query)
            pending["modifiers"].append(item)
            if item.kind == "order":
                pending["order"] = cast(tuple[OrderItem, ...], item.value)
            else:
                pending[cast(PaginationKey, item.kind)] = cast(Pagination, item.value)
        else:  # pragma: no cover - grammar builder invariant
            raise _located_error(CypherSemanticError, "Unsupported Cypher query", query)
    close_projection()
    return Query(tuple(clauses), query, span=_query_span(query))


def _parse_source(query: str) -> ParsedSource:
    try:
        parsed = _BUILDER.transform(_PARSER.parse(query))
    except UnexpectedInput as exc:
        hint = _gql_quantifier_hint(query)
        if hint is not None:
            message, offset = hint
            line = query.count("\n", 0, offset) + 1
            line_start = query.rfind("\n", 0, offset) + 1
            raise CypherSyntaxError(
                message,
                line=line,
                column=offset - line_start + 1,
                offset=offset,
                source=query,
            ) from exc
        raise CypherSyntaxError(
            "Unsupported Cypher query",
            line=exc.line,
            column=exc.column,
            offset=cast(int, exc.pos_in_stream),
            source=query,
        ) from exc
    except VisitError as exc:
        if isinstance(exc.orig_exc, (CypherSyntaxError, CypherSemanticError)):
            raise exc.orig_exc from exc
        raise _located_error(CypherSyntaxError, f"Invalid Cypher literal: {exc.orig_exc}", query) from exc
    except (SyntaxError, ValueError) as exc:
        if isinstance(exc, (CypherSyntaxError, CypherSemanticError)):
            raise
        raise _located_error(CypherSyntaxError, f"Invalid Cypher literal: {exc}", query) from exc

    return cast(ParsedSource, parsed)


def _build_sample_call(parsed: RawProcedure, query: str) -> SampleTypedPathsCall | EntailsCall:
    _, name, arguments, yields, returns, limit = parsed
    procedure = name.lower()
    if procedure not in {"pg.sample_typed_paths", "kg.entails"}:
        raise _located_error(CypherSemanticError, f"Unsupported procedure: {name}", query, name)
    allowed = {"path"} if procedure == "pg.sample_typed_paths" else {"status", "confidence", "explanation"}
    yielded_fields = [item[1].lower() for item in yields]
    if len(set(yielded_fields)) != len(yielded_fields):
        raise _located_error(CypherSemanticError, f"Procedure {name} yields a field more than once", query)
    unsupported = next((item[1] for item in yields if item[1].lower() not in allowed), None)
    if unsupported is not None:
        raise _located_error(
            CypherSemanticError,
            f"Procedure {name} does not yield field: {unsupported}",
            query,
            unsupported,
        )
    aliases = {item[2] for item in yields}
    if len(aliases) != len(yields):
        raise _located_error(
            CypherSemanticError, f"Procedure {name} yields an alias more than once", query
        )
    invalid_return = next((item for item in returns if item not in aliases), None)
    if invalid_return is not None:
        raise _located_error(
            CypherSemanticError,
            f"RETURN must reference a yielded variable: {invalid_return}",
            query,
            invalid_return,
        )
    if procedure == "kg.entails":
        if len(arguments) != 4:
            raise _located_error(
                CypherSemanticError,
                "kg.entails expects agent, proposition, mode, and options",
                query,
            )
        return EntailsCall(
            agent=arguments[0], proposition=arguments[1], mode=arguments[2], options=arguments[3],
            yields=tuple((item[1].lower(), item[2]) for item in yields),
            returns=tuple(returns), limit=limit,
        )
    if len(arguments) != 2:
        raise _located_error(CypherSemanticError, "pg.sample_typed_paths expects seed IDs and a sampling pattern", query)
    seed_ids, pattern = arguments
    if not isinstance(seed_ids, (list, Parameter)) or isinstance(seed_ids, list) and not all(isinstance(seed_id, str) for seed_id in seed_ids):
        raise _located_error(CypherSemanticError, "pg.sample_typed_paths seed IDs must be a list of strings", query)
    if not isinstance(pattern, (list, Parameter)) or isinstance(pattern, list) and not all(isinstance(hop, dict) for hop in pattern):
        raise _located_error(CypherSemanticError, "pg.sample_typed_paths pattern must be a list of dictionaries", query)
    return SampleTypedPathsCall(
        seed_ids=seed_ids,
        pattern=pattern,
        returns=tuple(returns),
        limit=limit,
        output_name=yields[0][2],
    )


def _gql_quantifier_hint(query: str) -> tuple[str, int] | None:
    """Recognize unsupported postfix GQL path quantifiers after parse failure."""
    relationship = re.search(r"(?:->|<-|-)\s*(\{\s*\d*\s*(?:,\s*\d*)?\s*\}|[+*?])\s*\(", query)
    if relationship is not None:
        return (
            "GQL quantified relationships are not supported; use legacy variable-length "
            "syntax such as -[:TYPE*1..3]->",
            relationship.start(1),
        )
    path = re.search(r"\)\s*(\{\s*\d*\s*(?:,\s*\d*)?\s*\}|[+*?])\s*(?:RETURN|,|\))", query, re.IGNORECASE)
    if path is not None and query.lstrip().upper().startswith("MATCH (("):
        return (
            "GQL quantified path patterns are not supported; use legacy *min..max syntax "
            "for one repeated relationship or expand the pattern explicitly",
            path.start(1),
        )
    return None


def _build_match_query(parsed: _ParsedMatch, query: str, analysis: QueryAnalysis) -> LegacyQuery:
    clauses: list[LegacyPattern] = []
    group_ids: list[int] = []
    for group_id, patterns in enumerate(parsed.pattern_groups):
        force_generalized = len(patterns) > 1
        for pattern in patterns:
            clauses.append(_build_clause(pattern, query, force_generalized=force_generalized))
            group_ids.append(group_id)
    resolved = analysis.clauses[-1].projections
    returns = tuple(item.output.name for item in resolved)
    projections = tuple(item.rendered_expression for item in resolved)
    projection_expressions = tuple(item.expression for item in resolved)

    common: MatchOptions = {
        "returns": returns,
        "where": parsed.where,
        "projections": projections,
        "projection_expressions": projection_expressions,
        "order_by": parsed.order_by,
        "skip": parsed.skip,
        "limit": parsed.limit,
        "distinct": parsed.distinct,
    }
    if len(clauses) > 1:
        return MultiMatchQuery(clauses=tuple(clauses), match_group_ids=tuple(group_ids), **common)

    clause = clauses[0]
    if isinstance(clause, NodePatternClause):
        return NodeScanQuery(
            variable=clause.variable,
            label=clause.label,
            property_name=clause.property_name,
            property_value=clause.property_value,
            labels=clause.labels,
            properties=clause.properties,
            **common,
        )
    if isinstance(clause, RelationshipPatternClause):
        return RelationshipScanQuery(
            source_var=clause.source_var,
            rel_var=clause.rel_var,
            edge_type=clause.edge_type,
            target_var=clause.target_var,
            direction=clause.direction,
            edge_types=clause.edge_types,
            **common,
        )
    if isinstance(clause, AnchoredPatternClause):
        return MatchQuery(source_var=clause.source_var, source_id=clause.source_id, hops=clause.hops, **common)
    if isinstance(clause, PathPatternClause):
        return MultiMatchQuery(clauses=tuple(clauses), match_group_ids=(0,), **common)
    raise _located_error(CypherSemanticError, "Unsupported Cypher query", query)


def _build_clause(pattern: _Pattern, query: str, *, force_generalized: bool = False) -> LegacyPattern:
    node = pattern.source
    if not pattern.hops and node.variable is not None and not force_generalized and pattern.name is None:
        first_name, first_value = node.properties[0] if node.properties else (None, None)
        return NodePatternClause(node.variable, node.labels[0] if node.labels else None, first_name, first_value, node.labels, node.properties)

    if (
        not force_generalized
        and pattern.name is None
        and len(pattern.hops) == 1
        and node.variable is not None
        and pattern.hops[0].target.variable is not None
        and pattern.hops[0].edge_types
        and not node.labels
        and not node.properties
        and not pattern.hops[0].target.labels
        and not pattern.hops[0].target.properties
        and not pattern.hops[0].properties
        and (pattern.hops[0].min_length, pattern.hops[0].max_length) == (1, 1)
    ):
        hop = pattern.hops[0]
        if hop.direction == "in":
            return RelationshipPatternClause(cast(str, hop.target.variable), hop.rel_var, hop.edge_types[0], cast(str, node.variable), "in", hop.edge_types)
        return RelationshipPatternClause(cast(str, node.variable), hop.rel_var, hop.edge_types[0], cast(str, hop.target.variable), hop.direction, hop.edge_types)

    properties = dict(node.properties)
    if (
        not force_generalized
        and pattern.name is None
        and node.variable is not None
        and not node.labels
        and set(properties) == {"id"}
        and isinstance(properties.get("id"), str)
        and all(hop.edge_types and hop.target.variable is not None and not hop.target.labels and not hop.target.properties and not hop.properties and (hop.min_length, hop.max_length) == (1, 1) for hop in pattern.hops)
    ):
        hops = tuple(TraversalHop(hop.rel_var, hop.edge_types[0], cast(str, hop.target.variable), hop.direction, hop.edge_types) for hop in pattern.hops)
        return AnchoredPatternClause(cast(str, node.variable), cast(str, properties["id"]), hops)
    return PathPatternClause(node, pattern.hops, pattern.name)


def _canonical_uses_functions(canonical: Query) -> bool:
    """Return whether any canonical clause projects or filters on a function call."""
    from .semantics import contains_function_call

    for clause in canonical.clauses:
        if isinstance(clause, WhereClause) and contains_function_call(clause.expression):
            return True
        if isinstance(clause, (WithClause, ReturnClause)):
            if any(contains_function_call(item.expression) for item in clause.items):
                return True
            if any(contains_function_call(item.expression_ast) for item in clause.order_by if item.expression_ast is not None):
                return True
    return False


def _canonical_uses_extended_expressions(canonical: Query) -> bool:
    """Return whether any canonical clause uses extended expression forms."""
    from .semantics import contains_extended_expression

    for clause in canonical.clauses:
        if isinstance(clause, WhereClause) and contains_extended_expression(clause.expression):
            return True
        if isinstance(clause, (WithClause, ReturnClause)):
            if any(contains_extended_expression(item.expression) for item in clause.items):
                return True
            if any(contains_extended_expression(item.expression_ast) for item in clause.order_by if item.expression_ast is not None):
                return True
    return False


def _validate_pattern_literals(pattern: _Pattern, query: str) -> None:
    for pattern_node in (pattern.source, *(hop.target for hop in pattern.hops)):
        for _, value in pattern_node.properties:
            if not _is_plain_value(value):
                raise _located_error(CypherSemanticError, "Invalid Cypher literal", query)
    for hop in pattern.hops:
        for _, value in hop.properties:
            if not _is_plain_value(value):
                raise _located_error(CypherSemanticError, "Invalid Cypher literal", query)


def _is_plain_value(value: object) -> bool:
    if isinstance(value, Parameter):
        return True
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, list):
        return all(_is_plain_value(item) for item in value)
    if isinstance(value, dict):
        return all(_is_plain_value(item) for item in value.values())
    return False


def _decode_string(text: str) -> str:
    try:
        value = ast.literal_eval(text)
    except (SyntaxError, ValueError) as exc:
        raise ValueError(text) from exc
    return cast(str, value)


def parse_literal(literal_text: str) -> object:
    """Parse a Cypher literal or parameter reference supported by this subset."""
    literal_text = literal_text.strip()
    parameter_match = _PARAMETER_RE.match(literal_text)
    if parameter_match is not None:
        return Parameter(parameter_match.group("name"))
    lowered = literal_text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    try:
        return ast.literal_eval(literal_text)
    except (SyntaxError, ValueError) as exc:
        raise ValueError(f"Invalid Cypher literal: {literal_text}") from exc


def split_top_level_args(args_text: str) -> list[str]:
    """Split comma-separated procedure arguments without splitting nested values."""
    parts = []
    start = 0
    depth = 0
    quote = None
    escape = False
    for index, char in enumerate(args_text):
        if quote is not None:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
        elif char in "[({":
            depth += 1
        elif char in "])}":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(args_text[start:index].strip())
            start = index + 1
    parts.append(args_text[start:].strip())
    return parts


def unsupported_query_error(query: str = "") -> CypherSyntaxError:
    """Return the legacy unsupported-query error with source metadata."""
    return _located_error(CypherSyntaxError, "Unsupported Cypher query", query)


def _located_error(error_type: type[ErrorT], message: str, query: str, needle: str | None = None) -> ErrorT:
    offset = query.find(needle) if needle else 0
    offset = max(offset, 0)
    line = query.count("\n", 0, offset) + 1
    line_start = query.rfind("\n", 0, offset) + 1
    return error_type(message, line=line, column=offset - line_start + 1, offset=offset, source=query)


def _source_span(meta: SourceMeta) -> SourceSpan:
    return SourceSpan(
        meta.start_pos,
        meta.end_pos,
        meta.line,
        meta.column,
        meta.end_line,
        meta.end_column,
    )


def _merge_spans(first: SourceSpan, *rest: SourceSpan) -> SourceSpan:
    last = rest[-1] if rest else first
    return SourceSpan(
        first.start_offset,
        last.end_offset,
        first.line,
        first.column,
        last.end_line,
        last.end_column,
    )


def _query_span(query: str) -> SourceSpan:
    lines = query.splitlines(keepends=True)
    if not lines:
        return SourceSpan(0, 0, 1, 1, 1, 1)
    final_line = lines[-1]
    if final_line.endswith(("\n", "\r")):
        end_line = len(lines) + 1
        end_column = 1
    else:
        end_line = len(lines)
        end_column = len(final_line) + 1
    return SourceSpan(0, len(query), 1, 1, end_line, end_column)

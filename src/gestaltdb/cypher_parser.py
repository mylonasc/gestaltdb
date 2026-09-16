"""Grammar-based parser for the GestaltDB read-only Cypher subset."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from lark import Lark, Token, Transformer, UnexpectedInput, v_args
from lark.exceptions import VisitError

from .cypher_ast import (
    AnchoredPatternClause,
    AndExpression,
    ArithmeticExpression,
    CaseExpression,
    ComparisonExpression,
    ExistsExpression,
    FunctionCall,
    InExpression,
    ListComprehension,
    ListExpression,
    MapExpression,
    MapProjectionExpression,
    MatchClause,
    MatchQuery,
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
    PatternHop,
    ProjectionItem,
    PropertyAccessExpression,
    PropertyRef,
    QuantifiedPredicate,
    Query,
    ReduceExpression,
    RelationshipPatternClause,
    RelationshipScanQuery,
    ReturnClause,
    SampleTypedPathsCall,
    SliceExpression,
    SourceSpan,
    StringPredicate,
    SubqueryClause,
    SubscriptExpression,
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
from .cypher_errors import CypherSemanticError, CypherSyntaxError
from .cypher_semantics import QueryAnalysis, analyze_query, render_projection

_GRAMMAR = r"""
 ?start: query ";"?
 ?query: match_query | sample_call | union_query
 union_query: match_query (union_operator match_query)+
 union_operator: "UNION"i "ALL"i -> union_all
               | "UNION"i -> union_distinct

  match_query: (match_clause | optional_match_clause | unwind_clause | call_subquery) (match_clause | optional_match_clause | where_clause | unwind_clause | call_subquery | with_section)* return_full
  match_clause: "MATCH"i pattern ("," pattern)*
  optional_match_clause: "OPTIONAL"i "MATCH"i pattern ("," pattern)*
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

sample_call: "CALL"i "pg"i "." "sample_typed_paths"i "(" call_arguments ")" "YIELD"i "path"i "RETURN"i "path"i limit_clause?
call_arguments: [expression ("," expression)*]

pattern: node_pattern traversal_hop*
node_pattern: "(" symbolic_name? labels? properties? ")"
labels: (":" symbolic_name)+
properties: "{" [property_pair ("," property_pair)*] "}"
property_pair: symbolic_name ":" expression
?traversal_hop: "-" relationship? "->" node_pattern -> out_hop
              | "<-" relationship? "-" node_pattern -> in_hop
              | "-" relationship? "-" node_pattern -> any_hop
relationship: "[" symbolic_name? rel_type_spec? "]"
rel_type_spec: ":" rel_types
rel_types: rel_name ("|" rel_name)*
rel_name: REL_NAME | BACKTICK_NAME

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
?multiplicative: unary (MUL_OP unary)* -> arithmetic_expression
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
 QUANTIFIER.2: /(ALL|ANY|NONE|SINGLE)/i
ORDER_DIRECTION.2: /ASC|DESC/i
COMP_OP: "=~" | "<>" | "!=" | "<=" | ">=" | "=" | "<" | ">"
ADD_OP: "+" | "-"
MUL_OP: "*" | "/" | "%"
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


@dataclass(frozen=True)
class _Properties:
    values: tuple[tuple[str, object], ...]


@dataclass(frozen=True)
class _RelationshipTypes:
    values: tuple[str, ...]


@dataclass(frozen=True)
class _MatchPatterns:
    values: tuple[_Pattern, ...]
    span: SourceSpan
    optional: bool = False


@dataclass(frozen=True)
class _Projection:
    expression: object
    alias: str | None = None
    span: SourceSpan | None = None


@dataclass(frozen=True)
class _CaseWhen:
    condition: object
    value: object


@dataclass(frozen=True)
class _CaseElse:
    value: object


@dataclass(frozen=True)
class _CompWhere:
    value: object


@dataclass(frozen=True)
class _CompYield:
    value: object


@dataclass(frozen=True)
class _ParsedPart:
    kind: str
    value: object
    span: SourceSpan


@dataclass(frozen=True)
class _ParsedMatch:
    pattern_groups: tuple[tuple[_Pattern, ...], ...]
    where: object | None
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


class _ASTBuilder(Transformer):
    def symbolic_name(self, children):
        text = str(children[0])
        return text[1:-1].replace("``", "`") if text.startswith("`") else text

    def rel_name(self, children):
        return self.symbolic_name(children)

    def string(self, children):
        return _decode_string(str(children[0]))

    def number(self, children):
        text = str(children[0])
        return float(text) if any(char in text for char in ".eE") else int(text)

    def integer(self, children):
        return int(children[0])

    def true(self, _children):
        return True

    def false(self, _children):
        return False

    def null(self, _children):
        return None

    def variable(self, children):
        return Variable(children[0])

    def property_ref(self, children):
        return PropertyRef(children[0], children[1])

    def property_access(self, children):
        return PropertyAccessExpression(children[0], children[1])

    def parameter(self, children):
        return Parameter(children[0])

    def list_literal(self, children):
        values = list(children)
        return values if all(_is_plain_value(value) for value in values) else ListExpression(tuple(values))

    def property_pair(self, children):
        return children[0], children[1]

    def map_literal(self, children):
        items = tuple(item for item in children if item is not None)
        return dict(items) if all(_is_plain_value(value) for _, value in items) else MapExpression(items)

    def map_pair(self, children):
        return children[0], children[1]

    def parenthesized(self, children):
        return children[0]

    def when_clause(self, children):
        return _CaseWhen(children[0], children[1])

    def else_clause(self, children):
        return _CaseElse(children[0])

    def case_simple(self, children):
        operand = children[0]
        rest = children[1:]
        return CaseExpression(
            tuple((item.condition, item.value) for item in rest if isinstance(item, _CaseWhen)),
            next((item.value for item in rest if isinstance(item, _CaseElse)), None),
            operand,
        )

    def case_generic(self, children):
        return CaseExpression(
            tuple((item.condition, item.value) for item in children if isinstance(item, _CaseWhen)),
            next((item.value for item in children if isinstance(item, _CaseElse)), None),
        )

    def comp_where(self, children):
        return _CompWhere(children[0])

    def comp_yield(self, children):
        return _CompYield(children[0])

    def list_comprehension(self, children):
        return ListComprehension(
            children[0],
            children[1],
            next((item.value for item in children[2:] if isinstance(item, _CompWhere)), None),
            next((item.value for item in children[2:] if isinstance(item, _CompYield)), None),
        )

    def quantified_predicate(self, children):
        return QuantifiedPredicate(str(children[0]).lower(), children[1], children[2], children[3])

    def exists_expression(self, children):
        return ExistsExpression(children[0])

    def proj_all(self, _children):
        return ("all",)

    def proj_property(self, children):
        return ("property", children[0])

    def proj_alias(self, children):
        return ("alias", children[0], children[1])

    def map_projection(self, children):
        return MapProjectionExpression(children[0], tuple(children[1:]))

    @v_args(meta=True)
    def reduce_call(self, meta, children):
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

    def index_suffix(self, children):
        return ("index", children[0])

    def slice_both(self, children):
        return ("slice", children[0], children[1])

    def slice_from(self, children):
        return ("slice", children[0], None)

    def slice_to(self, children):
        return ("slice", None, children[0])

    def postfix(self, children):
        node = children[0]
        for suffix in children[1:]:
            if suffix[0] == "index":
                node = SubscriptExpression(node, suffix[1])
            else:
                node = SliceExpression(node, suffix[1], suffix[2])
        return node

    @v_args(meta=True)
    def count_star(self, meta, children):
        span = _source_span(meta)
        return FunctionCall(str(children[0]).lower(), (Wildcard(span=span),), False, span=span)

    @v_args(meta=True)
    def function_call(self, meta, children):
        name = str(children[0]).lower()
        distinct = False
        arguments: list[object] = []
        for child in children[1:]:
            if isinstance(child, Token):
                distinct = True
            else:
                arguments.append(child)
        return FunctionCall(name, tuple(arguments), distinct, span=_source_span(meta))

    def arithmetic_expression(self, children):
        if len(children) == 1:
            return children[0]
        expression = children[0]
        for index in range(1, len(children), 2):
            expression = ArithmeticExpression(expression, str(children[index]), children[index + 1])
        return expression

    def unary_expression(self, children):
        return UnaryExpression(str(children[0]), children[1])

    def comparison_expression(self, children):
        return ComparisonExpression(children[0], str(children[1]), children[2])

    def in_expression(self, children):
        return InExpression(children[0], children[1])

    def is_null(self, children):
        return NullPredicate(children[0])

    def is_not_null(self, children):
        return NullPredicate(children[0], negated=True)

    def starts_with(self, children):
        return StringPredicate(children[0], "STARTS WITH", children[1])

    def ends_with(self, children):
        return StringPredicate(children[0], "ENDS WITH", children[1])

    def contains(self, children):
        return StringPredicate(children[0], "CONTAINS", children[1])

    def not_expression(self, children):
        return NotExpression(children[0])

    def and_expression(self, children):
        return children[0] if len(children) == 1 else AndExpression(tuple(children))

    def xor_expression(self, children):
        return children[0] if len(children) == 1 else XorExpression(tuple(children))

    def or_expression(self, children):
        return children[0] if len(children) == 1 else OrExpression(tuple(children))

    def labels(self, children):
        return _Labels(tuple(children))

    def properties(self, children):
        return _Properties(tuple(children))

    def node_pattern(self, children):
        variable = next((item for item in children if isinstance(item, str)), None)
        labels = next((item.values for item in children if isinstance(item, _Labels)), ())
        properties = next((item.values for item in children if isinstance(item, _Properties)), ())
        return _NodePattern(variable, labels, properties)

    def rel_types(self, children):
        return _RelationshipTypes(tuple(children))

    def rel_type_spec(self, children):
        return children[0]

    def relationship(self, children):
        rel_var = next((item for item in children if isinstance(item, str)), None)
        edge_types = next((item.values for item in children if isinstance(item, _RelationshipTypes)), ())
        return rel_var, edge_types

    def out_hop(self, children):
        relationship, node = (children if len(children) == 2 else ((None, ()), children[0]))
        return _Hop(relationship[0], relationship[1], node, "out")

    def in_hop(self, children):
        relationship, node = (children if len(children) == 2 else ((None, ()), children[0]))
        return _Hop(relationship[0], relationship[1], node, "in")

    def any_hop(self, children):
        relationship, node = (children if len(children) == 2 else ((None, ()), children[0]))
        return _Hop(relationship[0], relationship[1], node, "any")

    def pattern(self, children):
        return _Pattern(children[0], tuple(children[1:]))

    @v_args(meta=True)
    def match_clause(self, meta, children):
        return _MatchPatterns(tuple(children), _source_span(meta))

    @v_args(meta=True)
    def optional_match_clause(self, meta, children):
        return _MatchPatterns(tuple(children), _source_span(meta), True)

    @v_args(meta=True)
    def unwind_clause(self, meta, children):
        return _ParsedPart("unwind", (children[0], children[1]), _source_span(meta))

    @v_args(meta=True)
    def call_subquery(self, meta, children):
        return _ParsedPart("subquery", children[0], _source_span(meta))

    def union_all(self, _children):
        return True

    def union_distinct(self, _children):
        return False

    def union_query(self, children):
        return ("union", tuple(children))

    @v_args(meta=True)
    def where_clause(self, meta, children):
        return _ParsedPart("where", children[0], _source_span(meta))

    def star_projection(self, _children):
        return "*"

    @v_args(meta=True)
    def return_item(self, meta, children):
        return _Projection(
            children[0],
            children[1] if len(children) > 1 else None,
            _source_span(meta),
        )

    def return_items(self, children):
        return tuple(children)

    @v_args(meta=True)
    def return_clause(self, meta, children):
        distinct = bool(children and str(children[0]).upper() == "DISTINCT")
        return _ParsedPart("return", (children[-1], distinct), _source_span(meta))

    def order_item(self, children):
        expression = children[0]
        direction = str(children[1]).upper() if len(children) > 1 else "ASC"
        return OrderItem(render_projection(expression), direction == "DESC", expression)

    @v_args(meta=True)
    def order_clause(self, meta, children):
        return _ParsedPart("order", tuple(children), _source_span(meta))

    @v_args(meta=True)
    def skip_clause(self, meta, children):
        return _ParsedPart("skip", children[0], _source_span(meta))

    @v_args(meta=True)
    def limit_clause(self, meta, children):
        return _ParsedPart("limit", children[0], _source_span(meta))

    def call_arguments(self, children):
        return ("arguments", tuple(item for item in children if item is not None))

    @v_args(meta=True)
    def with_clause(self, meta, children):
        distinct = bool(children and str(children[0]).upper() == "DISTINCT")
        return _ParsedPart("with", (children[-1], distinct), _source_span(meta))

    def with_section(self, children):
        return tuple(children)

    def return_full(self, children):
        return tuple(children)

    def match_query(self, children):
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
        return_items, distinct = return_part.value
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
            where_part.value if where_part is not None else None,
            return_items,
            distinct,
            order_by,
            skip,
            limit,
            match_groups,
            where_part,
            return_part,
            modifier_parts,
            tuple(sequence),
        )

    def sample_call(self, children):
        limit = next(
            (
                item.value
                for item in children
                if isinstance(item, _ParsedPart) and item.kind == "limit"
            ),
            None,
        )
        arguments = next(item[1] for item in children if isinstance(item, tuple) and item and item[0] == "arguments")
        return ("sample", arguments, limit)


_BUILDER = _ASTBuilder()


def parse(query: str) -> MatchQuery | SampleTypedPathsCall | NodeScanQuery | RelationshipScanQuery | MultiMatchQuery:
    """Parse the supported Cypher subset into runtime-compatible AST objects."""
    parsed = _parse_source(query)

    if isinstance(parsed, tuple) and parsed and parsed[0] == "sample":
        return _build_sample_call(parsed, query)
    if isinstance(parsed, tuple) and parsed and parsed[0] == "union":
        raise _located_error(
            CypherSemanticError,
            "Query cannot be represented by legacy parse(); use parse_ast()",
            query,
        )
    canonical = _build_canonical_query(parsed, query)
    if any(isinstance(clause, (WithClause, OptionalMatchClause, UnwindClause, SubqueryClause)) for clause in canonical.clauses):
        raise _located_error(
            CypherSemanticError,
            "Query cannot be represented by legacy parse(); use parse_ast()",
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


def parse_ast(query: str) -> Query | SampleTypedPathsCall | UnionQuery:
    """Parse the supported Cypher subset into the canonical clause AST."""
    parsed = _parse_source(query)
    if isinstance(parsed, tuple) and parsed and parsed[0] == "sample":
        return _build_sample_call(parsed, query)
    if isinstance(parsed, tuple) and parsed and parsed[0] == "union":
        return _build_union_query(parsed, query)

    canonical = _build_canonical_query(parsed, query)
    analyze_query(canonical)
    return canonical


def _build_union_query(parsed, query: str) -> UnionQuery:
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
        analyze_query(branch)
    return UnionQuery(branches, tuple(flags), query, span=_query_span(query))


def _build_canonical_query(parsed: _ParsedMatch, query: str) -> Query:
    for patterns in parsed.pattern_groups:
        for pattern in patterns:
            _validate_pattern_literals(pattern, query)
    clauses: list[object] = []
    pending: dict[str, object] = {}
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
        items, distinct = pending_value
        span = _merge_spans(
            pending_span,
            *(part.span for part in pending["modifiers"]),
        )
        if pending_kind == "with":
            clauses.append(
                WithClause(
                    items=projection_items(items),
                    distinct=distinct,
                    order_by=pending["order"],
                    skip=pending["skip"],
                    limit=pending["limit"],
                    span=span,
                )
            )
        else:
            clauses.append(
                ReturnClause(
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
                clauses.append(OptionalMatchClause(item.values, span=item.span))
            else:
                clauses.append(MatchClause(item.values, span=item.span))
        elif isinstance(item, _ParsedPart) and item.kind == "where":
            # A WHERE following WITH belongs to that WITH stage, so the open
            # projection must be closed first to preserve textual order.
            close_projection()
            clauses.append(WhereClause(item.value, span=item.span))
        elif isinstance(item, _ParsedPart) and item.kind == "unwind":
            close_projection()
            expression, variable = item.value
            clauses.append(UnwindClause(expression, variable, span=item.span))
        elif isinstance(item, _ParsedPart) and item.kind == "subquery":
            close_projection()
            clauses.append(SubqueryClause(_build_canonical_query(item.value, query), span=item.span))
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
            if item.kind in ("skip", "limit") and pending[item.kind] is not None:
                raise _located_error(CypherSemanticError, f"Duplicate {item.kind.upper()} clause", query)
            pending["modifiers"].append(item)
            if item.kind == "order":
                pending["order"] = item.value
            else:
                pending[item.kind] = item.value
        else:  # pragma: no cover - grammar builder invariant
            raise _located_error(CypherSemanticError, "Unsupported Cypher query", query)
    close_projection()
    return Query(tuple(clauses), query, span=_query_span(query))


def _parse_source(query: str):
    try:
        parsed = _BUILDER.transform(_PARSER.parse(query))
    except UnexpectedInput as exc:
        raise CypherSyntaxError(
            "Unsupported Cypher query",
            line=exc.line,
            column=exc.column,
            offset=exc.pos_in_stream,
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

    return parsed


def _build_sample_call(parsed, query: str) -> SampleTypedPathsCall:
    _, arguments, limit = parsed
    if len(arguments) != 2:
        raise _located_error(CypherSemanticError, "pg.sample_typed_paths expects seed IDs and a sampling pattern", query)
    seed_ids, pattern = arguments
    if not isinstance(seed_ids, list) or not all(isinstance(seed_id, str) for seed_id in seed_ids):
        raise _located_error(CypherSemanticError, "pg.sample_typed_paths seed IDs must be a list of strings", query)
    if not isinstance(pattern, list) or not all(isinstance(hop, dict) for hop in pattern):
        raise _located_error(CypherSemanticError, "pg.sample_typed_paths pattern must be a list of dictionaries", query)
    return SampleTypedPathsCall(seed_ids=seed_ids, pattern=pattern, limit=limit)


def _build_match_query(parsed: _ParsedMatch, query: str, analysis: QueryAnalysis):
    clauses = []
    group_ids = []
    for group_id, patterns in enumerate(parsed.pattern_groups):
        force_generalized = len(patterns) > 1
        for pattern in patterns:
            clauses.append(_build_clause(pattern, query, force_generalized=force_generalized))
            group_ids.append(group_id)
    clauses = tuple(clauses)
    resolved = analysis.clauses[-1].projections
    returns = tuple(item.output.name for item in resolved)
    projections = tuple(item.rendered_expression for item in resolved)
    projection_expressions = tuple(item.expression for item in resolved)

    common = {
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
        return MultiMatchQuery(clauses=clauses, match_group_ids=tuple(group_ids), **common)

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
        return MultiMatchQuery(clauses=clauses, match_group_ids=(0,), **common)
    raise _located_error(CypherSemanticError, "Unsupported Cypher query", query)


def _build_clause(pattern: _Pattern, query: str, *, force_generalized: bool = False):
    node = pattern.source
    if not pattern.hops and node.variable is not None and not force_generalized:
        first_name, first_value = node.properties[0] if node.properties else (None, None)
        return NodePatternClause(node.variable, node.labels[0] if node.labels else None, first_name, first_value, node.labels, node.properties)

    if (
        not force_generalized
        and len(pattern.hops) == 1
        and node.variable is not None
        and pattern.hops[0].target.variable is not None
        and pattern.hops[0].edge_types
        and not node.labels
        and not node.properties
        and not pattern.hops[0].target.labels
        and not pattern.hops[0].target.properties
    ):
        hop = pattern.hops[0]
        if hop.direction == "in":
            source_var, target_var, direction = hop.target.variable, node.variable, "in"
        else:
            source_var, target_var, direction = node.variable, hop.target.variable, hop.direction
        return RelationshipPatternClause(source_var, hop.rel_var, hop.edge_types[0], target_var, direction, hop.edge_types)

    properties = dict(node.properties)
    if (
        not force_generalized
        and node.variable is not None
        and not node.labels
        and set(properties) == {"id"}
        and isinstance(properties.get("id"), str)
        and all(hop.edge_types and hop.target.variable is not None and not hop.target.labels and not hop.target.properties for hop in pattern.hops)
    ):
        hops = tuple(TraversalHop(hop.rel_var, hop.edge_types[0], hop.target.variable, hop.direction, hop.edge_types) for hop in pattern.hops)
        return AnchoredPatternClause(node.variable, properties["id"], hops)
    return PathPatternClause(node, pattern.hops)


def _canonical_uses_functions(canonical: Query) -> bool:
    """Return whether any canonical clause projects or filters on a function call."""
    from .cypher_semantics import contains_function_call

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
    from .cypher_semantics import contains_extended_expression

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


def _is_plain_value(value) -> bool:
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
    return value


def parse_literal(literal_text: str):
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


def _located_error(error_type, message: str, query: str, needle: str | None = None):
    offset = query.find(needle) if needle else 0
    offset = max(offset, 0)
    line = query.count("\n", 0, offset) + 1
    line_start = query.rfind("\n", 0, offset) + 1
    return error_type(message, line=line, column=offset - line_start + 1, offset=offset, source=query)


def _source_span(meta) -> SourceSpan:
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

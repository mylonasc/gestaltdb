"""Grammar-based parser for the GestaltDB read-only Cypher subset."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from lark import Lark, Transformer, UnexpectedInput
from lark.exceptions import VisitError

from .cypher_ast import (
    AnchoredPatternClause,
    AndExpression,
    ArithmeticExpression,
    ComparisonExpression,
    InExpression,
    ListExpression,
    MapExpression,
    MatchQuery,
    MultiMatchQuery,
    NodePattern,
    NodePatternClause,
    NodeScanQuery,
    NotExpression,
    NullPredicate,
    OrderItem,
    OrExpression,
    Parameter,
    PathPatternClause,
    PatternHop,
    PropertyRef,
    RelationshipPatternClause,
    RelationshipScanQuery,
    SampleTypedPathsCall,
    StringPredicate,
    TraversalHop,
    UnaryExpression,
    Variable,
    XorExpression,
)


class CypherSyntaxError(ValueError):
    """A Cypher syntax error with a source position."""

    def __init__(self, message: str, *, line: int, column: int, offset: int, source: str):
        super().__init__(f"{message} (line {line}, column {column})")
        self.message = message
        self.line = line
        self.column = column
        self.offset = offset
        self.source = source


class CypherSemanticError(ValueError):
    """A semantically invalid Cypher query with a source position."""

    def __init__(self, message: str, *, line: int, column: int, offset: int, source: str):
        super().__init__(f"{message} (line {line}, column {column})")
        self.message = message
        self.line = line
        self.column = column
        self.offset = offset
        self.source = source


_GRAMMAR = r"""
?start: query ";"?
?query: match_query | sample_call

match_query: match_clause+ where_clause? return_clause order_clause? skip_clause? limit_clause?
match_clause: "MATCH"i pattern ("," pattern)*
where_clause: "WHERE"i expression
return_clause: "RETURN"i DISTINCT? return_items
return_items: return_item ("," return_item)*
return_item: projection ("AS"i symbolic_name)?
?projection: STAR -> star_projection
           | property_ref
           | variable
order_clause: "ORDER"i "BY"i order_item ("," order_item)*
order_item: projection ORDER_DIRECTION?
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
      | atom
?atom: property_ref
     | parameter
     | literal
     | variable
     | list_literal
     | map_literal
     | "(" expression ")" -> parenthesized
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
ORDER_DIRECTION.2: /ASC|DESC/i
COMP_OP: "=~" | "<>" | "!=" | "<=" | ">=" | "=" | "<" | ">"
ADD_OP: "+" | "-"
MUL_OP: "*" | "/" | "%"
STAR: "*"
INTEGER: /[0-9]+/
NUMBER: /(?:[0-9]+\.[0-9]*|\.[0-9]+|[0-9]+)(?:[eE][+-]?[0-9]+)?/
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


@dataclass(frozen=True)
class _Projection:
    expression: object
    alias: str | None = None


@dataclass(frozen=True)
class _ParsedMatch:
    pattern_groups: tuple[tuple[_Pattern, ...], ...]
    where: object | None
    projections: tuple[_Projection, ...]
    distinct: bool
    order_by: tuple[OrderItem, ...]
    skip: int | Parameter | None
    limit: int | Parameter | None


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

    def match_clause(self, children):
        return _MatchPatterns(tuple(children))

    def where_clause(self, children):
        return ("where", children[0])

    def star_projection(self, _children):
        return "*"

    def return_item(self, children):
        return _Projection(children[0], children[1] if len(children) > 1 else None)

    def return_items(self, children):
        return tuple(children)

    def return_clause(self, children):
        distinct = bool(children and str(children[0]).upper() == "DISTINCT")
        return ("return", children[-1], distinct)

    def order_item(self, children):
        expression = children[0]
        direction = str(children[1]).upper() if len(children) > 1 else "ASC"
        return OrderItem(_render_projection(expression), direction == "DESC", expression)

    def order_clause(self, children):
        return ("order", tuple(children))

    def skip_clause(self, children):
        return ("skip", children[0])

    def limit_clause(self, children):
        return ("limit", children[0])

    def call_arguments(self, children):
        return ("arguments", tuple(item for item in children if item is not None))

    def match_query(self, children):
        pattern_groups = tuple(item.values for item in children if isinstance(item, _MatchPatterns))
        where = next((item[1] for item in children if isinstance(item, tuple) and item and item[0] == "where"), None)
        return_data = next(item for item in children if isinstance(item, tuple) and item and item[0] == "return")
        order_by = next((item[1] for item in children if isinstance(item, tuple) and item and item[0] == "order"), ())
        skip = next((item[1] for item in children if isinstance(item, tuple) and item and item[0] == "skip"), None)
        limit = next((item[1] for item in children if isinstance(item, tuple) and item and item[0] == "limit"), None)
        return _ParsedMatch(pattern_groups, where, return_data[1], return_data[2], order_by, skip, limit)

    def sample_call(self, children):
        limit = next((item[1] for item in children if isinstance(item, tuple) and item and item[0] == "limit"), None)
        arguments = next(item[1] for item in children if isinstance(item, tuple) and item and item[0] == "arguments")
        return ("sample", arguments, limit)


_BUILDER = _ASTBuilder()


def parse(query: str) -> MatchQuery | SampleTypedPathsCall | NodeScanQuery | RelationshipScanQuery | MultiMatchQuery:
    """Parse the supported Cypher subset into runtime-compatible AST objects."""
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

    if isinstance(parsed, tuple) and parsed and parsed[0] == "sample":
        return _build_sample_call(parsed, query)
    return _build_match_query(parsed, query)


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


def _build_match_query(parsed: _ParsedMatch, query: str):
    clauses = []
    group_ids = []
    for group_id, patterns in enumerate(parsed.pattern_groups):
        force_generalized = len(patterns) > 1
        for pattern in patterns:
            clauses.append(_build_clause(pattern, query, force_generalized=force_generalized))
            group_ids.append(group_id)
    clauses = tuple(clauses)
    _validate_variable_kinds(clauses, query)
    ordered_variables = _bound_variables_ordered(clauses)
    bound_variables = set(ordered_variables)
    _validate_expression_variables(parsed.where, bound_variables, query, "WHERE")
    returns, projections, projection_expressions = _build_projections(parsed.projections, ordered_variables, bound_variables, query)
    if len(set(returns)) != len(returns):
        raise _located_error(CypherSemanticError, "RETURN contains duplicate column names", query)
    order_variables = bound_variables.union(returns)
    for item in parsed.order_by:
        _validate_expression_variables(item.expression_ast, order_variables, query, "ORDER BY")

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
    for pattern_node in (node, *(hop.target for hop in pattern.hops)):
        for _, value in pattern_node.properties:
            if not _is_plain_value(value):
                raise _located_error(CypherSemanticError, "Invalid Cypher literal", query)
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


def _build_projections(items, ordered_variables, bound_variables, query):
    if len(items) == 1 and items[0].expression == "*":
        if not ordered_variables:
            raise _located_error(CypherSemanticError, "RETURN * requires bound variables", query)
        expressions = tuple(Variable(name) for name in ordered_variables)
        return ordered_variables, ordered_variables, expressions
    returns = []
    projections = []
    expressions = []
    for item in items:
        expression = item.expression
        _validate_expression_variables(expression, bound_variables, query, "RETURN")
        rendered = _render_projection(expression)
        projections.append(rendered)
        returns.append(item.alias or rendered)
        expressions.append(expression)
    return tuple(returns), tuple(projections), tuple(expressions)


def _validate_expression_variables(expression, bound_variables: set[str], query: str, clause: str) -> None:
    for variable in _expression_variables(expression):
        if variable not in bound_variables:
            message = f"{clause} references unbound variable: {variable}"
            raise _located_error(CypherSemanticError, message, query, variable)


def _expression_variables(expression):
    if expression is None or isinstance(expression, (str, int, float, bool, Parameter)):
        return ()
    if isinstance(expression, Variable):
        return (expression.name,)
    if isinstance(expression, PropertyRef):
        return (expression.variable,)
    if isinstance(expression, (ComparisonExpression, ArithmeticExpression, StringPredicate)):
        return _expression_variables(expression.left) + _expression_variables(expression.right)
    if isinstance(expression, InExpression):
        return _expression_variables(expression.left) + _expression_variables(expression.values)
    if isinstance(expression, NullPredicate):
        return _expression_variables(expression.expression)
    if isinstance(expression, (NotExpression, UnaryExpression)):
        return _expression_variables(expression.expression)
    if isinstance(expression, (AndExpression, OrExpression, XorExpression)):
        return tuple(variable for item in expression.expressions for variable in _expression_variables(item))
    if isinstance(expression, ListExpression):
        return tuple(variable for item in expression.items for variable in _expression_variables(item))
    if isinstance(expression, MapExpression):
        return tuple(variable for _, item in expression.items for variable in _expression_variables(item))
    if isinstance(expression, list):
        return tuple(variable for item in expression for variable in _expression_variables(item))
    if isinstance(expression, dict):
        return tuple(variable for item in expression.values() for variable in _expression_variables(item))
    return ()


def _bound_variables_ordered(clauses) -> tuple[str, ...]:
    variables = []

    def add(name):
        if name is not None and name not in variables:
            variables.append(name)

    for clause in clauses:
        if isinstance(clause, NodePatternClause):
            add(clause.variable)
        elif isinstance(clause, RelationshipPatternClause):
            first_var = clause.target_var if clause.direction == "in" else clause.source_var
            second_var = clause.source_var if clause.direction == "in" else clause.target_var
            add(first_var)
            add(clause.rel_var)
            add(second_var)
        elif isinstance(clause, AnchoredPatternClause):
            add(clause.source_var)
            for hop in clause.hops:
                add(hop.rel_var)
                add(hop.target_var)
        elif isinstance(clause, PathPatternClause):
            add(clause.source.variable)
            for hop in clause.hops:
                add(hop.rel_var)
                add(hop.target.variable)
    return tuple(variables)


def _validate_variable_kinds(clauses, query: str) -> None:
    kinds: dict[str, str] = {}

    def add(name, kind):
        if name is None:
            return
        previous = kinds.setdefault(name, kind)
        if previous != kind:
            raise _located_error(
                CypherSemanticError,
                f"Variable {name} cannot be used as both a node and a relationship",
                query,
                name,
            )

    for clause in clauses:
        if isinstance(clause, NodePatternClause):
            add(clause.variable, "node")
        elif isinstance(clause, RelationshipPatternClause):
            add(clause.source_var, "node")
            add(clause.rel_var, "relationship")
            add(clause.target_var, "node")
        elif isinstance(clause, AnchoredPatternClause):
            add(clause.source_var, "node")
            for hop in clause.hops:
                add(hop.rel_var, "relationship")
                add(hop.target_var, "node")
        elif isinstance(clause, PathPatternClause):
            add(clause.source.variable, "node")
            for hop in clause.hops:
                add(hop.rel_var, "relationship")
                add(hop.target.variable, "node")


def _render_projection(expression) -> str:
    if expression == "*":
        return "*"
    if isinstance(expression, Variable):
        return expression.name
    if isinstance(expression, PropertyRef):
        return f"{expression.variable}.{expression.property_name}"
    raise ValueError("Only variables and property references can be projected or ordered")


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

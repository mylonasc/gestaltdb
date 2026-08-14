"""Parser for the current GestaltDB Cypher subset.

This module intentionally keeps the accepted language small while separating
query syntax from execution. It is the migration point for a fuller Cypher
frontend in later phases.
"""

from __future__ import annotations

import ast
import re

from .cypher_ast import AndExpression, AnchoredPatternClause, ComparisonExpression, InExpression, MatchQuery, MultiMatchQuery, NodePatternClause, NodeScanQuery, NullPredicate, OrderItem, Parameter, PropertyRef, RelationshipPatternClause, RelationshipScanQuery, SampleTypedPathsCall, TraversalHop


_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_PARAMETER_RE = re.compile(rf"^\$(?P<name>{_IDENTIFIER})$")
_RETURN_ITEM = rf"(?:\*|{_IDENTIFIER}(?:\.{_IDENTIFIER})?)"
_RETURN_ALIAS_ITEM = rf"{_RETURN_ITEM}(?:\s+AS\s+{_IDENTIFIER})?"
_RETURN_ITEMS = rf"{_RETURN_ALIAS_ITEM}(?:\s*,\s*{_RETURN_ALIAS_ITEM})*"
_ORDER_ITEM = rf"{_IDENTIFIER}(?:\.{_IDENTIFIER})?(?:\s+(?:ASC|DESC))?"
_ORDER_ITEMS = rf"{_ORDER_ITEM}(?:\s*,\s*{_ORDER_ITEM})*"
_ANCHOR_RE = re.compile(
    rf"^\s*\((?P<source_var>{_IDENTIFIER})\s*"
    rf"\{{\s*id\s*:\s*(?P<quote>['\"])(?P<source_id>.*?)(?P=quote)\s*\}}\)"
)
_OUT_HOP_RE = re.compile(
    rf"^\s*-\s*\[(?:(?P<rel_var>{_IDENTIFIER})\s*)?:(?P<edge_type>[^\]\s]+)\]\s*->\s*"
    rf"\((?P<target_var>{_IDENTIFIER})\)"
)
_IN_HOP_RE = re.compile(
    rf"^\s*<-\s*\[(?:(?P<rel_var>{_IDENTIFIER})\s*)?:(?P<edge_type>[^\]\s]+)\]\s*-\s*"
    rf"\((?P<target_var>{_IDENTIFIER})\)"
)
_ANY_HOP_RE = re.compile(
    rf"^\s*-\s*\[(?:(?P<rel_var>{_IDENTIFIER})\s*)?:(?P<edge_type>[^\]\s]+)\]\s*-\s*"
    rf"\((?P<target_var>{_IDENTIFIER})\)"
)
_RETURN_RE = re.compile(
    rf"^\s*(?:WHERE\s+(?P<where>.*?)\s+)?RETURN\s+(?:(?P<distinct>DISTINCT)\s+)?(?P<returns>{_RETURN_ITEMS})"
    rf"(?:\s+ORDER\s+BY\s+(?P<order_by>{_ORDER_ITEMS}))?(?:\s+SKIP\s+(?P<skip>\d+))?(?:\s+LIMIT\s+(?P<limit>\d+))?\s*;?\s*$",
    re.IGNORECASE | re.DOTALL,
)
_NODE_SCAN_RE = re.compile(
    rf"^\s*MATCH\s+\((?P<var>{_IDENTIFIER})(?P<labels>(?::{_IDENTIFIER})*)(?:\s*\{{\s*(?P<property>{_IDENTIFIER})\s*:\s*(?P<value>.*?)\s*\}})?\)\s+"
    rf"(?:WHERE\s+(?P<where>.*?)\s+)?"
    rf"RETURN\s+(?:(?P<distinct>DISTINCT)\s+)?(?P<returns>{_RETURN_ITEMS})"
    rf"(?:\s+ORDER\s+BY\s+(?P<order_by>{_ORDER_ITEMS}))?(?:\s+SKIP\s+(?P<skip>\d+))?(?:\s+LIMIT\s+(?P<limit>\d+))?\s*;?\s*$",
    re.IGNORECASE | re.DOTALL,
)
_NODE_PATTERN_RE = re.compile(
    rf"^\s*\((?P<var>{_IDENTIFIER})(?P<labels>(?::{_IDENTIFIER})*)(?:\s*\{{\s*(?P<property>{_IDENTIFIER})\s*:\s*(?P<value>.*?)\s*\}})?\)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_REL_PATTERN_OUT_RE = re.compile(
    rf"^\s*\((?P<source_var>{_IDENTIFIER})\)\s*-\s*\[(?:(?P<rel_var>{_IDENTIFIER})\s*)?:(?P<edge_type>[^\]\s]+)\]\s*->\s*\((?P<target_var>{_IDENTIFIER})\)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_REL_PATTERN_IN_RE = re.compile(
    rf"^\s*\((?P<target_var>{_IDENTIFIER})\)\s*<-\s*\[(?:(?P<rel_var>{_IDENTIFIER})\s*)?:(?P<edge_type>[^\]\s]+)\]\s*-\s*\((?P<source_var>{_IDENTIFIER})\)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_REL_SCAN_OUT_RE = re.compile(
    rf"^\s*MATCH\s+\((?P<source_var>{_IDENTIFIER})\)\s*-\s*\[(?:(?P<rel_var>{_IDENTIFIER})\s*)?:(?P<edge_type>[^\]\s]+)\]\s*->\s*\((?P<target_var>{_IDENTIFIER})\)\s+"
    rf"(?:WHERE\s+(?P<where>.*?)\s+)?RETURN\s+(?:(?P<distinct>DISTINCT)\s+)?(?P<returns>{_RETURN_ITEMS})"
    rf"(?:\s+ORDER\s+BY\s+(?P<order_by>{_ORDER_ITEMS}))?(?:\s+SKIP\s+(?P<skip>\d+))?(?:\s+LIMIT\s+(?P<limit>\d+))?\s*;?\s*$",
    re.IGNORECASE | re.DOTALL,
)
_REL_SCAN_IN_RE = re.compile(
    rf"^\s*MATCH\s+\((?P<target_var>{_IDENTIFIER})\)\s*<-\s*\[(?:(?P<rel_var>{_IDENTIFIER})\s*)?:(?P<edge_type>[^\]\s]+)\]\s*-\s*\((?P<source_var>{_IDENTIFIER})\)\s+"
    rf"(?:WHERE\s+(?P<where>.*?)\s+)?RETURN\s+(?:(?P<distinct>DISTINCT)\s+)?(?P<returns>{_RETURN_ITEMS})"
    rf"(?:\s+ORDER\s+BY\s+(?P<order_by>{_ORDER_ITEMS}))?(?:\s+SKIP\s+(?P<skip>\d+))?(?:\s+LIMIT\s+(?P<limit>\d+))?\s*;?\s*$",
    re.IGNORECASE | re.DOTALL,
)
_COMPARISON_RE = re.compile(
    rf"^\s*(?P<variable>{_IDENTIFIER})\.(?P<property>{_IDENTIFIER})\s*"
    rf"(?P<operator>=|<>|!=|<=|>=|<|>)\s*(?P<value>.*?)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_IN_RE = re.compile(
    rf"^\s*(?P<variable>{_IDENTIFIER})\.(?P<property>{_IDENTIFIER})\s+IN\s+(?P<value>.*?)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_NULL_RE = re.compile(
    rf"^\s*(?P<variable>{_IDENTIFIER})\.(?P<property>{_IDENTIFIER})\s+IS\s+(?P<negated>NOT\s+)?NULL\s*$",
    re.IGNORECASE | re.DOTALL,
)
_CALL_SAMPLE_RE = re.compile(
    r"^\s*CALL\s+pg\.sample_typed_paths\s*\((?P<args>.*)\)\s+"
    r"YIELD\s+path\s+RETURN\s+path(?:\s+LIMIT\s+(?P<limit>\d+))?\s*;?\s*$",
    re.IGNORECASE | re.DOTALL,
)


def parse(query: str) -> MatchQuery | SampleTypedPathsCall | NodeScanQuery | RelationshipScanQuery | MultiMatchQuery:
    """Parse the supported Cypher subset into AST objects."""
    stripped = query.strip()
    if re.match(r"^CALL\s+pg\.sample_typed_paths\b", stripped, re.IGNORECASE):
        return _parse_sample_typed_paths(stripped)
    multi_match = _parse_multi_match(stripped)
    if multi_match is not None:
        return multi_match
    node_scan = _parse_node_scan(stripped)
    if node_scan is not None:
        return node_scan
    rel_scan = _parse_relationship_scan(stripped)
    if rel_scan is not None:
        return rel_scan
    return _parse_match(stripped)


def _parse_multi_match(query: str) -> MultiMatchQuery | None:
    if len(re.findall(r"\bMATCH\b", query, re.IGNORECASE)) < 2:
        return None
    split_at = _find_return_suffix_start(query)
    if split_at is None:
        raise unsupported_query_error()
    match_text = query[:split_at]
    return_match = _RETURN_RE.match(query[split_at:])
    if return_match is None:
        raise unsupported_query_error()
    pattern_texts = [part.strip() for part in re.split(r"\bMATCH\b", match_text, flags=re.IGNORECASE) if part.strip()]
    clauses = tuple(_parse_match_clause_pattern(pattern_text) for pattern_text in pattern_texts)
    ordered_variables = _multi_match_bound_variables_ordered(clauses)
    bound_variables = set(ordered_variables)
    where = None
    if return_match.group("where") is not None:
        where = _parse_where_expression(return_match.group("where"), bound_variables)
    returns, projections = _parse_returns(return_match.group("returns"), ordered_variables)
    unknown = [name for name in _return_variables(projections) if name not in bound_variables]
    if unknown:
        raise ValueError(f"RETURN references unbound variable(s): {', '.join(unknown)}")
    return MultiMatchQuery(
        clauses=clauses,
        returns=returns,
        where=where,
        projections=projections,
        order_by=_parse_order_by(return_match.group("order_by")),
        skip=_parse_limit(return_match.group("skip")),
        limit=_parse_limit(return_match.group("limit")),
        distinct=return_match.group("distinct") is not None,
    )


def _find_return_suffix_start(query: str) -> int | None:
    for match in re.finditer(r"\b(?:WHERE|RETURN)\b", query, re.IGNORECASE):
        if _RETURN_RE.match(query[match.start():]) is not None:
            return match.start()
    return None


def _parse_match_clause_pattern(pattern_text: str):
    node_match = _NODE_PATTERN_RE.match(pattern_text)
    if node_match is not None:
        labels = tuple(part for part in node_match.group("labels").split(":") if part)
        property_value = None
        if node_match.group("property") is not None:
            property_value = parse_literal(node_match.group("value"))
        return NodePatternClause(
            variable=node_match.group("var"),
            label=labels[0] if labels else None,
            property_name=node_match.group("property"),
            property_value=property_value,
            labels=labels,
        )
    relationship_match = _REL_PATTERN_OUT_RE.match(pattern_text)
    direction = "out"
    if relationship_match is None:
        relationship_match = _REL_PATTERN_IN_RE.match(pattern_text)
        direction = "in"
    if relationship_match is not None:
        edge_types = tuple(edge_type for edge_type in relationship_match.group("edge_type").split("|") if edge_type)
        return RelationshipPatternClause(
            source_var=relationship_match.group("source_var"),
            rel_var=relationship_match.group("rel_var"),
            edge_type=edge_types[0],
            target_var=relationship_match.group("target_var"),
            direction=direction,
            edge_types=edge_types,
        )
    anchor_match = _ANCHOR_RE.match(pattern_text)
    if anchor_match is not None:
        remainder = pattern_text[anchor_match.end():]
        hops = []
        while True:
            hop_match, direction = _match_hop(remainder)
            if hop_match is None or direction is None:
                break
            edge_types = tuple(edge_type for edge_type in hop_match.group("edge_type").split("|") if edge_type)
            hops.append(TraversalHop(hop_match.group("rel_var"), edge_types[0], hop_match.group("target_var"), direction, edge_types))
            remainder = remainder[hop_match.end():]
        if hops and not remainder.strip():
            return AnchoredPatternClause(anchor_match.group("source_var"), anchor_match.group("source_id"), tuple(hops))
    raise unsupported_query_error()


def _parse_node_scan(query: str) -> NodeScanQuery | None:
    match = _NODE_SCAN_RE.match(query)
    if match is None:
        return None
    variable = match.group("var")
    returns, projections = _parse_returns(match.group("returns"), (variable,))
    unknown = [name for name in _return_variables(projections) if name != variable]
    if unknown:
        raise ValueError(f"RETURN references unbound variable(s): {', '.join(unknown)}")
    labels = tuple(part for part in match.group("labels").split(":") if part)
    property_value = None
    if match.group("property") is not None:
        property_value = parse_literal(match.group("value"))
    where = None
    if match.group("where") is not None:
        where = _parse_where_expression(match.group("where"), {variable})
    return NodeScanQuery(
        variable=variable,
        label=labels[0] if labels else None,
        property_name=match.group("property"),
        property_value=property_value,
        returns=returns,
        limit=_parse_limit(match.group("limit")),
        where=where,
        labels=labels,
        projections=projections,
        order_by=_parse_order_by(match.group("order_by")),
        skip=_parse_limit(match.group("skip")),
        distinct=match.group("distinct") is not None,
    )


def _parse_match(query: str) -> MatchQuery:
    if not query.upper().startswith("MATCH "):
        raise unsupported_query_error()

    remainder = query[5:]
    anchor_match = _ANCHOR_RE.match(remainder)
    if anchor_match is None:
        raise unsupported_query_error()

    source_var = anchor_match.group("source_var")
    source_id = anchor_match.group("source_id")
    remainder = remainder[anchor_match.end():]
    hops = []
    while True:
        hop_match, direction = _match_hop(remainder)
        if hop_match is None or direction is None:
            break
        edge_types = tuple(edge_type for edge_type in hop_match.group("edge_type").split("|") if edge_type)
        hops.append(
            TraversalHop(
                rel_var=hop_match.group("rel_var"),
                edge_type=edge_types[0],
                target_var=hop_match.group("target_var"),
                direction=direction,
                edge_types=edge_types,
            )
        )
        remainder = remainder[hop_match.end():]

    if not hops:
        raise unsupported_query_error()
    return_match = _RETURN_RE.match(remainder)
    if return_match is None:
        raise unsupported_query_error()

    bound_variables = _match_bound_variables(source_var, hops)
    where = None
    if return_match.group("where") is not None:
        where = _parse_where_expression(return_match.group("where"), bound_variables)

    returns, projections = _parse_returns(return_match.group("returns"), _match_bound_variables_ordered(source_var, hops))
    parsed = MatchQuery(
        source_var=source_var,
        source_id=source_id,
        hops=tuple(hops),
        returns=returns,
        limit=_parse_limit(return_match.group("limit")),
        where=where,
        projections=projections,
        order_by=_parse_order_by(return_match.group("order_by")),
        skip=_parse_limit(return_match.group("skip")),
        distinct=return_match.group("distinct") is not None,
    )
    _validate_match_returns(parsed)
    return parsed


def _parse_relationship_scan(query: str) -> RelationshipScanQuery | None:
    match = _REL_SCAN_OUT_RE.match(query)
    direction = "out"
    if match is None:
        match = _REL_SCAN_IN_RE.match(query)
        direction = "in"
    if match is None:
        return None
    edge_types = tuple(edge_type for edge_type in match.group("edge_type").split("|") if edge_type)
    bound_variables = {match.group("source_var"), match.group("target_var")}
    if match.group("rel_var") is not None:
        bound_variables.add(match.group("rel_var"))
    where = None
    if match.group("where") is not None:
        where = _parse_where_expression(match.group("where"), bound_variables)
    ordered_variables = _relationship_bound_variables_ordered(match)
    returns, projections = _parse_returns(match.group("returns"), ordered_variables)
    unknown = [name for name in _return_variables(projections) if name not in set(ordered_variables)]
    if unknown:
        raise ValueError(f"RETURN references unbound variable(s): {', '.join(unknown)}")
    return RelationshipScanQuery(
        source_var=match.group("source_var"),
        rel_var=match.group("rel_var"),
        edge_type=edge_types[0],
        target_var=match.group("target_var"),
        returns=returns,
        direction=direction,
        edge_types=edge_types,
        where=where,
        projections=projections,
        order_by=_parse_order_by(match.group("order_by")),
        skip=_parse_limit(match.group("skip")),
        limit=_parse_limit(match.group("limit")),
        distinct=match.group("distinct") is not None,
    )


def _parse_sample_typed_paths(query: str) -> SampleTypedPathsCall:
    match = _CALL_SAMPLE_RE.match(query)
    if match is None:
        raise unsupported_query_error()
    seed_ids, pattern = _parse_call_arguments(match.group("args"))
    if not isinstance(seed_ids, list) or not all(isinstance(seed_id, str) for seed_id in seed_ids):
        raise ValueError("pg.sample_typed_paths seed IDs must be a list of strings")
    if not isinstance(pattern, list) or not all(isinstance(hop, dict) for hop in pattern):
        raise ValueError("pg.sample_typed_paths pattern must be a list of dictionaries")
    return SampleTypedPathsCall(seed_ids=seed_ids, pattern=pattern, limit=_parse_limit(match.groupdict().get("limit")))


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


def _parse_where_expression(expression_text: str, bound_variables: set[str]):
    parts = _split_top_level_keyword(expression_text, "AND")
    if len(parts) > 1:
        return AndExpression(tuple(_parse_comparison(part, bound_variables) for part in parts))
    return _parse_comparison(expression_text, bound_variables)


def _parse_comparison(expression_text: str, bound_variables: set[str]) -> ComparisonExpression:
    null_match = _NULL_RE.match(expression_text)
    if null_match is not None:
        variable = null_match.group("variable")
        if variable not in bound_variables:
            raise ValueError(f"WHERE references unbound variable: {variable}")
        return NullPredicate(
            expression=PropertyRef(variable=variable, property_name=null_match.group("property")),
            negated=null_match.group("negated") is not None,
        )
    in_match = _IN_RE.match(expression_text)
    if in_match is not None:
        variable = in_match.group("variable")
        if variable not in bound_variables:
            raise ValueError(f"WHERE references unbound variable: {variable}")
        return InExpression(
            left=PropertyRef(variable=variable, property_name=in_match.group("property")),
            values=parse_literal(in_match.group("value")),
        )
    match = _COMPARISON_RE.match(expression_text)
    if match is None:
        raise ValueError(f"Unsupported WHERE expression: {expression_text}")
    variable = match.group("variable")
    if variable not in bound_variables:
        raise ValueError(f"WHERE references unbound variable: {variable}")
    return ComparisonExpression(
        left=PropertyRef(variable=variable, property_name=match.group("property")),
        operator=match.group("operator"),
        right=parse_literal(match.group("value")),
    )


def _parse_returns(return_text: str, bound_variables: tuple[str, ...] = ()) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if return_text.strip() == "*":
        if not bound_variables:
            raise ValueError("RETURN * requires bound variables")
        return bound_variables, bound_variables
    columns = []
    projections = []
    for part in return_text.split(","):
        projection, column = _parse_return_item(part.strip())
        projections.append(projection)
        columns.append(column)
    return tuple(columns), tuple(projections)


def _parse_return_item(return_item: str) -> tuple[str, str]:
    alias_match = re.match(rf"^(?P<projection>{_RETURN_ITEM})\s+AS\s+(?P<alias>{_IDENTIFIER})$", return_item, re.IGNORECASE)
    if alias_match is not None:
        return alias_match.group("projection"), alias_match.group("alias")
    return return_item, return_item


def _parse_order_by(order_by_text: str | None) -> tuple[OrderItem, ...]:
    if order_by_text is None:
        return ()
    items = []
    for part in order_by_text.split(","):
        pieces = part.strip().split()
        expression = pieces[0]
        descending = len(pieces) > 1 and pieces[1].upper() == "DESC"
        items.append(OrderItem(expression=expression, descending=descending))
    return tuple(items)


def _parse_limit(limit_text: str | None) -> int | None:
    if limit_text is None:
        return None
    limit = int(limit_text)
    if limit < 0:
        raise ValueError("LIMIT must be non-negative")
    return limit


def _match_hop(remainder: str):
    for pattern, direction in (
        (_IN_HOP_RE, "in"),
        (_OUT_HOP_RE, "out"),
        (_ANY_HOP_RE, "any"),
    ):
        match = pattern.match(remainder)
        if match is not None:
            return match, direction
    return None, None


def _validate_match_returns(parsed: MatchQuery) -> None:
    bound_variables = _match_bound_variables(parsed.source_var, parsed.hops)
    unknown = [name for name in _return_variables(parsed.projections or parsed.returns) if name not in bound_variables]
    if unknown:
        raise ValueError(f"RETURN references unbound variable(s): {', '.join(unknown)}")


def _match_bound_variables(source_var: str, hops: list[TraversalHop] | tuple[TraversalHop, ...]) -> set[str]:
    bound_variables = {source_var}
    for hop in hops:
        bound_variables.add(hop.target_var)
        if hop.rel_var is not None:
            bound_variables.add(hop.rel_var)
    return bound_variables


def _match_bound_variables_ordered(source_var: str, hops: list[TraversalHop] | tuple[TraversalHop, ...]) -> tuple[str, ...]:
    variables = [source_var]
    for hop in hops:
        if hop.rel_var is not None and hop.rel_var not in variables:
            variables.append(hop.rel_var)
        if hop.target_var not in variables:
            variables.append(hop.target_var)
    return tuple(variables)


def _relationship_bound_variables_ordered(match) -> tuple[str, ...]:
    variables = [match.group("source_var")]
    if match.group("rel_var") is not None:
        variables.append(match.group("rel_var"))
    variables.append(match.group("target_var"))
    return tuple(variables)


def _multi_match_bound_variables_ordered(clauses: tuple[object, ...]) -> tuple[str, ...]:
    variables = []

    def add(variable):
        if variable is not None and variable not in variables:
            variables.append(variable)

    for clause in clauses:
        if isinstance(clause, NodePatternClause):
            add(clause.variable)
        elif isinstance(clause, RelationshipPatternClause):
            add(clause.source_var)
            add(clause.rel_var)
            add(clause.target_var)
        elif isinstance(clause, AnchoredPatternClause):
            add(clause.source_var)
            for hop in clause.hops:
                add(hop.rel_var)
                add(hop.target_var)
    return tuple(variables)


def _return_variables(returns: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(item.split(".", 1)[0] for item in returns)


def _parse_call_arguments(args_text: str):
    parts = split_top_level_args(args_text)
    if len(parts) != 2:
        raise ValueError("pg.sample_typed_paths expects seed IDs and a sampling pattern")
    return ast.literal_eval(parts[0]), ast.literal_eval(parts[1])


def split_top_level_args(args_text: str) -> list[str]:
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
            continue
        if char in "[({":
            depth += 1
            continue
        if char in "])}":
            depth -= 1
            continue
        if char == "," and depth == 0:
            parts.append(args_text[start:index].strip())
            start = index + 1
    parts.append(args_text[start:].strip())
    return parts


def _split_top_level_keyword(text: str, keyword: str) -> list[str]:
    parts = []
    start = 0
    quote = None
    escape = False
    pattern = re.compile(rf"\b{re.escape(keyword)}\b", re.IGNORECASE)
    index = 0
    while index < len(text):
        char = text[index]
        if quote is not None:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in {'"', "'"}:
            quote = char
            index += 1
            continue
        match = pattern.match(text, index)
        if match is not None:
            parts.append(text[start:index].strip())
            start = match.end()
            index = match.end()
            continue
        index += 1
    parts.append(text[start:].strip())
    return parts


def unsupported_query_error() -> ValueError:
    return ValueError(
        "Unsupported Cypher query. Supported subset: "
        'MATCH (a {id: "node-id"})-[:TYPE1]->(b)<-[:TYPE2]-(c) RETURN a, b, c; '
        'MATCH (n:Label) RETURN n; '
        'CALL pg.sample_typed_paths(["node-id"], [{"edge_type": "TYPE", "sample_size": 2}]) YIELD path RETURN path'
    )

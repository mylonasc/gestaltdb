"""Expression evaluation for the GestaltDB Cypher engine.

This module owns the entire expression language: scalar operators with Cypher
null propagation and three-valued boolean logic, plus the extended forms
(``CASE``, subscripts/slices, comprehensions, ``reduce``, map projections,
quantified predicates, ``exists``). The runtime pipeline evaluates row
expressions exclusively through :func:`evaluate_expression`.
"""

from __future__ import annotations

import re

from .cypher_ast import (
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
    NotExpression,
    NullPredicate,
    OrExpression,
    Parameter,
    PathValue,
    PropertyAccessExpression,
    PropertyRef,
    QuantifiedPredicate,
    ReduceExpression,
    ShortestPathExpression,
    SliceExpression,
    StringPredicate,
    SubscriptExpression,
    UnaryExpression,
    Variable,
    Wildcard,
    XorExpression,
)
from .cypher_functions import get_function_def


def evaluate_expression(expression, bindings: dict[str, object], context) -> object:
    """Evaluate an expression using Cypher null propagation and boolean logic."""
    if isinstance(expression, Parameter):
        return context.resolve(expression)
    if isinstance(expression, Variable):
        return bindings[expression.name]
    if isinstance(expression, PropertyRef):
        return project_value(bindings, f"{expression.variable}.{expression.property_name}")
    if isinstance(expression, PropertyAccessExpression):
        base = evaluate_expression(expression.expression, bindings, context)
        if base is None:
            return None
        return project_value({"base": base}, f"base.{expression.property_name}")
    if isinstance(expression, ListExpression):
        return [evaluate_expression(item, bindings, context) for item in expression.items]
    if isinstance(expression, MapExpression):
        return {key: evaluate_expression(value, bindings, context) for key, value in expression.items}
    if isinstance(expression, list):
        return [evaluate_expression(item, bindings, context) for item in expression]
    if isinstance(expression, dict):
        return {key: evaluate_expression(value, bindings, context) for key, value in expression.items()}
    if isinstance(expression, FunctionCall):
        return evaluate_function_call(expression, bindings, context)
    if isinstance(expression, CaseExpression):
        return _evaluate_case(expression, bindings, context)
    if isinstance(expression, (SubscriptExpression, SliceExpression)):
        return _evaluate_subscript(expression, bindings, context)
    if isinstance(expression, ListComprehension):
        return _evaluate_comprehension(expression, bindings, context)
    if isinstance(expression, ReduceExpression):
        return _evaluate_reduce(expression, bindings, context)
    if isinstance(expression, MapProjectionExpression):
        return _evaluate_map_projection(expression, bindings, context)
    if isinstance(expression, QuantifiedPredicate):
        return _evaluate_quantified(expression, bindings, context)
    if isinstance(expression, ExistsExpression):
        return evaluate_expression(expression.expression, bindings, context) is not None
    if isinstance(expression, ShortestPathExpression):
        return _evaluate_shortest_path(expression, bindings, context)
    if isinstance(expression, NotExpression):
        value = _boolean_value(evaluate_expression(expression.expression, bindings, context))
        return None if value is None else not value
    if isinstance(expression, AndExpression):
        result: bool | None = True
        for part in expression.expressions:
            value = _boolean_value(evaluate_expression(part, bindings, context))
            if value is False:
                return False
            if value is None:
                result = None
        return result
    if isinstance(expression, OrExpression):
        result = False
        for part in expression.expressions:
            value = _boolean_value(evaluate_expression(part, bindings, context))
            if value is True:
                return True
            if value is None:
                result = None
        return result
    if isinstance(expression, XorExpression):
        values = [_boolean_value(evaluate_expression(part, bindings, context)) for part in expression.expressions]
        if any(value is None for value in values):
            return None
        return sum(value is True for value in values) % 2 == 1
    if isinstance(expression, InExpression):
        left_value = evaluate_expression(expression.left, bindings, context)
        values = evaluate_expression(expression.values, bindings, context)
        if values is None:
            return None
        if not isinstance(values, (list, tuple)):
            raise TypeError("IN expects a list value")
        saw_null = left_value is None
        for value in values:
            equal = _cypher_equals(left_value, value)
            if equal is True:
                return True
            saw_null = saw_null or equal is None
        return None if saw_null else False
    if isinstance(expression, NullPredicate):
        value = evaluate_expression(expression.expression, bindings, context)
        return value is not None if expression.negated else value is None
    if isinstance(expression, StringPredicate):
        left_value = evaluate_expression(expression.left, bindings, context)
        right_value = evaluate_expression(expression.right, bindings, context)
        if left_value is None or right_value is None:
            return None
        if not isinstance(left_value, str) or not isinstance(right_value, str):
            raise TypeError(f"{expression.operator} expects string operands")
        if expression.operator == "STARTS WITH":
            return left_value.startswith(right_value)
        if expression.operator == "ENDS WITH":
            return left_value.endswith(right_value)
        return right_value in left_value
    if isinstance(expression, UnaryExpression):
        value = evaluate_expression(expression.expression, bindings, context)
        if value is None:
            return None
        _require_number(value, expression.operator)
        return value if expression.operator == "+" else -value
    if isinstance(expression, ArithmeticExpression):
        left_value = evaluate_expression(expression.left, bindings, context)
        right_value = evaluate_expression(expression.right, bindings, context)
        if left_value is None or right_value is None:
            return None
        if expression.operator == "+" and (isinstance(left_value, str) or isinstance(right_value, str)):
            if not isinstance(left_value, str) or not isinstance(right_value, str):
                raise TypeError("+ expects two strings or two numbers")
            return left_value + right_value
        _require_number(left_value, expression.operator)
        _require_number(right_value, expression.operator)
        operations = {
            "+": lambda: left_value + right_value,
            "-": lambda: left_value - right_value,
            "*": lambda: left_value * right_value,
            "/": lambda: left_value / right_value,
            "%": lambda: left_value % right_value,
        }
        return operations[expression.operator]()
    if isinstance(expression, ComparisonExpression):
        left_value = evaluate_expression(expression.left, bindings, context)
        right_value = evaluate_expression(expression.right, bindings, context)
        operator = expression.operator
        if operator == "=":
            return _cypher_equals(left_value, right_value)
        if operator in {"!=", "<>"}:
            equal = _cypher_equals(left_value, right_value)
            return None if equal is None else not equal
        if left_value is None or right_value is None:
            return None
        if operator == "=~":
            if not isinstance(left_value, str) or not isinstance(right_value, str):
                raise TypeError("=~ expects string operands")
            return re.fullmatch(right_value, left_value) is not None
        try:
            return {
                "<": lambda: left_value < right_value,
                "<=": lambda: left_value <= right_value,
                ">": lambda: left_value > right_value,
                ">=": lambda: left_value >= right_value,
            }[operator]()
        except TypeError as exc:
            raise TypeError(f"Cannot compare {type(left_value).__name__} and {type(right_value).__name__}") from exc
    return expression


def evaluate_function_call(call: FunctionCall, bindings: dict[str, object], context) -> object:
    """Evaluate a scalar function call against validated row bindings."""
    definition = get_function_def(call.name)
    if definition is None or definition.is_aggregate:
        raise TypeError(f"Cannot evaluate function: {call.name}")
    args = []
    for argument in call.arguments:
        if isinstance(argument, Wildcard):
            raise TypeError(f"{call.name}() does not accept *")
        args.append(evaluate_expression(argument, bindings, context))
    return definition.execute(args, context)


def _evaluate_case(expression: CaseExpression, bindings: dict[str, object], context) -> object:
    """Evaluate a ``CASE`` expression lazily, first match wins."""
    if expression.operand is None:
        for condition, value in expression.whens:
            if evaluate_expression(condition, bindings, context) is True:
                return evaluate_expression(value, bindings, context)
    else:
        operand = evaluate_expression(expression.operand, bindings, context)
        for compared, value in expression.whens:
            if _cypher_equals(operand, evaluate_expression(compared, bindings, context)) is True:
                return evaluate_expression(value, bindings, context)
    if expression.else_value is None:
        return None
    return evaluate_expression(expression.else_value, bindings, context)


def _evaluate_subscript(expression: SubscriptExpression | SliceExpression, bindings: dict[str, object], context) -> object:
    """Evaluate element access (``xs[i]``, ``m[k]``, ``n[$p]``) and slices."""
    target = evaluate_expression(expression.target, bindings, context)
    if target is None:
        return None
    if isinstance(expression, SliceExpression):
        return _apply_slice(target, expression, bindings, context)
    index = evaluate_expression(expression.index, bindings, context)
    if index is None:
        return None
    if isinstance(target, (list, tuple)):
        if isinstance(index, bool) or not isinstance(index, int):
            raise TypeError("List index must be an integer")
        if index < 0 or index >= len(target):
            return None
        return target[index]
    if isinstance(target, dict):
        if not isinstance(index, str):
            raise TypeError("Map key must be a string")
        return target.get(index)
    properties = getattr(target, "properties", None)
    if isinstance(properties, dict):
        if not isinstance(index, str):
            raise TypeError("Node property name must be a string")
        return properties.get(index)
    raise TypeError(f"Cannot subscript value of type {type(target).__name__}")


def _apply_slice(target: object, expression: SliceExpression, bindings: dict[str, object], context) -> object:
    """Evaluate list slicing with clamped bounds and null propagation."""
    if not isinstance(target, (list, tuple)):
        raise TypeError("Slice expects a list value")
    start = evaluate_expression(expression.start, bindings, context) if expression.start is not None else 0
    end = evaluate_expression(expression.end, bindings, context) if expression.end is not None else len(target)
    if start is None or end is None:
        return None
    if isinstance(start, bool) or not isinstance(start, int):
        raise TypeError("Slice bounds must be integers")
    if isinstance(end, bool) or not isinstance(end, int):
        raise TypeError("Slice bounds must be integers")
    return list(target[max(start, 0):max(min(end, len(target)), 0)])


def _evaluate_comprehension(expression: ListComprehension, bindings: dict[str, object], context) -> object:
    """Evaluate ``[x IN xs WHERE p | f(x)]`` with a shadowing loop variable."""
    items = evaluate_expression(expression.iterable, bindings, context)
    if items is None:
        return None
    if not isinstance(items, (list, tuple)):
        raise TypeError("Comprehension expects a list value")
    result = []
    for item in items:
        scoped = dict(bindings)
        scoped[expression.variable] = item
        if expression.where is not None:
            if evaluate_expression(expression.where, scoped, context) is not True:
                continue
        if expression.projection is None:
            result.append(item)
        else:
            result.append(evaluate_expression(expression.projection, scoped, context))
    return result


def _evaluate_reduce(expression: ReduceExpression, bindings: dict[str, object], context) -> object:
    """Evaluate ``reduce(acc = init, x IN xs | step)`` left to right."""
    items = evaluate_expression(expression.iterable, bindings, context)
    if items is None:
        return None
    if not isinstance(items, (list, tuple)):
        raise TypeError("Reduce expects a list value")
    accumulator = evaluate_expression(expression.initial, bindings, context)
    for item in items:
        scoped = dict(bindings)
        scoped[expression.accumulator] = accumulator
        scoped[expression.variable] = item
        accumulator = evaluate_expression(expression.expression, scoped, context)
    return accumulator


def _evaluate_map_projection(expression: MapProjectionExpression, bindings: dict[str, object], context) -> object:
    """Evaluate ``n{.*, .prop, key: value}`` over a node or map value."""
    base = bindings[expression.variable]
    if base is None:
        return None
    if isinstance(base, dict):
        entries: dict[str, object] = dict(base)
    elif isinstance(getattr(base, "properties", None), dict):
        entries = base.properties
    else:
        raise TypeError("Map projection expects a map or node value")
    projected: dict[str, object] = {}
    for item in expression.items:
        kind = item[0]
        if kind == "all":
            projected.update(entries)
        elif kind == "property":
            projected[item[1]] = entries.get(item[1])
        else:
            _, key, value_expression = item
            projected[key] = evaluate_expression(value_expression, bindings, context)
    return projected


def _evaluate_quantified(expression: QuantifiedPredicate, bindings: dict[str, object], context) -> object:
    """Evaluate ``all/any/none/single(x IN xs WHERE p)`` with null logic."""
    items = evaluate_expression(expression.iterable, bindings, context)
    if items is None:
        return None
    if not isinstance(items, (list, tuple)):
        raise TypeError(f"{expression.function} expects a list value")
    function = expression.function
    if function == "all":
        result: bool | None = True
        for item in items:
            value = _quantified_match(expression, item, bindings, context)
            if value is False:
                return False
            if value is None:
                result = None
        return result
    if function == "any":
        result = False
        for item in items:
            value = _quantified_match(expression, item, bindings, context)
            if value is True:
                return True
            if value is None:
                result = None
        return result
    if function == "none":
        result = _evaluate_quantified(
            QuantifiedPredicate("any", expression.variable, expression.iterable, expression.where),
            bindings,
            context,
        )
        return None if result is None else not result
    truths = 0
    saw_null = False
    for item in items:
        value = _quantified_match(expression, item, bindings, context)
        if value is True:
            truths += 1
            if truths > 1:
                return False
        elif value is None:
            saw_null = True
    if truths == 1:
        return None if saw_null else True
    return None if saw_null else False


def _quantified_match(expression: QuantifiedPredicate, item: object, bindings: dict[str, object], context) -> bool | None:
    """Evaluate one quantified-predicate element against its ``WHERE``."""
    scoped = dict(bindings)
    scoped[expression.variable] = item
    value = evaluate_expression(expression.where, scoped, context)
    if value is None:
        return None
    return value is True


def _evaluate_shortest_path(expression: ShortestPathExpression, bindings: dict[str, object], context) -> object:
    """Evaluate ``shortestPath``/``allShortestPaths`` between bound endpoints."""
    from .cypher_runtime import _is_node, _shortest_between

    pattern = expression.pattern
    hop = pattern.hops[0]
    start = bindings[pattern.source.variable]
    end = bindings[hop.target.variable]
    if start is None or end is None:
        return None if not expression.all_paths else []
    if not _is_node(start) or not _is_node(end):
        raise TypeError("shortestPath() endpoints must be nodes")
    return _shortest_between(context, pattern.source, start, hop, end, expression.all_paths)


def project_value(bindings: dict[str, object], return_item: str):
    """Project one return item from variable bindings."""
    variable, _, property_name = return_item.partition(".")
    value = bindings[variable]
    if not property_name:
        return value
    if isinstance(value, dict):
        return value.get(property_name)
    if property_name == "id" and hasattr(value, "get_id"):
        return value.get_id
    if property_name == "labels" and hasattr(value, "labels"):
        return value.labels
    if property_name in {"source", "target"} and hasattr(value, property_name):
        return getattr(value, property_name)
    properties = getattr(value, "properties", {})
    if property_name in properties:
        return properties[property_name]
    return None


def _cypher_equals(left, right):
    if left is None or right is None:
        return None
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        if len(left) != len(right):
            return False
        comparisons = [_cypher_equals(left_item, right_item) for left_item, right_item in zip(left, right)]
        if False in comparisons:
            return False
        return None if None in comparisons else True
    if isinstance(left, dict) and isinstance(right, dict):
        if left.keys() != right.keys():
            return False
        comparisons = [_cypher_equals(left[key], right[key]) for key in left]
        if False in comparisons:
            return False
        return None if None in comparisons else True
    return left == right


def _boolean_value(value):
    if value is None or isinstance(value, bool):
        return value
    raise TypeError(f"Expected boolean expression, got {type(value).__name__}")


def _require_number(value, operator: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{operator} expects numeric operands")

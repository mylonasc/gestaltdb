"""Scalar and aggregate function registry for the GestaltDB Cypher engine.

Every supported function is declared exactly once as a :class:`FunctionDef`
with its normalized name, allowed arities, kind, and implementation. Semantic
validation consults the registry for name/arity/``DISTINCT`` checks; the
expression evaluator dispatches scalar calls through it. Aggregate functions
are executed per-group by ``AggregateOperator`` instead.
"""

from __future__ import annotations

import math
import random
import uuid
from dataclasses import dataclass
from functools import partial
from typing import Callable

from .cypher_ast import PathValue


@dataclass(frozen=True, slots=True)
class FunctionDef:
    """One supported Cypher function and how to validate and run it."""

    name: str
    arity: tuple[int, int | None]
    kind: str
    execute: Callable[[list[object], object], object]

    @property
    def is_aggregate(self) -> bool:
        """Return whether this function aggregates rows per group."""
        return self.kind == "aggregate"


AGGREGATE_FUNCTIONS = ("count", "collect", "sum", "avg", "min", "max")


def _scalar(name: str, arity: tuple[int, int | None], execute: Callable[[list[object], object], object]) -> FunctionDef:
    """Declare a scalar function with ``(min_args, max_args)`` arity."""
    return FunctionDef(name, arity, "scalar", execute)


def _expect_type(value: object, name: str, *types: type) -> None:
    """Raise a ``TypeError`` when a function argument has the wrong type."""
    if not isinstance(value, types) or isinstance(value, bool) and bool not in types:
        expected = " or ".join(kind.__name__ for kind in types)
        raise TypeError(f"{name} expects {expected} operands")


def _is_entity(value: object) -> bool:
    """Return whether a value quacks like a graph entity."""
    return hasattr(value, "get_id") and hasattr(value, "properties")


def _coalesce(args: list[object], context) -> object:
    for value in args:
        if value is not None:
            return value
    return None


def _entity_id(args: list[object], context) -> object:
    (value,) = args
    if value is None:
        return None
    if not _is_entity(value):
        raise TypeError("id() expects a node or relationship")
    return value.get_id


def _relationship_type(args: list[object], context) -> object:
    (value,) = args
    if value is None:
        return None
    properties = getattr(value, "properties", None)
    if not isinstance(properties, dict) or hasattr(value, "labels"):
        raise TypeError("type() expects a relationship")
    return properties.get("type")


def _node_labels(args: list[object], context) -> object:
    (value,) = args
    if value is None:
        return None
    labels = getattr(value, "labels", None)
    if not isinstance(labels, (list, tuple)):
        raise TypeError("labels() expects a node")
    return list(labels)


def _endpoint(args: list[object], context, *, name: str, attribute: str) -> object:
    (value,) = args
    if value is None:
        return None
    if not _is_entity(value) or hasattr(value, "labels"):
        raise TypeError(f"{name}() expects a relationship")
    endpoint = getattr(value, attribute, None)
    if endpoint is None:
        return None
    node = context.graph.get_node(context.node_key_to_bytes(endpoint))
    return node


def _start_node(args: list[object], context) -> object:
    return _endpoint(args, context, name="startNode", attribute="source")


def _end_node(args: list[object], context) -> object:
    return _endpoint(args, context, name="endNode", attribute="target")


def _properties(args: list[object], context) -> object:
    (value,) = args
    if value is None:
        return None
    if isinstance(value, dict):
        return dict(value)
    properties = getattr(value, "properties", None)
    if isinstance(properties, dict):
        return dict(properties)
    raise TypeError("properties() expects a map, node, or relationship")


def _head(args: list[object], context) -> object:
    (value,) = args
    if value is None:
        return None
    _expect_type(value, "head()", list, tuple)
    return value[0] if value else None


def _last(args: list[object], context) -> object:
    (value,) = args
    if value is None:
        return None
    _expect_type(value, "last()", list, tuple)
    return value[-1] if value else None


def _size(args: list[object], context) -> object:
    (value,) = args
    if value is None:
        return None
    if isinstance(value, (list, tuple, str, dict)):
        return len(value)
    raise TypeError("size() expects a list, map, or string")


def _length(args: list[object], context) -> object:
    (value,) = args
    if value is None:
        return None
    if isinstance(value, PathValue):
        return len(value.edges)
    if isinstance(value, (list, tuple, str)):
        return len(value)
    raise TypeError("length() expects a list, string, or path")


def _path_elements(args: list[object], context, *, name: str) -> object:
    (value,) = args
    if value is None:
        return None
    if not isinstance(value, PathValue):
        raise TypeError(f"{name}() expects a path")
    return list(value.nodes if name == "nodes" else value.edges)


def _to_boolean(args: list[object], context) -> object:
    (value,) = args
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        return None
    return None


def _to_integer(args: list[object], context) -> object:
    (value,) = args
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _to_float(args: list[object], context) -> object:
    (value,) = args
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _to_string(args: list[object], context) -> object:
    (value,) = args
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, str)):
        return str(value)
    raise TypeError("toString() expects a boolean, number, or string")


def _trim(args: list[object], context) -> object:
    (value,) = args
    if value is None:
        return None
    _expect_type(value, "trim()", str)
    return value.strip()


def _ltrim(args: list[object], context) -> object:
    (value,) = args
    if value is None:
        return None
    _expect_type(value, "lTrim()", str)
    return value.lstrip()


def _rtrim(args: list[object], context) -> object:
    (value,) = args
    if value is None:
        return None
    _expect_type(value, "rTrim()", str)
    return value.rstrip()


def _case_fold(name: str, fold) -> Callable[[list[object], object], object]:
    def convert(args: list[object], context) -> object:
        (value,) = args
        if value is None:
            return None
        _expect_type(value, f"{name}()", str)
        return fold(value)

    return convert


def _replace(args: list[object], context) -> object:
    original, search, replacement = args
    if original is None or search is None or replacement is None:
        return None
    _expect_type(original, "replace()", str)
    _expect_type(search, "replace()", str)
    _expect_type(replacement, "replace()", str)
    return original.replace(search, replacement)


def _split(args: list[object], context) -> object:
    value, delimiter = args
    if value is None or delimiter is None:
        return None
    _expect_type(value, "split()", str)
    _expect_type(delimiter, "split()", str)
    if delimiter == "":
        return list(value)
    return value.split(delimiter)


def _substring(args: list[object], context) -> object:
    value = args[0]
    start = args[1]
    length = args[2] if len(args) > 2 else 0
    if value is None or start is None or length is None:
        return None
    _expect_type(value, "substring()", str)
    _expect_type(start, "substring()", int)
    if len(args) > 2:
        _expect_type(length, "substring()", int)
        return value[max(start, 0):max(start, 0) + max(length, 0)]
    return value[max(start, 0):]


def _side(name: str, take_left: bool) -> Callable[[list[object], object], object]:
    def convert(args: list[object], context) -> object:
        value, count = args
        if value is None or count is None:
            return None
        _expect_type(value, f"{name}()", str)
        _expect_type(count, f"{name}()", int)
        clamped = max(count, 0)
        if take_left:
            return value[:clamped]
        return value[max(len(value) - clamped, 0):]

    return convert


def _numeric_unary(name: str, apply) -> Callable[[list[object], object], object]:
    def convert(args: list[object], context) -> object:
        (value,) = args
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{name}() expects numeric operands")
        return apply(value)

    return convert


def _round(args: list[object], context) -> object:
    value = args[0]
    precision = args[1] if len(args) > 1 else 0
    if value is None or precision is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("round() expects numeric operands")
    if isinstance(precision, bool) or not isinstance(precision, int):
        raise TypeError("round() expects an integer precision")
    factor = 10**precision
    if value >= 0:
        rounded = math.floor(value * factor + 0.5) / factor
    else:
        rounded = math.ceil(value * factor - 0.5) / factor
    return int(rounded) if len(args) == 1 else rounded


def _pow(args: list[object], context) -> object:
    base, exponent = args
    if base is None or exponent is None:
        return None
    if isinstance(base, bool) or not isinstance(base, (int, float)):
        raise TypeError("pow() expects numeric operands")
    if isinstance(exponent, bool) or not isinstance(exponent, (int, float)):
        raise TypeError("pow() expects numeric operands")
    return base**exponent


def _rand(args: list[object], context) -> object:
    return random.random()


def _sign(args: list[object], context) -> object:
    (value,) = args
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("sign() expects numeric operands")
    return 0 if value == 0 else (1 if value > 0 else -1)


def _pi(args: list[object], context) -> object:
    return math.pi


def _euler(args: list[object], context) -> object:
    return math.e


def _random_uuid(args: list[object], context) -> object:
    return str(uuid.uuid4())


def _range(args: list[object], context) -> object:
    start, end = args[0], args[1]
    step = args[2] if len(args) > 2 else 1
    if start is None or end is None or step is None:
        return None
    for label, bound in (("range() start", start), ("range() end", end), ("range() step", step)):
        if isinstance(bound, bool) or not isinstance(bound, int):
            raise TypeError(f"{label} must be an integer")
    if step == 0:
        raise ValueError("range() step cannot be zero")
    if (step > 0 and start > end) or (step < 0 and start < end):
        return []
    stop = end + (1 if step > 0 else -1)
    return list(range(start, stop, step))


def _reverse(args: list[object], context) -> object:
    (value,) = args
    if value is None:
        return None
    if isinstance(value, str):
        return value[::-1]
    if isinstance(value, (list, tuple)):
        return list(reversed(value))
    raise TypeError("reverse() expects a list or string")


def _tail(args: list[object], context) -> object:
    (value,) = args
    if value is None:
        return None
    _expect_type(value, "tail()", list, tuple)
    return list(value[1:])


def _keys(args: list[object], context) -> object:
    (value,) = args
    if value is None:
        return None
    if isinstance(value, dict):
        return list(value.keys())
    properties = getattr(value, "properties", None)
    if isinstance(properties, dict):
        return list(properties.keys())
    raise TypeError("keys() expects a map, node, or relationship")


FUNCTIONS: dict[str, FunctionDef] = {
    "coalesce": _scalar("coalesce", (1, None), _coalesce),
    "id": _scalar("id", (1, 1), _entity_id),
    "elementid": _scalar("elementid", (1, 1), _entity_id),
    "type": _scalar("type", (1, 1), _relationship_type),
    "labels": _scalar("labels", (1, 1), _node_labels),
    "startnode": _scalar("startnode", (1, 1), _start_node),
    "endnode": _scalar("endnode", (1, 1), _end_node),
    "properties": _scalar("properties", (1, 1), _properties),
    "head": _scalar("head", (1, 1), _head),
    "last": _scalar("last", (1, 1), _last),
    "size": _scalar("size", (1, 1), _size),
    "length": _scalar("length", (1, 1), _length),
    "toboolean": _scalar("toboolean", (1, 1), _to_boolean),
    "tointeger": _scalar("tointeger", (1, 1), _to_integer),
    "tofloat": _scalar("tofloat", (1, 1), _to_float),
    "tostring": _scalar("tostring", (1, 1), _to_string),
    "trim": _scalar("trim", (1, 1), _trim),
    "ltrim": _scalar("ltrim", (1, 1), _ltrim),
    "rtrim": _scalar("rtrim", (1, 1), _rtrim),
    "toupper": _scalar("toupper", (1, 1), _case_fold("toUpper", str.upper)),
    "tolower": _scalar("tolower", (1, 1), _case_fold("toLower", str.lower)),
    "replace": _scalar("replace", (3, 3), _replace),
    "split": _scalar("split", (2, 2), _split),
    "substring": _scalar("substring", (2, 3), _substring),
    "left": _scalar("left", (2, 2), _side("left", True)),
    "right": _scalar("right", (2, 2), _side("right", False)),
    "abs": _scalar("abs", (1, 1), _numeric_unary("abs", abs)),
    "ceil": _scalar("ceil", (1, 1), _numeric_unary("ceil", math.ceil)),
    "floor": _scalar("floor", (1, 1), _numeric_unary("floor", math.floor)),
    "round": _scalar("round", (1, 2), _round),
    "sqrt": _scalar("sqrt", (1, 1), _numeric_unary("sqrt", math.sqrt)),
    "pow": _scalar("pow", (2, 2), _pow),
    "sign": _scalar("sign", (1, 1), _sign),
    "exp": _scalar("exp", (1, 1), _numeric_unary("exp", math.exp)),
    "log": _scalar("log", (1, 1), _numeric_unary("log", math.log)),
    "log10": _scalar("log10", (1, 1), _numeric_unary("log10", math.log10)),
    "sin": _scalar("sin", (1, 1), _numeric_unary("sin", math.sin)),
    "cos": _scalar("cos", (1, 1), _numeric_unary("cos", math.cos)),
    "tan": _scalar("tan", (1, 1), _numeric_unary("tan", math.tan)),
    "pi": _scalar("pi", (0, 0), _pi),
    "e": _scalar("e", (0, 0), _euler),
    "rand": _scalar("rand", (0, 0), _rand),
    "randomuuid": _scalar("randomuuid", (0, 0), _random_uuid),
    "range": _scalar("range", (2, 3), _range),
    "reverse": _scalar("reverse", (1, 1), _reverse),
    "tail": _scalar("tail", (1, 1), _tail),
    "keys": _scalar("keys", (1, 1), _keys),
    "nodes": _scalar("nodes", (1, 1), partial(_path_elements, name="nodes")),
    "relationships": _scalar("relationships", (1, 1), partial(_path_elements, name="relationships")),
}


def _aggregate_stub(name: str) -> Callable[[list[object], object], object]:
    """Build the per-row guard for an aggregate function.

    Aggregates execute per group in ``AggregateOperator``; reaching this stub
    means a planner bug. Aggregate arity stays enforced by semantic
    validation, so the registry arity below is a placeholder.
    """

    def execute(args: list[object], context) -> object:
        raise TypeError(f"{name}() aggregates rows and cannot be evaluated per row")

    return execute


for _aggregate_name in AGGREGATE_FUNCTIONS:
    FUNCTIONS[_aggregate_name] = FunctionDef(_aggregate_name, (1, 1), "aggregate", _aggregate_stub(_aggregate_name))

del _aggregate_name


def get_function_def(name: str) -> FunctionDef | None:
    """Return the registry entry for a (case-insensitive) function name."""
    return FUNCTIONS.get(name.lower())


def is_aggregate_function(name: str) -> bool:
    """Return whether a function name aggregates rows per group."""
    return name.lower() in AGGREGATE_FUNCTIONS


def check_scalar_arity(defn: FunctionDef, given: int) -> str | None:
    """Return an error message when ``given`` violates ``defn`` arity."""
    minimum, maximum = defn.arity
    if maximum is None and given < minimum:
        return f"{defn.name} expects at least {minimum} argument(s), got {given}"
    if maximum is not None and not minimum <= given <= maximum:
        if minimum == maximum:
            return f"{defn.name} expects exactly {minimum} argument(s), got {given}"
        return f"{defn.name} expects {minimum} to {maximum} arguments, got {given}"
    return None

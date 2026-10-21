"""Immutable typed literal vocabulary; no rule syntax or evaluation.

Literals are explicit finite values with declared kinds: boolean, integer,
float, text, bytes, instant (microseconds since the Unix epoch) and null.
Entity strings and rule variables are not literals. Value identity is strict
per kind; lexical forms are parsed explicitly and invalid inputs fail closed.
NaN participates under IEEE semantics: it is unequal to everything, including
itself. Cross-kind comparisons are never implicit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .model import ReasoningError

KINDS = ("boolean", "integer", "float", "text", "bytes", "instant", "null")


def _canonical(kind: str, value: object) -> object:
    if kind == "boolean":
        if type(value) is not bool:
            raise ReasoningError("boolean literals require a bool value")
        return value
    if kind == "integer":
        if type(value) is not int:
            raise ReasoningError("integer literals require an int value, never bool or float")
        return value
    if kind == "float":
        if type(value) is not float:
            raise ReasoningError("float literals require a float value")
        return value
    if kind == "text":
        if type(value) is not str:
            raise ReasoningError("text literals require a str value (empty allowed)")
        return value
    if kind == "bytes":
        if type(value) is not bytes:
            raise ReasoningError("bytes literals require a bytes value")
        return value
    if kind == "instant":
        if type(value) is not int or isinstance(value, bool):
            raise ReasoningError("instant literals require integer microseconds")
        return value
    if type(value) is not type(None):
        raise ReasoningError("null literals carry no value")
    return None


@dataclass(frozen=True, order=False)
class Literal:
    """One immutable typed value; equality is kind-strict (NaN != NaN)."""

    kind: str
    value: Any

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ReasoningError(f"literal kind must be one of {KINDS}")
        object.__setattr__(self, "value", _canonical(self.kind, self.value))

    @classmethod
    def from_lexical(cls, kind: str, lexical: str) -> Literal:
        """Parse a lexical form strictly; malformed input fails closed."""
        if kind not in KINDS:
            raise ReasoningError(f"literal kind must be one of {KINDS}")
        if not isinstance(lexical, str):
            raise ReasoningError("lexical forms must be str values")
        try:
            if kind == "boolean":
                if lexical == "true":
                    return cls(kind, True)
                if lexical == "false":
                    return cls(kind, False)
                raise ReasoningError(f"invalid boolean lexical form: {lexical!r}")
            if kind == "integer":
                text = lexical.strip()
                if not text or (text[0] in "+-" and len(text) == 1):
                    raise ReasoningError(f"invalid integer lexical form: {lexical!r}")
                return cls(kind, int(text, 10))
            if kind == "float":
                text = lexical.strip().lower()
                if text in ("nan", "+nan", "-nan"):
                    return cls(kind, float("nan"))
                if text in ("inf", "+inf", "infinity", "+infinity"):
                    return cls(kind, float("inf"))
                if text in ("-inf", "-infinity"):
                    return cls(kind, float("-inf"))
                return cls(kind, float(text))
            if kind == "text":
                return cls(kind, lexical)
            if kind == "bytes":
                return cls(kind, bytes.fromhex("".join(lexical.split())))
            if kind == "instant":
                return cls(kind, int(lexical.strip(), 10))
            if lexical != "":
                raise ReasoningError("null literals accept only the empty lexical form")
            return cls(kind, None)
        except ReasoningError:
            raise
        except (ValueError, TypeError) as exc:
            raise ReasoningError(f"invalid {kind} lexical form: {lexical!r}") from exc

    @property
    def lexical(self) -> str:
        """Canonical lexical form; NaN renders as ``NaN`` (still unequal to itself)."""
        if self.kind == "boolean":
            return "true" if self.value else "false"
        if self.kind == "integer" or self.kind == "instant":
            return str(self.value)
        if self.kind == "float":
            if self.value != self.value:
                return "NaN"
            if self.value in (float("inf"), float("-inf")):
                return "INF" if self.value > 0 else "-INF"
            return repr(self.value)
        if self.kind == "text":
            return self.value
        if self.kind == "bytes":
            return self.value.hex()
        return ""


def same_kind(first: Literal, second: Literal) -> bool:
    """Kind-strict type compatibility for value operations."""
    if not isinstance(first, Literal) or not isinstance(second, Literal):
        raise ReasoningError("comparison operands must be Literal values")
    return first.kind == second.kind


def equal(first: Literal, second: Literal) -> bool:
    """Kind-strict equality; null equals null, NaN equals nothing.

    Null is an explicit absence marker, not SQL-style unknown, so grouping and
    joins stay deterministic. NaN is unequal to everything including itself.
    """
    if not same_kind(first, second):
        return False
    if first.kind == "float":
        return bool(first.value == second.value)
    return first.value == second.value


def compare(first: Literal, second: Literal) -> int | None:
    """Three-way ordering for same-kind ordered values, else ``None``.

    ``None`` means incomparable (different kinds, null operands or NaN
    involvement) — never an implicit cross-kind ranking. Callers treat
    incomparability as filter failure, not as an error.
    """
    if not same_kind(first, second) or first.kind == "null":
        return None
    left, right = first.value, second.value
    if first.kind == "float" and (left != left or right != right):
        return None
    if left == right:
        return 0
    return -1 if left < right else 1

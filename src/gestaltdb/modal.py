"""Bounded four-valued modal evaluation over temporal epistemic claims."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from .epistemic import ClaimObjectKind, ClaimPolarity, ClaimStatus
from .versioning import TemporalVersionError, canonical_json_bytes


class ModalError(TemporalVersionError):
    """Base error for invalid modal formulas and evaluations."""


class ModalEvaluationLimitError(ModalError):
    """Raised when a bounded modal evaluation exhausts a configured limit."""


class ModalOperator(str, Enum):
    """Operators accepted by :class:`ModalExpression`."""

    ATOM = "ATOM"
    NOT = "NOT"
    AND = "AND"
    OR = "OR"
    BELIEVES = "BELIEVES"
    KNOWS = "KNOWS"
    POSSIBLE = "POSSIBLE"
    NECESSARY = "NECESSARY"


class AccessibilityKind(str, Enum):
    """Independent accessibility frames used by modal operators."""

    BELIEF = "belief"
    KNOWLEDGE = "knowledge"
    MODAL = "modal"


ACCESSIBILITY_PREDICATES = {
    AccessibilityKind.BELIEF: "gestaltdb:accessible:belief",
    AccessibilityKind.KNOWLEDGE: "gestaltdb:accessible:knowledge",
    AccessibilityKind.MODAL: "gestaltdb:accessible:modal",
}


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ModalError(f"modal {name} must be a non-empty string")
    value.encode("utf-8")
    return value


@dataclass(frozen=True)
class ModalExpression:
    """A validated finite atom, connective, or nested modal expression.

    Mapping forms use ``subject``/``predicate``/``object`` for atoms,
    ``formula`` for unary operators, and ``operands`` for ``AND``/``OR``.
    Every modal operator requires an explicit ``agent``.
    """

    operator: ModalOperator
    subject: str | None = None
    predicate: str | None = None
    object: object = None
    object_kind: ClaimObjectKind = ClaimObjectKind.ENTITY
    agent: str | None = None
    operands: tuple["ModalExpression", ...] = ()

    def __post_init__(self) -> None:
        try:
            operator = ModalOperator(self.operator)
        except (TypeError, ValueError) as exc:
            raise ModalError(f"unsupported modal operator: {self.operator}") from exc
        object.__setattr__(self, "operator", operator)
        operands = tuple(self.operands)
        if not all(isinstance(item, ModalExpression) for item in operands):
            raise ModalError("modal operands must be ModalExpression values")
        object.__setattr__(self, "operands", operands)
        if operator is ModalOperator.ATOM:
            object.__setattr__(self, "subject", _text(self.subject, "subject"))
            object.__setattr__(self, "predicate", _text(self.predicate, "predicate"))
            try:
                object_kind = ClaimObjectKind(self.object_kind)
            except (TypeError, ValueError) as exc:
                raise ModalError("atom object_kind must be 'entity' or 'literal'") from exc
            object.__setattr__(self, "object_kind", object_kind)
            if object_kind is ClaimObjectKind.ENTITY:
                _text(self.object, "entity object")
            else:
                try:
                    canonical_json_bytes(self.object)
                except TemporalVersionError as exc:
                    raise ModalError("atom literal object must be JSON-compatible") from exc
            if self.agent is not None:
                object.__setattr__(self, "agent", _text(self.agent, "atom agent"))
            if operands:
                raise ModalError("ATOM cannot contain operands")
            return
        if self.subject is not None or self.predicate is not None or self.object is not None:
            raise ModalError(f"{operator.value} cannot contain atom fields")
        if operator in {ModalOperator.AND, ModalOperator.OR}:
            if len(operands) < 2:
                raise ModalError(f"{operator.value} requires at least two operands")
            if self.agent is not None:
                raise ModalError(f"{operator.value} cannot contain an agent")
            return
        if len(operands) != 1:
            raise ModalError(f"{operator.value} requires exactly one formula")
        if operator is ModalOperator.NOT:
            if self.agent is not None:
                raise ModalError("NOT cannot contain an agent")
            return
        object.__setattr__(self, "agent", _text(self.agent, f"{operator.value} agent"))

    @classmethod
    def from_value(cls, value: object) -> "ModalExpression":
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise ModalError("modal expression must be an object")
        try:
            canonical_json_bytes(value)
        except (TemporalVersionError, RecursionError) as exc:
            raise ModalError("modal expression must be finite and JSON-compatible") from exc
        raw_operator = value.get("operator", "ATOM")
        try:
            operator = ModalOperator(str(raw_operator).upper())
        except ValueError as exc:
            raise ModalError(f"unsupported modal operator: {raw_operator}") from exc

        if operator is ModalOperator.ATOM:
            allowed = {"operator", "subject", "predicate", "object", "objectKind", "object_kind", "agent"}
            if set(value) - allowed:
                raise ModalError("atom contains unsupported fields")
            try:
                subject = _text(value["subject"], "subject")
                predicate = _text(value["predicate"], "predicate")
                claim_object = value["object"]
                object_kind = ClaimObjectKind(value.get("objectKind", value.get("object_kind", "entity")))
            except KeyError as exc:
                raise ModalError("atom requires subject, predicate, and object") from exc
            except (TypeError, ValueError) as exc:
                raise ModalError("atom objectKind must be 'entity' or 'literal'") from exc
            # Reuse canonical JSON validation for literal values.
            if object_kind is ClaimObjectKind.ENTITY:
                _text(claim_object, "entity object")
            else:
                canonical_json_bytes(claim_object)
            atom_agent = value.get("agent")
            if atom_agent is not None:
                atom_agent = _text(atom_agent, "atom agent")
            return cls(operator, subject, predicate, claim_object, object_kind, atom_agent)

        if operator in {ModalOperator.AND, ModalOperator.OR}:
            if set(value) - {"operator", "operands"}:
                raise ModalError(f"{operator.value} contains unsupported fields")
            raw_operands = value.get("operands")
            if not isinstance(raw_operands, (list, tuple)) or len(raw_operands) < 2:
                raise ModalError(f"{operator.value} requires at least two operands")
            return cls(operator, operands=tuple(cls.from_value(item) for item in raw_operands))

        if set(value) - {"operator", "formula", "agent"}:
            raise ModalError(f"{operator.value} contains unsupported fields")
        if "formula" not in value:
            raise ModalError(f"{operator.value} requires formula")
        agent = value.get("agent")
        if operator is ModalOperator.NOT:
            if agent is not None:
                raise ModalError("NOT cannot contain an agent")
        else:
            agent = _text(agent, f"{operator.value} agent")
        return cls(operator, agent=agent, operands=(cls.from_value(value["formula"]),))

    def to_dict(self) -> dict[str, object]:
        if self.operator is ModalOperator.ATOM:
            result = {
                "operator": "ATOM", "subject": self.subject, "predicate": self.predicate,
                "object": self.object, "objectKind": self.object_kind.value,
            }
            if self.agent is not None:
                result["agent"] = self.agent
            return result
        if self.operator in {ModalOperator.AND, ModalOperator.OR}:
            return {"operator": self.operator.value, "operands": [item.to_dict() for item in self.operands]}
        result = {"operator": self.operator.value, "formula": self.operands[0].to_dict()}
        if self.agent is not None:
            result["agent"] = self.agent
        return result


@dataclass(frozen=True)
class ModalEntailmentResult:
    """Four-valued result and deterministic finite evaluation evidence."""

    status: ClaimStatus
    confidence: float | None
    explanation: Mapping[str, object]


@dataclass(frozen=True)
class _Value:
    true: bool
    false: bool
    true_confidence: float | None
    false_confidence: float | None
    explanation: dict[str, object]


def _status(value: _Value) -> ClaimStatus:
    if value.true and value.false:
        return ClaimStatus.BOTH
    if value.true:
        return ClaimStatus.SUPPORTED
    if value.false:
        return ClaimStatus.REFUTED
    return ClaimStatus.UNKNOWN


def _confidence(value: _Value) -> float | None:
    status = _status(value)
    if status is ClaimStatus.SUPPORTED:
        return value.true_confidence
    if status is ClaimStatus.REFUTED:
        return value.false_confidence
    if status is ClaimStatus.BOTH:
        if value.true_confidence is None or value.false_confidence is None:
            return None
        return min(value.true_confidence, value.false_confidence)
    return None


def _all_confidences(values: list[float | None]) -> float | None:
    return None if not values or any(value is None for value in values) else min(values)


def _any_confidences(values: list[float | None]) -> float | None:
    known = [value for value in values if value is not None]
    return max(known) if known else None


def _evidence(version) -> dict[str, object]:
    claim = version.claim
    return {
        "versionId": version.version_id,
        "claimId": version.logical_id,
        "polarity": claim.polarity.value,
        "agent": claim.agent,
        "source": claim.source,
        "confidence": claim.confidence,
        "world": claim.world,
        "validFrom": version.valid.start.epoch_microseconds,
        "validTo": None if version.valid.end is None else version.valid.end.epoch_microseconds,
        "systemFrom": version.system_time.epoch_microseconds,
        "provenance": claim.to_dict()["provenance"],
    }


def evaluate_modal(
    view,
    expression: ModalExpression | Mapping[str, object],
    *,
    world: str,
    max_depth: int = 4,
    max_states: int = 1_000,
) -> ModalEntailmentResult:
    """Evaluate one formula under a pinned read view and explicit finite bounds."""
    formula = ModalExpression.from_value(expression)
    world = _text(world, "world")
    for name, value in (("max_depth", max_depth), ("max_states", max_states)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ModalError(f"{name} must be a positive integer")

    cache: dict[tuple[bytes, str, int], _Value] = {}
    state_count = 0

    def evaluate(current: ModalExpression, current_world: str, depth: int) -> _Value:
        nonlocal state_count
        key = (canonical_json_bytes(current.to_dict()), current_world, depth)
        if key in cache:
            return cache[key]
        state_count += 1
        if state_count > max_states:
            raise ModalEvaluationLimitError(f"modal evaluation exceeded max_states={max_states}")

        operator = current.operator
        if operator is ModalOperator.ATOM:
            versions = tuple(view.iter_claims_as_of(
                subject=current.subject, predicate=current.predicate, object=current.object,
                object_kind=current.object_kind, world=current_world, agent=current.agent,
            ))
            positive = [item for item in versions if item.claim.polarity is ClaimPolarity.POSITIVE]
            negative = [item for item in versions if item.claim.polarity is ClaimPolarity.NEGATIVE]
            result = _Value(
                bool(positive), bool(negative),
                _any_confidences([item.claim.confidence for item in positive]),
                _any_confidences([item.claim.confidence for item in negative]),
                {"operator": "ATOM", "world": current_world, "status": "", "evidence": [_evidence(item) for item in versions]},
            )
        elif operator is ModalOperator.NOT:
            child = evaluate(current.operands[0], current_world, depth)
            result = _Value(child.false, child.true, child.false_confidence, child.true_confidence, {
                "operator": "NOT", "world": current_world, "status": "", "children": [child.explanation]
            })
        elif operator in {ModalOperator.AND, ModalOperator.OR}:
            children = [evaluate(item, current_world, depth) for item in current.operands]
            if operator is ModalOperator.AND:
                truth = all(item.true for item in children)
                falsity = any(item.false for item in children)
                true_conf = _all_confidences([item.true_confidence for item in children]) if truth else None
                false_conf = _any_confidences([item.false_confidence for item in children if item.false]) if falsity else None
            else:
                truth = any(item.true for item in children)
                falsity = all(item.false for item in children)
                true_conf = _any_confidences([item.true_confidence for item in children if item.true]) if truth else None
                false_conf = _all_confidences([item.false_confidence for item in children]) if falsity else None
            result = _Value(truth, falsity, true_conf, false_conf, {
                "operator": operator.value, "world": current_world, "status": "",
                "children": [item.explanation for item in children],
            })
        else:
            if depth >= max_depth:
                raise ModalEvaluationLimitError(f"modal evaluation exceeded max_depth={max_depth}")
            if operator is ModalOperator.BELIEVES:
                kind, universal = AccessibilityKind.BELIEF, True
            elif operator is ModalOperator.KNOWS:
                kind, universal = AccessibilityKind.KNOWLEDGE, True
            elif operator is ModalOperator.POSSIBLE:
                kind, universal = AccessibilityKind.MODAL, False
            else:
                kind, universal = AccessibilityKind.MODAL, True
            effective_agent = current.agent
            links = tuple(view.iter_claims_as_of(
                subject=current_world, predicate=ACCESSIBILITY_PREDICATES[kind],
                object_kind=ClaimObjectKind.ENTITY, agent=effective_agent,
                world=current_world, polarity=ClaimPolarity.POSITIVE,
            ))
            links = tuple(sorted(links, key=lambda item: (item.claim.object, item.version_id)))
            targets = sorted({item.claim.object for item in links})
            children = [evaluate(current.operands[0], target, depth + 1) for target in targets]
            if not children:
                truth = falsity = False
                true_conf = false_conf = None
            elif universal:
                truth = all(item.true for item in children)
                falsity = any(item.false for item in children)
                true_conf = _all_confidences([item.true_confidence for item in children]) if truth else None
                false_conf = _any_confidences([item.false_confidence for item in children if item.false]) if falsity else None
            else:
                truth = any(item.true for item in children)
                falsity = all(item.false for item in children)
                true_conf = _any_confidences([item.true_confidence for item in children if item.true]) if truth else None
                false_conf = _all_confidences([item.false_confidence for item in children]) if falsity else None
            result = _Value(truth, falsity, true_conf, false_conf, {
                "operator": operator.value, "agent": effective_agent, "world": current_world,
                "status": "", "accessibility": [
                    {"toWorld": item.claim.object, **_evidence(item)} for item in links
                ], "children": [item.explanation for item in children],
            })
        result.explanation["status"] = _status(result).value
        cache[key] = result
        return result

    value = evaluate(formula, world, 0)
    explanation = {
        "status": _status(value).value,
        "world": world,
        "validTime": view.valid_time.epoch_microseconds,
        "throughCommit": view.commit_horizon,
        "states": state_count,
        "maxDepth": max_depth,
        "maxStates": max_states,
        "evaluation": value.explanation,
    }
    return ModalEntailmentResult(_status(value), _confidence(value), explanation)


__all__ = [
    "ACCESSIBILITY_PREDICATES", "AccessibilityKind", "ModalEntailmentResult",
    "ModalError", "ModalEvaluationLimitError", "ModalExpression", "ModalOperator",
    "evaluate_modal",
]

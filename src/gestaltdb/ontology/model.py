"""Immutable, canonical ontology values with no database or temporal coupling."""

from __future__ import annotations

from dataclasses import dataclass, fields
import hashlib
import json
from typing import ClassVar

from ..reasoning.model import ReasoningError, _text


PROFILE = "gestalt-core-v1"
TYPE_PREDICATE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"


class OntologyError(ReasoningError):
    """Invalid or unsupported content in the selected ontology fragment."""


def _identifier(value: object, name: str) -> str:
    try:
        return _text(value, name)
    except ReasoningError as exc:
        raise OntologyError(str(exc)) from exc


def _digest(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class _Axiom:
    _property_fields: ClassVar[tuple[str, ...]] = ()
    _symmetric: ClassVar[bool] = False

    def __post_init__(self) -> None:
        for field in fields(self):
            _identifier(getattr(self, field.name), field.name)
        for name in self._property_fields:
            if getattr(self, name) == TYPE_PREDICATE:
                raise OntologyError("rdf:type is reserved for class membership")
        if self._symmetric and self.left > self.right:
            left, right = self.left, self.right
            object.__setattr__(self, "left", right)
            object.__setattr__(self, "right", left)

    def _signature(self) -> list[str]:
        return [type(self).__name__, *(getattr(self, field.name) for field in fields(self))]

    @property
    def axiom_id(self) -> str:
        """Content identity, shared when the same axiom occurs in two ontologies."""
        return _digest(["gestaltdb-axiom-v1", self._signature()])


@dataclass(frozen=True)
class SubClassOf(_Axiom):
    subclass: str
    superclass: str


@dataclass(frozen=True)
class SubPropertyOf(_Axiom):
    subproperty: str
    superproperty: str
    _property_fields: ClassVar[tuple[str, ...]] = ("subproperty", "superproperty")


@dataclass(frozen=True)
class EquivalentClasses(_Axiom):
    left: str
    right: str
    _symmetric: ClassVar[bool] = True


@dataclass(frozen=True)
class EquivalentProperties(_Axiom):
    left: str
    right: str
    _property_fields: ClassVar[tuple[str, ...]] = ("left", "right")
    _symmetric: ClassVar[bool] = True


@dataclass(frozen=True)
class Domain(_Axiom):
    property: str
    class_id: str
    _property_fields: ClassVar[tuple[str, ...]] = ("property",)


@dataclass(frozen=True)
class Range(_Axiom):
    property: str
    class_id: str
    _property_fields: ClassVar[tuple[str, ...]] = ("property",)


@dataclass(frozen=True)
class InverseOf(_Axiom):
    left: str
    right: str
    _property_fields: ClassVar[tuple[str, ...]] = ("left", "right")
    _symmetric: ClassVar[bool] = True


@dataclass(frozen=True)
class Symmetric(_Axiom):
    property: str
    _property_fields: ClassVar[tuple[str, ...]] = ("property",)


@dataclass(frozen=True)
class Transitive(_Axiom):
    property: str
    _property_fields: ClassVar[tuple[str, ...]] = ("property",)


Axiom = (
    SubClassOf | SubPropertyOf | EquivalentClasses | EquivalentProperties |
    Domain | Range | InverseOf | Symmetric | Transitive
)
_SUPPORTED = frozenset({
    SubClassOf, SubPropertyOf, EquivalentClasses, EquivalentProperties,
    Domain, Range, InverseOf, Symmetric, Transitive,
})


@dataclass(frozen=True)
class Ontology:
    """One explicit immutable ontology revision; no clock or import resolution.

    Axiom order and duplicate occurrences do not affect revision identity.
    Ontology identity does participate. This is a structural content revision,
    not equivalence checking between logically equivalent collections of axioms.
    """

    ontology_id: str
    axioms: tuple[Axiom, ...] = ()
    profile: str = PROFILE

    def __post_init__(self) -> None:
        _identifier(self.ontology_id, "ontology_id")
        if self.profile != PROFILE:
            raise OntologyError(f"unsupported ontology profile: {self.profile!r}")
        if not isinstance(self.axioms, (tuple, list)):
            raise OntologyError("axioms must be a tuple or list")
        if not all(type(axiom) in _SUPPORTED for axiom in self.axioms):
            raise OntologyError("unsupported axiom: gestalt-core-v1 accepts only its named axiom values")
        canonical = tuple(sorted(set(self.axioms), key=lambda axiom: axiom._signature()))
        object.__setattr__(self, "axioms", canonical)

    @property
    def revision_id(self) -> str:
        return _digest([
            "gestaltdb-ontology-v1", self.profile, self.ontology_id,
            [axiom._signature() for axiom in self.axioms],
        ])

"""Pure compilation from the selected ontology fragment to positive Datalog."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock

from ..reasoning.model import Atom, Program, Rule, Variable
from .model import (
    Domain, EquivalentClasses, EquivalentProperties, IntersectionSubClassOf, InverseOf, Ontology,
    OntologyError, PropertyChain, Range, Reflexive, SubClassOf, SubPropertyOf, Symmetric,
    Transitive, UnionSubClassOf, TYPE_PREDICATE, _identifier,
)


@dataclass(frozen=True, order=True)
class RuleOrigin:
    """Exact source axiom for one generated rule, in its containing revision."""

    rule_id: str
    axiom_id: str

    def __post_init__(self) -> None:
        _identifier(self.rule_id, "rule_id")
        _identifier(self.axiom_id, "axiom_id")


@dataclass(frozen=True)
class CompiledOntology:
    """Immutable execution plan and complete rule-to-axiom origin table."""

    ontology: Ontology
    program: Program
    origins: tuple[RuleOrigin, ...]

    def __post_init__(self) -> None:
        if type(self.ontology) is not Ontology:
            raise OntologyError("compiled ontology must contain an Ontology")
        if not isinstance(self.program, Program):
            raise OntologyError("compiled program must be a Program")
        if not isinstance(self.origins, (tuple, list)) or not all(
            isinstance(origin, RuleOrigin) for origin in self.origins
        ):
            raise OntologyError("origins must be a tuple or list of RuleOrigin values")
        origins = tuple(sorted(self.origins))
        if tuple(origin.rule_id for origin in origins) != tuple(rule.rule_id for rule in self.program.rules):
            raise OntologyError("origins must identify each program rule exactly once")
        if {origin.axiom_id for origin in origins} != {axiom.axiom_id for axiom in self.ontology.axioms}:
            raise OntologyError("origins must cover exactly the source ontology's axioms")
        object.__setattr__(self, "origins", origins)

    @property
    def ontology_id(self) -> str:
        return self.ontology.ontology_id

    @property
    def revision_id(self) -> str:
        return self.ontology.revision_id

    @property
    def profile(self) -> str:
        return self.ontology.profile


def compile_ontology(ontology: Ontology) -> CompiledOntology:
    """Compile without evaluating facts or modifying any external rule catalog."""
    if type(ontology) is not Ontology:
        raise OntologyError("ontology must be an Ontology")
    rules = []
    origins = []
    x, y, z = Variable("x"), Variable("y"), Variable("z")
    for axiom in ontology.axioms:
        axiom_id = axiom.axiom_id

        def add(body: list[Atom], head: Atom, direction: str = "forward") -> None:
            rule_id = f"ontology:{axiom_id}:{direction}"
            rules.append(Rule(rule_id, body, head))
            origins.append(RuleOrigin(rule_id, axiom_id))

        if isinstance(axiom, SubClassOf):
            add([Atom(x, TYPE_PREDICATE, axiom.subclass)], Atom(x, TYPE_PREDICATE, axiom.superclass))
        elif isinstance(axiom, SubPropertyOf):
            add([Atom(x, axiom.subproperty, y)], Atom(x, axiom.superproperty, y))
        elif isinstance(axiom, EquivalentClasses):
            add([Atom(x, TYPE_PREDICATE, axiom.left)], Atom(x, TYPE_PREDICATE, axiom.right))
            add([Atom(x, TYPE_PREDICATE, axiom.right)], Atom(x, TYPE_PREDICATE, axiom.left), "reverse")
        elif isinstance(axiom, EquivalentProperties):
            add([Atom(x, axiom.left, y)], Atom(x, axiom.right, y))
            add([Atom(x, axiom.right, y)], Atom(x, axiom.left, y), "reverse")
        elif isinstance(axiom, Domain):
            add([Atom(x, axiom.property, y)], Atom(x, TYPE_PREDICATE, axiom.class_id))
        elif isinstance(axiom, Range):
            add([Atom(x, axiom.property, y)], Atom(y, TYPE_PREDICATE, axiom.class_id))
        elif isinstance(axiom, InverseOf):
            add([Atom(x, axiom.left, y)], Atom(y, axiom.right, x))
            add([Atom(x, axiom.right, y)], Atom(y, axiom.left, x), "reverse")
        elif isinstance(axiom, Symmetric):
            add([Atom(x, axiom.property, y)], Atom(y, axiom.property, x))
        elif isinstance(axiom, Transitive):
            add([Atom(x, axiom.property, y), Atom(y, axiom.property, z)], Atom(x, axiom.property, z))
        elif isinstance(axiom, PropertyChain):
            add([Atom(x, axiom.first, y), Atom(y, axiom.second, z)], Atom(x, axiom.result, z))
        elif isinstance(axiom, IntersectionSubClassOf):
            add([Atom(x, TYPE_PREDICATE, member) for member in axiom.members],
                Atom(x, TYPE_PREDICATE, axiom.superclass))
        elif isinstance(axiom, UnionSubClassOf):
            for index, member in enumerate(axiom.members):
                add([Atom(x, TYPE_PREDICATE, member)],
                    Atom(x, TYPE_PREDICATE, axiom.superclass), f"union-{index}")
        elif isinstance(axiom, Reflexive):
            add([Atom(x, TYPE_PREDICATE, axiom.scope)], Atom(x, axiom.property, x))
        else:
            # Ontology validation is strict; retain a fail-closed guard if the
            # model's supported set is expanded without compiler support.
            raise OntologyError(f"unsupported axiom: {type(axiom).__name__}")
    return CompiledOntology(ontology, Program(rules), origins)


class CompiledSchemaCache:
    """Bounded revision-keyed cache of compiled ontologies; no evaluation.

    Compilation is deterministic, so a revision hit returns the identical
    object. Eviction is FIFO once ``maxsize`` is exceeded. A lock guards
    concurrent callers; cached plans remain immutable and safe to share.
    """

    def __init__(self, maxsize: int = 64) -> None:
        if isinstance(maxsize, bool) or not isinstance(maxsize, int) or maxsize < 1:
            raise OntologyError("maxsize must be a positive integer")
        self._maxsize = maxsize
        self._entries: OrderedDict[str, CompiledOntology] = OrderedDict()
        self._lock = Lock()

    @property
    def maxsize(self) -> int:
        return self._maxsize

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def compile(self, ontology: Ontology) -> CompiledOntology:
        """Compile or reuse the plan bound to the ontology's revision."""
        if type(ontology) is not Ontology:
            raise OntologyError("ontology must be an Ontology")
        key = ontology.revision_id
        with self._lock:
            hit = self._entries.get(key)
            if hit is not None and hit.ontology == ontology:
                self._entries.move_to_end(key)
                return hit
            compiled = compile_ontology(ontology)
            self._entries[key] = compiled
            while len(self._entries) > self._maxsize:
                self._entries.popitem(last=False)
            return compiled

    def clear(self) -> None:
        """Drop all cached plans; in-flight users keep immutable results."""
        with self._lock:
            self._entries.clear()

"""Gated ontology profile expansion: a construct-by-construct conformance matrix.

Every known construct has exactly one row: what it means here, whether it
entails, validates, canonicalizes or waits, and which mechanism implements
it. Directional Horn rewrites, open-world validation and class-conflict
detection are separate rows — never one blurred claim. Anything without a
``supported`` row fails closed through ``check_construct``; extensions are
approved one row at a time by changing this matrix with its tests, never by
widening an admission rule. Complete OWL RL is a target only when every
normative applicable rule is independently covered; until then the named
subsets below are the whole published claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .model import ReasoningError, _text


@dataclass(frozen=True)
class ConstructEntry:
    """One matrix row: name, role, standing, mechanism and boundary note."""

    name: str
    role: str
    standing: str
    mechanism: str
    note: str

    def __post_init__(self) -> None:
        for field in ("name", "role", "standing", "mechanism", "note"):
            _text(getattr(self, field), field)
        if self.role not in ("entailment", "validation", "equality", "deferred"):
            raise ReasoningError("role must be entailment, validation, equality or deferred")
        if self.standing not in ("supported", "validation-only", "deferred"):
            raise ReasoningError("standing must be supported, validation-only or deferred")
        if (self.role == "deferred") != (self.standing == "deferred"):
            raise ReasoningError("deferred role and standing must agree")

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "role": self.role, "standing": self.standing,
                "mechanism": self.mechanism, "note": self.note}


def _row(name, role, standing, mechanism, note):
    return ConstructEntry(name, role, standing, mechanism, note)


MATRIX: tuple[ConstructEntry, ...] = tuple(sorted([
    _row("subclass", "entailment", "supported", "ontology.compiler",
         "Named subclass links compile to binary Horn rules."),
    _row("subproperty", "entailment", "supported", "ontology.compiler",
         "Named subproperty links with the same Horn compilation."),
    _row("equivalent-classes", "entailment", "supported", "ontology.compiler",
         "Named equivalence expands to a subclass pair with exact origins."),
    _row("equivalent-properties", "entailment", "supported", "ontology.compiler",
         "Named property equivalence expands to a subproperty pair."),
    _row("domain", "entailment", "supported", "ontology.compiler",
         "Domain axioms entail class membership; they never validate required fields."),
    _row("range", "entailment", "supported", "ontology.compiler",
         "Range axioms entail class membership; they never validate required fields."),
    _row("inverse", "entailment", "supported", "ontology.compiler",
         "Inverse pairs compile to directional Horn rewrites, never a merged relation."),
    _row("symmetric", "entailment", "supported", "ontology.compiler",
         "Symmetric properties mirror facts directionally."),
    _row("transitive", "entailment", "supported", "ontology.compiler",
         "Transitive closure over the positive fragment with exact origins."),
    _row("property-chain", "entailment", "supported", "ontology.compiler",
         "Two-link typed composition as one Horn rule."),
    _row("intersection-subclass-left", "entailment", "supported", "ontology.compiler",
         "Named left-side conjunction shares one Horn-safe implication."),
    _row("union-subclass-left", "entailment", "supported", "ontology.compiler",
         "Named left-side disjunction normalizes to per-member rules with exact origins."),
    _row("scoped-reflexivity", "entailment", "supported", "ontology.compiler",
         "Reflexive loops for named scope members only; unscoped reflexivity is unbounded."),
    _row("disjointness", "validation", "validation-only", "reasoning.validation",
         "Disjoint membership is reported evidence, never an inconsistency or inference."),
    _row("irreflexivity", "validation", "validation-only", "reasoning.validation",
         "Reflexive loops on declared properties are conflicts, not contradictions."),
    _row("functionality-conflict", "validation", "validation-only", "reasoning.validation",
         "Conflicting values are detected; equality is never inferred silently."),
    _row("denied-positive", "validation", "validation-only", "reasoning.validation",
         "Explicit denials contradict ground positives; absence stays open-world silence."),
    _row("unsatisfiable-class", "validation", "validation-only", "reasoning.validation",
         "Static schema check over subclass/disjointness edges; no instance data is read."),
    _row("required-property", "validation", "validation-only", "reasoning.structural",
         "Missing mandatory properties on explicitly closed classes; open-world silence stays silent."),
    _row("cardinality-bounds", "validation", "validation-only", "reasoning.structural",
         "Min/max occurrence counts on explicitly closed classes; inference-level counting stays deferred."),
    _row("datatype-check", "validation", "validation-only", "reasoning.structural",
         "Literal-kind restrictions on closed-class property facts; no datatype reasoning beyond identity."),
    _row("exclusivity", "validation", "validation-only", "reasoning.structural",
         "At-most-one / exactly-one-of groups on explicitly closed classes; no classical case splitting."),
    _row("same-as", "equality", "supported", "reasoning.equality",
         "Snapshot-local representatives with alias preservation and link evidence."),
    _row("different-from", "equality", "supported", "reasoning.equality",
         "Inequality conflicts carry merge-path plus violated-link evidence."),
    _row("functional-generation", "equality", "supported", "reasoning.equality",
         "Single-valued conflicts generate explicit links; nothing merges silently."),
    _row("disjunctive-head", "deferred", "deferred", "none",
         "Union on the right of an implication is non-Horn; no classical case splitting."),
    _row("unscoped-reflexivity", "deferred", "deferred", "none",
         "Global reflexivity is unbounded outside a finite evaluated domain."),
    _row("cardinality", "deferred", "deferred", "none",
         "Min/max/exact cardinality needs counting semantics beyond conflict detection."),
    _row("keys", "deferred", "deferred", "none",
         "Key constraints need identity generation beyond single-valued detection."),
    _row("datatype-facets", "deferred", "deferred", "none",
         "Facets beyond strict literal identity need a datatype reasoner."),
    _row("complement", "deferred", "deferred", "none",
         "Class complement needs scoped negation beyond explicit denials."),
    _row("nominals", "deferred", "deferred", "none",
         "oneOf enumerations need domain closure the open world does not grant."),
    _row("universal-restriction", "deferred", "deferred", "none",
         "allValuesFrom needs universal quantification outside Horn rules."),
    _row("existential-head", "deferred", "deferred", "none",
         "Fresh-individual generation is outside the finite-fact fragment."),
    _row("shape-constraints", "deferred", "deferred", "none",
         "SHACL-style shapes are a separate validation language, not this report vocabulary."),
    _row("recursive-negation", "deferred", "deferred", "none",
         "Negation through recursion has no stratification; use explicit closed scopes."),
    _row("aggregate-recursion", "deferred", "deferred", "none",
         "Recursive or lattice aggregation has no proven termination contract here."),
    _row("probabilistic-combination", "deferred", "deferred", "none",
         "Confidence combination is a separately selected calculus, never a default."),
], key=lambda item: item.name))

NAMED_SUBSETS: dict[str, frozenset[str]] = {
    "gestaltdb-rl-core-v1": frozenset(
        item.name for item in MATRIX if item.role == "entailment"),
    "gestaltdb-validation-v1": frozenset(
        item.name for item in MATRIX if item.role == "validation"),
    "gestaltdb-equality-v1": frozenset(
        item.name for item in MATRIX if item.role == "equality"),
}


def matrix_digest() -> str:
    """Structural identity of the conformance matrix; approvals change it."""
    import hashlib
    import json

    payload = ["gestaltdb-profile-matrix-v1",
               [item.to_dict() for item in MATRIX],
               sorted((name, sorted(members)) for name, members in NAMED_SUBSETS.items())]
    return "sha256:" + hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def check_construct(name: str, subset: str = "gestaltdb-rl-core-v1") -> ConstructEntry:
    """Admit a construct only through its named subset; otherwise fail closed.

    Unknown names, deferred rows and subset mismatches raise with the
    documented mechanism or reason — never a silent fallback. Extending a
    subset is an approval: change the matrix, its tests and this digest.
    """
    _text(name, "construct")
    _text(subset, "subset")
    if subset not in NAMED_SUBSETS:
        raise ReasoningError(f"unknown profile subset: {subset!r}")
    for item in MATRIX:
        if item.name == name:
            if name not in NAMED_SUBSETS[subset]:
                raise ReasoningError(
                    f"construct {name!r} is {item.standing} via {item.mechanism}; "
                    f"not in subset {subset!r} ({item.note})")
            return item
    raise ReasoningError(f"unknown construct: {name!r}; no row, no admission")


def subset_constructs(subset: str) -> tuple[str, ...]:
    """Sorted construct names admitted by one named subset."""
    _text(subset, "subset")
    if subset not in NAMED_SUBSETS:
        raise ReasoningError(f"unknown profile subset: {subset!r}")
    return tuple(sorted(NAMED_SUBSETS[subset]))


def approval_checklist() -> tuple[str, ...]:
    """Steps that approve one matrix extension; each leaves test evidence."""
    return ("specify construct semantics and termination contract",
            "implement behind the matrix row with exact origins",
            "add independent oracle/parity tests plus failure tests",
            "measure cost and record the frozen evidence reference",
            "update the matrix, named subsets and this digest")

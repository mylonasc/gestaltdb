"""Versioned, storage-neutral relational IR; not a second execution engine.

Only named-entity columns are accepted in v1. Arbitrary positive arities are
representable, but the existing evaluator still accepts binary ``Program`` only.
Typed literals, negative atoms and aggregates require future explicit profiles.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from .model import Program, ReasoningError, Term, Variable, _term, _text
from .protocols import InputContext


IR_VERSION = "gestaltdb-relational-v1"


def _digest(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _sequence(value: object, name: str) -> tuple:
    if not isinstance(value, (tuple, list)):
        raise ReasoningError(f"{name} must be a tuple or list")
    return tuple(value)


@dataclass(frozen=True, order=True)
class RelationSchema:
    """Non-null, set-valued relation over ordered, uniquely named entity columns."""

    name: str
    columns: tuple[str, ...]

    def __post_init__(self) -> None:
        _text(self.name, "relation name")
        columns = _sequence(self.columns, "columns")
        if not columns:
            raise ReasoningError("relation must have at least one column")
        for column in columns:
            _text(column, "column name")
        if len(set(columns)) != len(columns):
            raise ReasoningError("column names must be unique")
        object.__setattr__(self, "columns", columns)


@dataclass(frozen=True)
class RelationalAtom:
    """Constant relation name and ordered entity/explicit-variable terms."""

    relation: str
    terms: tuple[Term, ...]

    def __post_init__(self) -> None:
        _text(self.relation, "relation name")
        terms = _sequence(self.terms, "terms")
        if not terms:
            raise ReasoningError("atom must have at least one term")
        for term in terms:
            _term(term, "atom term")
        object.__setattr__(self, "terms", terms)

    @property
    def variables(self) -> frozenset[Variable]:
        return frozenset(term for term in self.terms if isinstance(term, Variable))

    def _encoded(self) -> list:
        return [self.relation, [["variable", term.name] if isinstance(term, Variable)
                                else ["entity", term] for term in self.terms]]


@dataclass(frozen=True)
class RelationalRule:
    """Safe positive single-head rule; body positions remain evidence positions."""

    rule_id: str
    body: tuple[RelationalAtom, ...]
    head: RelationalAtom

    def __post_init__(self) -> None:
        _text(self.rule_id, "rule_id")
        body = _sequence(self.body, "rule body")
        if not body or not all(isinstance(atom, RelationalAtom) for atom in body):
            raise ReasoningError("rule body must contain RelationalAtom values")
        if not isinstance(self.head, RelationalAtom):
            raise ReasoningError("rule head must be a RelationalAtom")
        bound = frozenset().union(*(atom.variables for atom in body))
        if not self.head.variables <= bound:
            raise ReasoningError("unsafe rule: head variables must occur in the body")
        object.__setattr__(self, "body", body)


@dataclass(frozen=True)
class RelationalProgram:
    """Canonical validated schema/catalog, with a new digest namespace.

    Schema order and catalog order do not affect identity. Column order, body
    occurrence order, variable names and rule IDs do. This is structural identity,
    not a claim of logical equivalence or an authenticated source horizon.
    """

    schemas: tuple[RelationSchema, ...]
    rules: tuple[RelationalRule, ...] = ()

    def __post_init__(self) -> None:
        schemas = _sequence(self.schemas, "schemas")
        rules = _sequence(self.rules, "rules")
        if not all(isinstance(schema, RelationSchema) for schema in schemas):
            raise ReasoningError("schemas must contain RelationSchema values")
        if not all(isinstance(rule, RelationalRule) for rule in rules):
            raise ReasoningError("rules must contain RelationalRule values")
        if len({schema.name for schema in schemas}) != len(schemas):
            raise ReasoningError("relation names must be unique")
        if len({rule.rule_id for rule in rules}) != len(rules):
            raise ReasoningError("rule IDs must be unique")
        by_name = {schema.name: schema for schema in schemas}
        for rule in rules:
            for atom in (*rule.body, rule.head):
                if atom.relation not in by_name:
                    raise ReasoningError(f"undeclared relation: {atom.relation}")
                if len(atom.terms) != len(by_name[atom.relation].columns):
                    raise ReasoningError(f"arity mismatch: {atom.relation}")
        object.__setattr__(self, "schemas", tuple(sorted(schemas)))
        object.__setattr__(self, "rules", tuple(sorted(rules, key=lambda r: r.rule_id)))

    @property
    def digest(self) -> str:
        return _digest([IR_VERSION,
                        [[s.name, list(s.columns), "entity", "set", "non-null"] for s in self.schemas],
                        [[r.rule_id, [a._encoded() for a in r.body], r.head._encoded()]
                         for r in self.rules]])

    @classmethod
    def from_program(cls, program: Program) -> RelationalProgram:
        """Lossless structural lowering; never changes the legacy program digest."""
        if not isinstance(program, Program):
            raise ReasoningError("program must be a Program")
        names = {atom.predicate for rule in program.rules for atom in (*rule.body, rule.head)}

        def lower(atom):
            return RelationalAtom(atom.predicate, (atom.subject, atom.object))

        return cls(tuple(RelationSchema(name, ("subject", "object")) for name in sorted(names)),
                   tuple(RelationalRule(rule.rule_id, tuple(lower(a) for a in rule.body), lower(rule.head))
                         for rule in program.rules))


@dataclass(frozen=True)
class CompilationIdentity:
    """Exact plan/result compatibility key, not a source authentication service.

    Adapters must verify the pinned context. Engines must compare the complete
    identity before reusing context-bound state; matching strings alone do not
    prove that a snapshot is authentic or still available.
    """

    program_digest: str
    context: InputContext
    dictionary_id: str
    semantics_profile: str
    executor_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.context, InputContext):
            raise ReasoningError("context must be an InputContext")
        for name in ("program_digest", "dictionary_id", "semantics_profile", "executor_version"):
            _text(getattr(self, name), name)

    @property
    def digest(self) -> str:
        context = self.context
        return _digest(["gestaltdb-compilation-v1", IR_VERSION, self.program_digest,
                        [context.context_id, context.world, context.ontology_revision,
                         context.source_policy_id, context.mapping_id],
                        self.dictionary_id, self.semantics_profile, self.executor_version])

    def require_compatible(self, other: CompilationIdentity) -> None:
        if not isinstance(other, CompilationIdentity) or self != other:
            raise ReasoningError("incompatible compilation identity; recompile/rebind explicitly")


def explain_plan(program: Program, *, identity: CompilationIdentity) -> dict:
    """JSON-safe structural plan description; no source reads or evaluation.

    Dynamic indexed-bucket ordering has no static row estimates. Actual rows,
    selected indexes and SCC execution belong to RE-04, not fabricated telemetry.
    Each call returns fresh mutable presentation data, never mutable plan state.
    """
    if not isinstance(program, Program):
        raise ReasoningError("program must be a Program")
    if not isinstance(identity, CompilationIdentity) or identity.program_digest != program.digest:
        raise ReasoningError("identity must bind this Program digest")
    if (identity.semantics_profile != "positive-binary-entity-v1"
            or identity.executor_version != "indexed-python-v1"):
        raise ReasoningError("unsupported semantics profile or executor for this plan description")
    relational = RelationalProgram.from_program(program)
    return {
        "schema_version": "gestaltdb-explain-v1",
        "kind": "structural-only",
        "identity": identity.digest,
        "program_digest": program.digest,
        "relational_digest": relational.digest,
        "semantics_profile": identity.semantics_profile,
        "executor_version": identity.executor_version,
        "join_order": "dynamic-smallest-bound-bucket",
        "rules": [{"rule_id": rule.rule_id,
                   "head_relation": rule.head.predicate,
                   "body": [{"position": index, "relation": atom.predicate,
                             "delta_candidate": True, "estimated_rows": None,
                             "actual_rows": None, "selected_index": None}
                            for index, atom in enumerate(rule.body)]}
                  for rule in program.rules],
    }

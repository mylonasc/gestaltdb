"""Finite typed Datalog: n-ary relations over entities and strict literals.

Ground values are nonempty entity strings or ``Literal`` values. Rule atoms
carry entity/variable/literal terms; ``Comparison`` filters apply kind-strict
``== != < <= > >=`` after their variables bind. Predicate variables are
accepted by the model but rejected at evaluation until an explicit expansion
lands. NaN never matches, cross-kind filters never match, and every filter
variable must occur in a relational body atom.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import itertools
from typing import Callable, Iterable

from .model import (
    ReasoningError,
    ReasoningLimitError,
    ReasoningLimits,
    Variable,
    _text,
    check_cancelled,
)
from .protocols import Evidence
from .terms import Literal, compare, equal


def _ground(value: object, name: str) -> str | Literal:
    if isinstance(value, Literal):
        return value
    return _text(value, name)


def _term(value: object, name: str) -> str | Variable | Literal:
    if isinstance(value, (Variable, Literal)):
        return value
    return _text(value, name)


@dataclass(frozen=True)
class TypedFact:
    """One ground n-ary assertion; values are entities or literals."""

    predicate: str
    values: tuple[str | Literal, ...]

    def __post_init__(self) -> None:
        _text(self.predicate, "fact predicate")
        if not isinstance(self.values, (tuple, list)):
            raise ReasoningError("fact values must be a tuple or list")
        values = tuple(_ground(value, "fact value") for value in self.values)
        object.__setattr__(self, "values", values)


@dataclass(frozen=True)
class TypedAtom:
    """Relational atom; predicate variables await explicit expansion."""

    predicate: str | Variable
    args: tuple[str | Variable | Literal, ...]

    def __post_init__(self) -> None:
        if isinstance(self.predicate, Variable):
            pass
        else:
            _text(self.predicate, "atom predicate")
        if not isinstance(self.args, (tuple, list)):
            raise ReasoningError("atom args must be a tuple or list")
        args = tuple(_term(value, "atom term") for value in self.args)
        object.__setattr__(self, "args", args)

    @property
    def variables(self) -> frozenset[Variable]:
        found = {term for term in self.args if isinstance(term, Variable)}
        if isinstance(self.predicate, Variable):
            found.add(self.predicate)
        return frozenset(found)


@dataclass(frozen=True)
class Comparison:
    """Kind-strict filter; incomparability fails the match, never errors."""

    op: str
    left: Variable | Literal
    right: Variable | Literal

    def __post_init__(self) -> None:
        if self.op not in ("==", "!=", "<", "<=", ">", ">="):
            raise ReasoningError(f"comparison op must be one of == != < <= > >=, got {self.op!r}")
        for name in ("left", "right"):
            value = getattr(self, name)
            if not isinstance(value, (Variable, Literal)):
                raise ReasoningError(f"comparison {name} must be a Variable or Literal")

    @property
    def variables(self) -> frozenset[Variable]:
        return frozenset(term for term in (self.left, self.right) if isinstance(term, Variable))

    def holds(self, bindings: dict[Variable, object]) -> bool:
        """Evaluate under complete bindings; unbound variables fail closed.

        Entity operands are incomparable with literals, so every operator —
        including ``==`` and ``!=`` — returns ``False`` rather than crossing
        the entity/literal sort boundary. Only unbound variables raise.
        """
        left = bindings.get(self.left, self.left) if isinstance(self.left, Variable) else self.left
        right = bindings.get(self.right, self.right) if isinstance(self.right, Variable) else self.right
        if isinstance(left, Variable) or isinstance(right, Variable):
            raise ReasoningError("comparison evaluated with unbound variables")
        if not isinstance(left, Literal) or not isinstance(right, Literal):
            return False
        if self.op == "==":
            return equal(left, right)
        if self.op == "!=":
            return not equal(left, right)
        order = compare(left, right)
        if order is None:
            return False
        return { "<": order < 0, "<=": order <= 0, ">": order > 0, ">=": order >= 0}[self.op]


@dataclass(frozen=True)
class GenericRule:
    """Safe positive rule with optional filters; heads stay single and relational.

    ``negated`` holds absence atoms over constant predicates. Negated atoms
    never bind variables: every variable they mention must already occur in
    the positive relational body, and head variables must be bound there too.
    Whether a negated predicate may be queried at all is a program-level
    closed-world declaration, checked during stratification.
    """

    rule_id: str
    body: tuple[TypedAtom, ...]
    filters: tuple[Comparison, ...]
    head: TypedAtom
    negated: tuple[TypedAtom, ...] = ()

    def __post_init__(self) -> None:
        _text(self.rule_id, "rule_id")
        for name in ("body", "filters", "negated"):
            value = getattr(self, name)
            if not isinstance(value, (tuple, list)):
                raise ReasoningError(f"rule {name} must be a tuple or list")
        body = tuple(self.body)
        filters = tuple(self.filters)
        negated = tuple(self.negated)
        if not body or not all(isinstance(atom, TypedAtom) for atom in body):
            raise ReasoningError("rule body must contain at least one TypedAtom")
        if not all(isinstance(item, Comparison) for item in filters):
            raise ReasoningError("rule filters must contain only Comparison values")
        if not all(isinstance(atom, TypedAtom) for atom in negated):
            raise ReasoningError("rule negations must contain only TypedAtom values")
        if not isinstance(self.head, TypedAtom):
            raise ReasoningError("rule head must be a TypedAtom")
        if isinstance(self.head.predicate, Variable):
            raise ReasoningError("head predicates must be constant")
        for atom in body:
            if isinstance(atom.predicate, Variable):
                term_names = {term.name for term in atom.args if isinstance(term, Variable)}
                if atom.predicate.name in term_names:
                    raise ReasoningError("a predicate variable must not also occur as a term variable")
                for item in filters:
                    if atom.predicate in item.variables:
                        raise ReasoningError("a predicate variable must not occur in filters")
        for atom in negated:
            if isinstance(atom.predicate, Variable):
                raise ReasoningError("negated predicates must be constant")
        bound = frozenset().union(*(atom.variables for atom in body))
        for item in filters:
            if not item.variables <= bound:
                raise ReasoningError("filter variables must occur in the relational body")
        for atom in negated:
            if not atom.variables <= bound:
                raise ReasoningError("negated variables must occur in the relational body")
        head_variables = {term for term in self.head.args if isinstance(term, Variable)}
        if not head_variables <= bound:
            raise ReasoningError("unsafe rule: head variables must occur in the body")
        object.__setattr__(self, "body", body)
        object.__setattr__(self, "filters", filters)
        object.__setattr__(self, "negated", negated)


@dataclass(frozen=True)
class Aggregation:
    """One grouped aggregation; ``target`` receives the result.

    ``source`` must be bound by the rule body; ``target`` must be fresh (absent
    from the body) so every aggregation stratum is non-recursive. ``count``
    tallies rows per group; ``sum``/``min``/``max`` consume integer/float
    literals, skipping nulls, and fail closed on other kinds or mixed kinds.
    """

    op: str
    source: Variable
    target: Variable

    def __post_init__(self) -> None:
        if self.op not in ("count", "sum", "min", "max"):
            raise ReasoningError(f"aggregation op must be one of count/sum/min/max, got {self.op!r}")
        for name in ("source", "target"):
            if not isinstance(getattr(self, name), Variable):
                raise ReasoningError(f"aggregation {name} must be a Variable")


@dataclass(frozen=True)
class MultiHeadRule:
    """Positive rule with an explicit finite non-empty head conjunction.

    Normalization splits one multi-head rule into equivalent single-head
    ``GenericRule`` values sharing the rule ID stem. Heads must be relational;
    filters and the body are shared unchanged. Range-restriction is checked per
    generated head, so a head using an unbound variable fails at normalization.
    """

    rule_id: str
    body: tuple[TypedAtom, ...]
    filters: tuple[Comparison, ...]
    heads: tuple[TypedAtom, ...]

    def __post_init__(self) -> None:
        _text(self.rule_id, "rule_id")
        for name in ("body", "filters", "heads"):
            if not isinstance(getattr(self, name), (tuple, list)):
                raise ReasoningError(f"rule {name} must be a tuple or list")
        object.__setattr__(self, "body", tuple(self.body))
        object.__setattr__(self, "filters", tuple(self.filters))
        object.__setattr__(self, "heads", tuple(self.heads))
        if not self.heads:
            raise ReasoningError("multi-head rules require at least one head")
        if len(self.heads) != len(set(self.heads)):
            raise ReasoningError("multi-head rules must not repeat a head")

    def normalize(self) -> tuple[GenericRule, ...]:
        """Split into single-head rules; head order follows declaration order."""
        return tuple(GenericRule(f"{self.rule_id}#{index}", self.body, self.filters, head)
                     for index, head in enumerate(self.heads))


def normalize_program(rules: Iterable[GenericRule | MultiHeadRule]) -> GenericProgram:
    """Flatten mixed single/multi-head rules; duplicate IDs fail closed."""
    try:
        items = list(rules)
    except TypeError as exc:
        raise ReasoningError("rules must be iterable") from exc
    flattened: list[GenericRule] = []
    for rule in items:
        if isinstance(rule, MultiHeadRule):
            flattened.extend(rule.normalize())
        elif isinstance(rule, GenericRule):
            flattened.append(rule)
        else:
            raise ReasoningError("rules must be GenericRule or MultiHeadRule values")
    return GenericProgram(flattened)


def predicate_domain(rules: Iterable[GenericRule], facts: Iterable[TypedFact]) -> tuple[str, ...]:
    """Finite known predicate universe from rules plus ground facts."""
    try:
        candidates = list(rules)
    except TypeError as exc:
        raise ReasoningError("rules must be iterable") from exc
    if not all(isinstance(rule, GenericRule) for rule in candidates):
        raise ReasoningError("rules must contain only GenericRule values")
    try:
        items = list(facts)
    except TypeError as exc:
        raise ReasoningError("facts must be iterable") from exc
    names: set[str] = set()
    for rule in candidates:
        for atom in (*rule.body, rule.head):
            if isinstance(atom.predicate, str):
                names.add(atom.predicate)
    for fact in items:
        if not isinstance(fact, TypedFact):
            raise ReasoningError("facts must contain only TypedFact values")
        names.add(fact.predicate)
    return tuple(sorted(names))


def expand_predicate_variables(rule: GenericRule, domain: Iterable[str]) -> tuple[GenericRule, ...]:
    """Bind each distinct predicate variable across an explicit finite domain.

    Expansion is the Cartesian product over the domain in first-occurrence
    order; generated IDs suffix the bindings deterministically. Rules without
    predicate variables pass through unchanged. An empty domain with a variable
    present, a non-string domain member, or a variable also used as a term all
    fail closed — open-world predicate invention is never implied.
    """
    if not isinstance(rule, GenericRule):
        raise ReasoningError("rule must be a GenericRule")
    try:
        predicates = tuple(dict.fromkeys(domain))
    except TypeError as exc:
        raise ReasoningError("domain must be iterable") from exc
    for name in predicates:
        _text(name, "domain predicate")
    names = tuple(dict.fromkeys(atom.predicate.name for atom in rule.body
                                if isinstance(atom.predicate, Variable)))
    if not names:
        return (rule,)
    if not predicates:
        raise ReasoningError("predicate variables require a non-empty domain")

    expanded = []
    for combo in itertools.product(predicates, repeat=len(names)):
        mapping = dict(zip(names, combo))
        suffix = ",".join(f"{name}={mapping[name]}" for name in names)

        def specialize(atom: TypedAtom) -> TypedAtom:
            if isinstance(atom.predicate, Variable):
                return TypedAtom(mapping[atom.predicate.name], atom.args)
            return atom

        expanded.append(GenericRule(f"{rule.rule_id}[{suffix}]",
                                    tuple(specialize(atom) for atom in rule.body),
                                    rule.filters,
                                    TypedAtom(rule.head.predicate, rule.head.args)))
    return tuple(expanded)


def _encoded_term(term):
    if isinstance(term, Variable):
        return ["variable", term.name]
    if isinstance(term, Literal):
        return ["literal", term.kind, term.lexical]
    return ["entity", term]


@dataclass(frozen=True)
class AggregateRule:
    """One non-recursive grouped aggregation stratum over a relational body.

    ``group`` lists grouping variables (possibly empty for a global aggregate).
    Each ``Aggregation`` consumes a body-bound ``source`` into a fresh
    ``target``; the head may use body variables and targets only. Heads define
    sink predicates: they must not occur in any rule body of the same program,
    and aggregate bodies must not reference other aggregate heads, keeping the
    aggregate stratum single-pass and termination-obvious. Recursive aggregates
    (``min``/``max``/``sum``/``count`` over their own output) are rejected.
    """

    rule_id: str
    body: tuple[TypedAtom, ...]
    group: tuple[Variable, ...]
    aggregations: tuple[Aggregation, ...]
    head: TypedAtom

    def __post_init__(self) -> None:
        _text(self.rule_id, "rule_id")
        for name in ("body", "group", "aggregations"):
            if not isinstance(getattr(self, name), (tuple, list)):
                raise ReasoningError(f"rule {name} must be a tuple or list")
        body, group, aggregations = tuple(self.body), tuple(self.group), tuple(self.aggregations)
        if not body or not all(isinstance(atom, TypedAtom) for atom in body):
            raise ReasoningError("rule body must contain at least one TypedAtom")
        if not all(isinstance(variable, Variable) for variable in group):
            raise ReasoningError("grouping keys must be Variables")
        if not aggregations or not all(isinstance(item, Aggregation) for item in aggregations):
            raise ReasoningError("rule aggregations must contain Aggregation values")
        if not isinstance(self.head, TypedAtom):
            raise ReasoningError("rule head must be a TypedAtom")
        if isinstance(self.head.predicate, Variable):
            raise ReasoningError("head predicates must be constant")
        for atom in body:
            if isinstance(atom.predicate, Variable):
                raise ReasoningError("predicate variables require explicit expansion")
        bound = frozenset().union(*(atom.variables for atom in body))
        if not set(group) <= bound:
            raise ReasoningError("grouping variables must occur in the relational body")
        targets = [item.target for item in aggregations]
        if len(set(targets)) != len(targets):
            raise ReasoningError("aggregation targets must be distinct")
        if any(target in bound for target in targets):
            raise ReasoningError("aggregation targets must be fresh variables")
        for item in aggregations:
            if item.source not in bound:
                raise ReasoningError("aggregation sources must occur in the relational body")
        allowed = bound | set(targets)
        head_variables = {term for term in self.head.args if isinstance(term, Variable)}
        if not head_variables <= allowed:
            raise ReasoningError("unsafe rule: head variables must be bound by the body or an aggregation")
        object.__setattr__(self, "body", body)
        object.__setattr__(self, "group", group)
        object.__setattr__(self, "aggregations", aggregations)


@dataclass(frozen=True)
class GenericProgram:
    """Validated rule catalog, canonically ordered by unique rule ID.

    Aggregate rules form one terminal stratum: their heads are sinks that no
    rule body may read, and their bodies may not read other aggregate heads.

    ``closed`` declares the complete predicates that negated atoms may query.
    Stratification assigns every headed predicate a rank: negative edges must
    point strictly downward, so evaluation completes lower strata before any
    anti-join reads them. Negative cycles fail at construction.
    """

    rules: tuple[GenericRule, ...] = ()
    aggregates: tuple[AggregateRule, ...] = ()
    closed: tuple[str, ...] = ()
    strata: tuple[tuple[str, ...], ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.rules, (tuple, list)):
            raise ReasoningError("program rules must be a tuple or list")
        if not isinstance(self.aggregates, (tuple, list)):
            raise ReasoningError("program aggregates must be a tuple or list")
        if not isinstance(self.closed, (tuple, list)):
            raise ReasoningError("program closed scope must be a tuple or list")
        rules = tuple(self.rules)
        aggregates = tuple(self.aggregates)
        closed = tuple(self.closed)
        if not all(isinstance(rule, GenericRule) for rule in rules):
            raise ReasoningError("program rules must contain only GenericRule values")
        if not all(isinstance(rule, AggregateRule) for rule in aggregates):
            raise ReasoningError("program aggregates must contain only AggregateRule values")
        for predicate in closed:
            _text(predicate, "closed predicate")
        if len({rule.rule_id for rule in (*rules, *aggregates)}) != len(rules) + len(aggregates):
            raise ReasoningError("program rule IDs must be unique")
        for rule in rules:
            for atom in rule.body:
                if isinstance(atom.predicate, Variable):
                    raise ReasoningError("unexpanded predicate variables; expand first")
            for atom in rule.negated:
                if atom.predicate not in closed:
                    raise ReasoningError(
                        f"negation over {atom.predicate!r} requires a closed-world declaration")
        heads = {rule.head.predicate for rule in aggregates}
        if heads & {atom.predicate for rule in rules for atom in (*rule.body, *rule.negated)}:
            raise ReasoningError("aggregate heads must be sink predicates unread by positive rules")
        if heads & {atom.predicate for rule in aggregates for atom in rule.body}:
            raise ReasoningError("aggregates must not read other aggregate heads (non-recursive only)")
        object.__setattr__(self, "rules", tuple(sorted(rules, key=lambda rule: rule.rule_id)))
        object.__setattr__(self, "aggregates", tuple(sorted(aggregates, key=lambda rule: rule.rule_id)))
        object.__setattr__(self, "closed", tuple(sorted(set(closed))))
        object.__setattr__(self, "strata", _stratify(rules))

    @property
    def digest(self) -> str:
        """Versioned structural identity; variable names and positions participate."""
        import hashlib
        import json

        payload = ["gestaltdb-generic-v3", list(self.closed), [
            [rule.rule_id,
             [[atom.predicate if isinstance(atom.predicate, str) else ["variable", atom.predicate.name],
               [_encoded_term(term) for term in atom.args]] for atom in rule.body],
             [[item.op, _encoded_term(item.left), _encoded_term(item.right)] for item in rule.filters],
             [[atom.predicate, [_encoded_term(term) for term in atom.args]] for atom in rule.negated],
             [rule.head.predicate, [_encoded_term(term) for term in rule.head.args]]]
            for rule in self.rules], [
            [rule.rule_id,
             [[atom.predicate, [_encoded_term(term) for term in atom.args]] for atom in rule.body],
             [variable.name for variable in rule.group],
             [[item.op, item.source.name, item.target.name] for item in rule.aggregations],
             [rule.head.predicate, [_encoded_term(term) for term in rule.head.args]]]
            for rule in self.aggregates]]
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _stratify(rules: tuple[GenericRule, ...]) -> tuple[tuple[str, ...], ...]:
    """Order positive predicate components so negative edges point downward.

    Components come from positive dependencies only (iterative Kosaraju, no
    recursion limit hazard). A negative edge inside one component fails closed
    immediately; remaining positive and negative dependencies form one
    must-precede DAG evaluated with deterministic Kahn order. A stalled
    schedule always contains a negative edge, since the positive subgraph is
    acyclic by construction.
    """
    graph: dict[str, set[str]] = {}
    negative: dict[str, set[str]] = {}
    for rule in rules:
        head = rule.head.predicate
        graph.setdefault(head, set())
        negative.setdefault(head, set())
        for atom in rule.body:
            graph.setdefault(atom.predicate, set()).add(head)
        for atom in rule.negated:
            negative[head].add(atom.predicate)
            graph.setdefault(atom.predicate, set())
    visited: set[str] = set()
    finished: list[str] = []
    for node in sorted(graph):
        if node in visited:
            continue
        visited.add(node)
        stack = [(node, iter(sorted(graph[node])))]
        while stack:
            current, children = stack[-1]
            child = next(children, None)
            if child is None:
                finished.append(current)
                stack.pop()
            elif child not in visited:
                visited.add(child)
                stack.append((child, iter(sorted(graph[child]))))
    visited.clear()
    components: list[tuple[str, ...]] = []
    owner: dict[str, int] = {}
    for node in reversed(finished):
        if node in visited:
            continue
        visited.add(node)
        members = [node]
        pending = [node]
        while pending:
            current = pending.pop()
            for parent, children in graph.items():
                if current in children and parent not in visited:
                    visited.add(parent)
                    members.append(parent)
                    pending.append(parent)
        for member in members:
            owner[member] = len(components)
        components.append(tuple(sorted(members)))
    for head, targets in negative.items():
        if owner[head] in {owner[target] for target in targets}:
            raise ReasoningError(
                f"negative dependency cycle: {head!r} negatively depends on its own stratum")
    before: dict[int, set[int]] = {index: set() for index in range(len(components))}
    for rule in rules:
        head = owner[rule.head.predicate]
        for atom in (*rule.body, *rule.negated):
            before[head].add(owner[atom.predicate])
    remaining = {index: set(deps) - {index} for index, deps in before.items()}
    ordered: list[int] = []
    ready = sorted([index for index, deps in remaining.items() if not deps],
                   key=lambda index: components[index])
    while ready:
        current = ready.pop(0)
        ordered.append(current)
        for index, deps in remaining.items():
            if current in deps:
                deps.discard(current)
                if not deps and index not in ordered and index not in ready:
                    ready.append(index)
        ready.sort(key=lambda index: components[index])
    if len(ordered) != len(components):
        stuck = [components[index] for index in range(len(components)) if index not in ordered]
        raise ReasoningError(
            f"negative dependency cycle involving {sorted({name for comp in stuck for name in comp})}")
    return tuple(components[index] for index in ordered)


def _digest_facts(facts) -> str:
    import hashlib
    import json

    payload = ["gestaltdb-fact-horizon-v1",
               sorted([fact.predicate, [_encoded_term(value) for value in fact.values]] for fact in facts)]
    return "sha256:" + hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _sort_key(fact: TypedFact):
    def value_key(value):
        if isinstance(value, Literal):
            return (1, value.kind, value.lexical)
        return (0, "", value)

    return (fact.predicate, tuple(value_key(value) for value in fact.values))


def _group_key(key: tuple) -> tuple:
    return tuple((1, value.kind, value.lexical) if isinstance(value, Literal) else (0, "", value)
                 for value in key)


@dataclass(frozen=True)
class GenericResult:
    """Complete closure in canonical order; evaluators never return partial runs."""

    facts: tuple[TypedFact, ...]
    iterations: int
    program_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.facts, (tuple, list)):
            raise ReasoningError("result facts must be a tuple or list")
        if not all(isinstance(fact, TypedFact) for fact in self.facts):
            raise ReasoningError("result facts must contain only TypedFact values")
        if isinstance(self.iterations, bool) or not isinstance(self.iterations, int) or self.iterations < 0:
            raise ReasoningError("iterations must be a non-negative integer")
        _text(self.program_digest, "program_digest")
        object.__setattr__(self, "facts", tuple(sorted(set(self.facts), key=_sort_key)))

    def __contains__(self, fact: object) -> bool:
        return isinstance(fact, TypedFact) and fact in set(self.facts)


@dataclass(frozen=True)
class NegationWitness:
    """One exact blocking presence check for a failed negated condition.

    The witness carries the closed-world scope and the horizon identity of the
    pinned input fact set — never a fabricated negative fact. ``position`` is
    the negated body occurrence; ``args`` identify the present blocking fact.
    ``outcome`` is always ``present``. Successful absence checks enabling a
    conclusion are not recorded by this why-not diagnostic.
    """

    rule_id: str
    position: int
    predicate: str
    args: tuple[str | Literal, ...]
    scope: tuple[str, ...]
    horizon: str
    outcome: str = "present"

    def __post_init__(self) -> None:
        _text(self.rule_id, "rule_id")
        if isinstance(self.position, bool) or not isinstance(self.position, int) or self.position < 0:
            raise ReasoningError("position must be a non-negative integer")
        _text(self.predicate, "predicate")
        if not isinstance(self.args, (tuple, list)):
            raise ReasoningError("args must be a tuple or list")
        for value in self.args:
            if not isinstance(value, (str, Literal)) or (isinstance(value, str) and not value):
                raise ReasoningError("witness args must be entities or literals")
        object.__setattr__(self, "args", tuple(self.args))
        if not isinstance(self.scope, (tuple, list)) or not self.scope:
            raise ReasoningError("scope must be a non-empty tuple of predicates")
        for predicate in self.scope:
            _text(predicate, "scope predicate")
        object.__setattr__(self, "scope", tuple(self.scope))
        _text(self.horizon, "horizon")
        if self.outcome != "present":
            raise ReasoningError("negation blocker witness outcome must be present")


@dataclass(frozen=True)
class StratifiedRun:
    """Stratified closure plus its component schedule and blocking witnesses."""

    result: GenericResult
    strata: tuple[tuple[str, ...], ...]
    component_rounds: tuple[int, ...]
    witnesses: tuple[NegationWitness, ...]
    truncated: bool

    def __post_init__(self) -> None:
        if not isinstance(self.result, GenericResult):
            raise ReasoningError("result must be a GenericResult")
        if not isinstance(self.strata, (tuple, list)) or not all(
                isinstance(component, (tuple, list)) and component
                and all(isinstance(name, str) and name for name in component)
                for component in self.strata):
            raise ReasoningError("strata must be non-empty predicate groups")
        object.__setattr__(self, "strata", tuple(tuple(component) for component in self.strata))
        if not isinstance(self.component_rounds, (tuple, list)) or not all(
                isinstance(count, int) and not isinstance(count, bool) and count >= 0
                for count in self.component_rounds):
            raise ReasoningError("component_rounds must be non-negative integers")
        if len(self.component_rounds) != len(self.strata):
            raise ReasoningError("component_rounds must cover every stratum")
        object.__setattr__(self, "component_rounds", tuple(self.component_rounds))
        if not isinstance(self.witnesses, (tuple, list)) or not all(
                isinstance(item, NegationWitness) for item in self.witnesses):
            raise ReasoningError("witnesses must contain only NegationWitness values")
        object.__setattr__(self, "witnesses", tuple(self.witnesses))
        if not isinstance(self.truncated, bool):
            raise ReasoningError("truncated must be a bool")


class _TypedIndex:
    def __init__(self, facts: Iterable[TypedFact] = ()) -> None:
        self.facts: set[TypedFact] = set()
        self.by_predicate: dict[str, list[TypedFact]] = defaultdict(list)
        self.by_position: dict[tuple[str, int, object], list[TypedFact]] = defaultdict(list)
        for fact in facts:
            self.add(fact)

    @staticmethod
    def _key(value):
        return ("literal", value.kind, value.lexical) if isinstance(value, Literal) else ("entity", value)

    def add(self, fact: TypedFact) -> None:
        if fact in self.facts:
            return
        self.facts.add(fact)
        self.by_predicate[fact.predicate].append(fact)
        for position, value in enumerate(fact.values):
            self.by_position[fact.predicate, position, self._key(value)].append(fact)

    def candidates(self, atom: TypedAtom, bindings: dict[Variable, object]) -> tuple[TypedFact, ...]:
        ground = [bindings.get(term, term) if isinstance(term, Variable) else term for term in atom.args]
        if all(not isinstance(value, Variable) for value in ground):
            exact = TypedFact(atom.predicate, tuple(ground))
            return (exact,) if exact in self.facts else ()
        buckets = [self.by_position[atom.predicate, position, self._key(value)]
                   for position, value in enumerate(ground) if not isinstance(value, Variable)]
        if not buckets:
            return tuple(self.by_predicate.get(atom.predicate, ()))
        return tuple(min(buckets, key=len))


def _unify(atom: TypedAtom, fact: TypedFact, bindings: dict) -> dict | None:
    if atom.predicate != fact.predicate or len(atom.args) != len(fact.values):
        return None
    updated = dict(bindings)
    for term, value in zip(atom.args, fact.values):
        if isinstance(term, Variable):
            previous = updated.get(term)
            if previous is not None and previous != value:
                return None
            updated[term] = value
        elif isinstance(term, Literal):
            if not isinstance(value, Literal) or not equal(term, value):
                return None
        elif term != value:
            return None
    return updated


class _TypedBudget:
    def __init__(self, limits: ReasoningLimits) -> None:
        self.limits = limits
        self.candidate_rows = 0

    def candidate(self) -> None:
        self.candidate_rows += 1
        if self.candidate_rows > self.limits.max_candidate_rows:
            raise ReasoningLimitError(
                f"evaluation exceeded max_candidate_rows={self.limits.max_candidate_rows}"
            )


def _ready(filters, bindings) -> bool:
    return all(item.holds(bindings) for item in filters
               if item.variables <= set(bindings))


class GenericEngine:
    """Stateless positive typed-Datalog evaluator with filter pruning.

    Each synchronous round joins at least one delta relation. Filters are
    checked incrementally as their variables bind, pruning before deeper
    traversal. Only complete closures are returned; limits and cancellation
    behave like the binary engine. Witness collection is not offered here.
    """

    def evaluate(
        self, program: GenericProgram, facts: Iterable[TypedFact | Evidence], *,
        limits: ReasoningLimits, collect_proofs: bool = False,
        cancel: Callable[[], bool] | None = None,
    ) -> GenericResult:
        if not isinstance(program, GenericProgram):
            raise ReasoningError("program must be a GenericProgram")
        if not isinstance(limits, ReasoningLimits):
            raise ReasoningError("limits must be ReasoningLimits")
        if collect_proofs:
            raise ReasoningError("generic evaluation does not collect witnesses")
        if any(rule.negated for rule in program.rules) or program.closed:
            raise ReasoningError(
                "scoped negation requires evaluate_stratified; plain evaluation stays positive")
        check_cancelled(cancel)
        if len(program.rules) > limits.max_rules:
            raise ReasoningLimitError(f"evaluation exceeded max_rules={limits.max_rules}")
        try:
            iterator = iter(facts)
        except TypeError as exc:
            raise ReasoningError("facts must be an iterable of TypedFact values") from exc
        initial: set[TypedFact] = set()
        for count, value in enumerate(iterator, 1):
            if count > limits.max_input_facts:
                raise ReasoningLimitError(f"evaluation exceeded max_input_facts={limits.max_input_facts}")
            if count % 1024 == 0:
                check_cancelled(cancel)
            fact = value.fact if isinstance(value, Evidence) else value
            if not isinstance(fact, TypedFact):
                raise ReasoningError("facts must contain only TypedFact values")
            initial.add(fact)
            if len(initial) > limits.max_facts:
                raise ReasoningLimitError(f"evaluation exceeded max_facts={limits.max_facts}")
        full = _TypedIndex(sorted(initial, key=_sort_key))
        delta = full
        del initial
        dispatch: dict[str, list[tuple[GenericRule, int]]] = defaultdict(list)
        for rule in program.rules:
            seen: set[TypedAtom] = set()
            for position, atom in enumerate(rule.body):
                if atom not in seen:
                    dispatch[atom.predicate].append((rule, position))
                    seen.add(atom)
        iterations = 0
        budget = _TypedBudget(limits)
        while delta.facts:
            check_cancelled(cancel)
            active = [(rule, position) for predicate in sorted(delta.by_predicate)
                      for rule, position in dispatch.get(predicate, ())
                      if all(atom.predicate in full.by_predicate for atom in rule.body)]
            if not active:
                break
            if iterations >= limits.max_iterations:
                raise ReasoningLimitError(f"evaluation exceeded max_iterations={limits.max_iterations}")
            added: set[TypedFact] = set()
            for rule, pivot in active:
                for bindings in self._matches(rule, pivot, full, delta, budget):
                    conclusion = TypedFact(rule.head.predicate, tuple(
                        bindings[term] if isinstance(term, Variable) else term for term in rule.head.args))
                    if conclusion in full.facts or conclusion in added:
                        continue
                    if len(full.facts) + len(added) >= limits.max_facts:
                        raise ReasoningLimitError(f"evaluation exceeded max_facts={limits.max_facts}")
                    added.add(conclusion)
            iterations += 1
            ordered = sorted(added, key=_sort_key)
            for fact in ordered:
                full.add(fact)
            delta = _TypedIndex(ordered)
        if program.aggregates:
            for fact in self._evaluate_aggregates(program, full, budget, cancel):
                if fact in full.facts:
                    continue
                if len(full.facts) >= limits.max_facts:
                    raise ReasoningLimitError(f"evaluation exceeded max_facts={limits.max_facts}")
                full.add(fact)
            iterations += 1
        return GenericResult(tuple(full.facts), iterations, program.digest)

    def evaluate_stratified(
        self, program: GenericProgram, facts: Iterable[TypedFact | Evidence], *,
        limits: ReasoningLimits, collect_negation_trace: bool = False,
        cancel: Callable[[], bool] | None = None,
    ) -> StratifiedRun:
        """Fixpoint per positive component in stratum order, then aggregates.

        Negated atoms anti-join against completed lower strata only; the trace
        records the first present blocker per failed binding when requested, bounded by
        ``max_proof_nodes``. ``iterations`` counts executed component rounds;
        the aggregate stratum adds one round when aggregates exist, matching
        ``evaluate`` accounting on positive programs.
        """
        if not isinstance(program, GenericProgram):
            raise ReasoningError("program must be a GenericProgram")
        if not isinstance(limits, ReasoningLimits):
            raise ReasoningError("limits must be ReasoningLimits")
        if not isinstance(collect_negation_trace, bool):
            raise ReasoningError("collect_negation_trace must be a bool")
        check_cancelled(cancel)
        if len(program.rules) > limits.max_rules:
            raise ReasoningLimitError(f"evaluation exceeded max_rules={limits.max_rules}")
        try:
            iterator = iter(facts)
        except TypeError as exc:
            raise ReasoningError("facts must be an iterable of TypedFact values") from exc
        initial: set[TypedFact] = set()
        for count, value in enumerate(iterator, 1):
            if count > limits.max_input_facts:
                raise ReasoningLimitError(f"evaluation exceeded max_input_facts={limits.max_input_facts}")
            if count % 1024 == 0:
                check_cancelled(cancel)
            fact = value.fact if isinstance(value, Evidence) else value
            if not isinstance(fact, TypedFact):
                raise ReasoningError("facts must contain only TypedFact values")
            initial.add(fact)
            if len(initial) > limits.max_facts:
                raise ReasoningLimitError(f"evaluation exceeded max_facts={limits.max_facts}")
        full = _TypedIndex(sorted(initial, key=_sort_key))
        del initial
        horizon = _digest_facts(full.facts)
        scope = tuple(sorted(program.closed))
        rounds, iterations, truncated = [], 0, False
        trace: list[NegationWitness] = []
        budget = _TypedBudget(limits)
        for component in program.strata:
            member = [rule for rule in program.rules if rule.head.predicate in component]
            dispatch: dict[str, list[tuple[GenericRule, int]]] = defaultdict(list)
            for rule in member:
                seen: set[TypedAtom] = set()
                for position, atom in enumerate(rule.body):
                    if atom not in seen:
                        dispatch[atom.predicate].append((rule, position))
                        seen.add(atom)
            delta = _TypedIndex(sorted(full.facts, key=_sort_key))
            local_rounds = 0
            while delta.facts:
                check_cancelled(cancel)
                active = [(rule, position) for predicate in sorted(delta.by_predicate)
                          for rule, position in dispatch.get(predicate, ())
                          if all(atom.predicate in full.by_predicate for atom in rule.body)]
                if not active:
                    break
                if iterations >= limits.max_iterations:
                    raise ReasoningLimitError("evaluation exceeded max_iterations (component delta rounds)")
                added: set[TypedFact] = set()
                for rule, pivot in active:
                    for bindings in self._matches_stratified(
                            rule, pivot, full, delta, budget, scope, horizon,
                            collect_negation_trace, trace, limits):
                        if bindings is None:
                            truncated = True
                            continue
                        conclusion = TypedFact(rule.head.predicate, tuple(
                            bindings[term] if isinstance(term, Variable) else term
                            for term in rule.head.args))
                        if conclusion in full.facts or conclusion in added:
                            continue
                        if len(full.facts) + len(added) >= limits.max_facts:
                            raise ReasoningLimitError(f"evaluation exceeded max_facts={limits.max_facts}")
                        added.add(conclusion)
                iterations += 1
                local_rounds += 1
                ordered = sorted(added, key=_sort_key)
                for fact in ordered:
                    full.add(fact)
                delta = _TypedIndex(ordered)
            rounds.append(local_rounds)
        if program.aggregates:
            for fact in self._evaluate_aggregates(program, full, budget, cancel):
                if fact in full.facts:
                    continue
                if len(full.facts) >= limits.max_facts:
                    raise ReasoningLimitError(f"evaluation exceeded max_facts={limits.max_facts}")
                full.add(fact)
            iterations += 1
        result = GenericResult(tuple(full.facts), iterations, program.digest)
        return StratifiedRun(result, program.strata, tuple(rounds), tuple(trace), truncated)

    def _matches_stratified(self, rule, pivot, full, delta, budget, scope, horizon,
                            collect_trace, trace, limits):
        remaining = tuple(index for index in range(len(rule.body)) if index != pivot)
        stack = [(dict(), remaining, pivot, iter(delta.candidates(rule.body[pivot], dict())))]
        while stack:
            bindings, remaining, position, candidates = stack[-1]
            try:
                fact = next(candidates)
            except StopIteration:
                stack.pop()
                continue
            budget.candidate()
            updated = _unify(rule.body[position], fact, bindings)
            if updated is None or not _ready(rule.filters, updated):
                continue
            if not remaining:
                # Range restriction guarantees bindings only after the whole
                # positive body, not after an arbitrary delta pivot.
                blocked, witness = self._check_negated(rule, updated, full, scope, horizon)
                if blocked:
                    if collect_trace:
                        if len(trace) >= limits.max_proof_nodes:
                            yield None
                        else:
                            trace.append(witness)
                    continue
                yield updated
                continue
            chosen, bucket = remaining[0], full.candidates(rule.body[remaining[0]], updated)
            for other in remaining[1:]:
                other_bucket = full.candidates(rule.body[other], updated)
                if len(other_bucket) < len(bucket):
                    chosen, bucket = other, other_bucket
                if not bucket:
                    break
            if bucket:
                stack.append((updated, tuple(index for index in remaining if index != chosen),
                              chosen, iter(bucket)))

    @staticmethod
    def _check_negated(rule, bindings, full, scope, horizon):
        for position, atom in enumerate(rule.negated):
            ground = tuple(bindings.get(term, term) if isinstance(term, Variable) else term
                           for term in atom.args)
            if any(isinstance(value, Variable) for value in ground):
                raise ReasoningError("negation evaluated with unbound variables")
            probe = TypedAtom(atom.predicate, [bindings.get(term, term) if isinstance(term, Variable) else term
                                               for term in atom.args])
            if full.candidates(probe, bindings):
                return True, NegationWitness(rule.rule_id, position, atom.predicate, ground, scope, horizon)
        return False, None

    def _evaluate_aggregates(self, program, full, budget, cancel):
        for rule in program.aggregates:
            check_cancelled(cancel)
            rows: set[tuple] = set()
            for pivot in range(len(rule.body)):
                for bindings in self._matches(rule, pivot, full, full, budget):
                    rows.add(tuple(bindings.get(term, term) if isinstance(term, Variable) else term
                                   for term in (*rule.group, *[item.source for item in rule.aggregations])))
            groups: dict[tuple, dict[str, list]] = {}
            width = len(rule.group)
            for row in rows:
                key = row[:width]
                groups.setdefault(key, []).append(row[width:])
            for key, members in sorted(groups.items(), key=lambda item: _group_key(item[0])):
                targets: dict = {}
                for index, item in enumerate(rule.aggregations):
                    values = [member[index] for member in members]
                    value = self._reduce(item.op, values)
                    if value is None:
                        break
                    targets[item.target] = value
                else:
                    scope = dict(zip(rule.group, key))
                    scope.update(targets)
                    head = tuple(scope.get(term, term) if isinstance(term, Variable) else term
                                 for term in rule.head.args)
                    yield TypedFact(rule.head.predicate, head)

    @staticmethod
    def _reduce(op, values):
        if op == "count":
            return Literal("integer", len(values))
        numbers = [value for value in values
                   if not (isinstance(value, Literal) and value.kind == "null")]
        if any(not isinstance(value, Literal) or value.kind not in ("integer", "float") for value in numbers):
            raise ReasoningError(f"{op} aggregation requires integer/float literals")
        if any(isinstance(value, Literal) and value.value != value.value for value in numbers):
            raise ReasoningError(f"{op} aggregation rejects NaN operands")
        if not numbers:
            return None
        if op == "sum":
            total = sum(value.value for value in numbers)
            return Literal("float", float(total)) if any(value.kind == "float" for value in numbers) else Literal("integer", total)
        best = numbers[0]
        for value in numbers[1:]:
            if compare(value, best) is None:
                raise ReasoningError(f"{op} aggregation requires mutually comparable values")
            if (order := compare(value, best)) and ((op == "min" and order < 0) or (op == "max" and order > 0)):
                best = value
        return best

    def _matches(self, rule, pivot, full, delta, budget):
        remaining = tuple(index for index in range(len(rule.body)) if index != pivot)
        stack = [(dict(), remaining, pivot, iter(delta.candidates(rule.body[pivot], dict())))]
        while stack:
            bindings, remaining, position, candidates = stack[-1]
            try:
                fact = next(candidates)
            except StopIteration:
                stack.pop()
                continue
            budget.candidate()
            updated = _unify(rule.body[position], fact, bindings)
            if updated is None or not _ready(getattr(rule, "filters", ()), updated):
                continue
            if not remaining:
                yield updated
                continue
            # Stable ties retain original body position, including repeated atoms.
            chosen, bucket = remaining[0], full.candidates(rule.body[remaining[0]], updated)
            for other in remaining[1:]:
                other_bucket = full.candidates(rule.body[other], updated)
                if len(other_bucket) < len(bucket):
                    chosen, bucket = other, other_bucket
                if not bucket:
                    break
            if bucket:
                stack.append((updated, tuple(index for index in remaining if index != chosen),
                              chosen, iter(bucket)))

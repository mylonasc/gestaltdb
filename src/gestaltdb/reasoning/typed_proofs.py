"""Derivation tracking for typed reasoning: premises, filters, absence, aggregates.

The binary engine keeps one witness per stored fact; the typed engine now
does the same. A ``TypedFactWitness`` records the exact rule, the positive
premise facts in body order, the grounded comparisons that held, the
absence checks that succeeded (stratified only, each naming its
closed-world scope), and the aggregate operations with their contributing
facts. ``TypedProofTrace`` keeps one witness per closure fact under explicit
budgets and answers ``explain`` with a bounded derivation DAG.

A trace is bound to one generation — program digest plus input-fact
horizon. Unknown facts explain to an explicitly truncated empty result.
Call ``is_current_for`` before treating a historical trace as current, and
reevaluate after program or input changes; ``explain`` alone does not consult
live inputs or automatically maintain support. Constructors check structural
consistency, not source authentication or independent closure correctness.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Iterable, Mapping, cast

from .model import ReasoningError, ReasoningLimits, Variable, _text
from .terms import Literal
from .typed import (
    AggregateRule,
    Comparison,
    GenericProgram,
    GenericRule,
    TypedFact,
    _digest_facts,
    _sort_key,
    _unify,
)


@dataclass(frozen=True)
class AbsenceCheck:
    """One successful absence probe: predicate, ground args, closed scope."""

    position: int
    predicate: str
    args: tuple[str | Literal, ...]
    scope: tuple[str, ...]

    def __post_init__(self) -> None:
        if isinstance(self.position, bool) or not isinstance(self.position, int) or self.position < 0:
            raise ReasoningError("absence position must be a non-negative integer")
        _text(self.predicate, "absence predicate")
        if not isinstance(self.args, (tuple, list)):
            raise ReasoningError("absence args must be a tuple or list")
        for value in self.args:
            if not isinstance(value, (str, Literal)) or (isinstance(value, str) and not value):
                raise ReasoningError("absence args must be entities or literals")
        object.__setattr__(self, "args", tuple(self.args))
        if not isinstance(self.scope, (tuple, list)) or not self.scope:
            raise ReasoningError("absence scope must be a non-empty tuple of predicates")
        for predicate in self.scope:
            _text(predicate, "scope predicate")
        object.__setattr__(self, "scope", tuple(self.scope))


@dataclass(frozen=True)
class AggregateDerivation:
    """One grouped aggregation behind a conclusion: op, group, evidence, result."""

    rule_id: str
    op: str
    target: str
    group: tuple[str | Literal, ...]
    operands: tuple[TypedFact, ...]
    result: Literal

    def __post_init__(self) -> None:
        _text(self.rule_id, "rule_id")
        if self.op not in ("count", "sum", "min", "max"):
            raise ReasoningError(f"aggregation op must be one of count/sum/min/max, got {self.op!r}")
        _text(self.target, "aggregation target")
        if not isinstance(self.group, (tuple, list)):
            raise ReasoningError("aggregation group must be a tuple or list")
        for value in self.group:
            if not isinstance(value, (str, Literal)) or (isinstance(value, str) and not value):
                raise ReasoningError("aggregation group must hold entities or literals")
        object.__setattr__(self, "group", tuple(self.group))
        if not isinstance(self.operands, (tuple, list)) or not self.operands:
            raise ReasoningError("aggregation operands must be non-empty")
        if not all(isinstance(item, TypedFact) for item in self.operands):
            raise ReasoningError("aggregation operands must contain only TypedFact values")
        object.__setattr__(self, "operands", tuple(sorted(set(self.operands), key=_sort_key)))
        if not isinstance(self.result, Literal):
            raise ReasoningError("aggregation result must be a Literal")


@dataclass(frozen=True)
class TypedFactWitness:
    """One recorded derivation: rule plus exact positive/filter/absence/aggregate evidence."""

    fact: TypedFact
    rule: GenericRule | AggregateRule | None
    premises: tuple[TypedFact, ...] = ()
    comparisons: tuple[Comparison, ...] = ()
    absence: tuple[AbsenceCheck, ...] = ()
    aggregates: tuple[AggregateDerivation, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.fact, TypedFact):
            raise ReasoningError("witness fact must be a TypedFact")
        for name in ("premises", "comparisons", "absence", "aggregates"):
            if not isinstance(getattr(self, name), (tuple, list)):
                raise ReasoningError(f"witness {name} must be a tuple or list")
        premises = tuple(self.premises)
        comparisons = tuple(self.comparisons)
        absence = tuple(self.absence)
        aggregates = tuple(self.aggregates)
        if not all(isinstance(item, TypedFact) for item in premises):
            raise ReasoningError("witness premises must contain only TypedFact values")
        if not all(isinstance(item, Comparison) for item in comparisons):
            raise ReasoningError("witness comparisons must contain only Comparison values")
        if not all(isinstance(item, AbsenceCheck) for item in absence):
            raise ReasoningError("witness absence must contain only AbsenceCheck values")
        if not all(isinstance(item, AggregateDerivation) for item in aggregates):
            raise ReasoningError("witness aggregates must contain only AggregateDerivation values")
        if self.rule is None:
            if premises or comparisons or absence or aggregates:
                raise ReasoningError("input witnesses cannot carry derivation evidence")
            object.__setattr__(self, "premises", premises)
            object.__setattr__(self, "comparisons", comparisons)
            object.__setattr__(self, "absence", absence)
            object.__setattr__(self, "aggregates", aggregates)
            return
        if not isinstance(self.rule, (GenericRule, AggregateRule)):
            raise ReasoningError("witness rule must be a GenericRule or AggregateRule")
        if aggregates:
            if not isinstance(self.rule, AggregateRule):
                raise ReasoningError("aggregate evidence needs an AggregateRule")
            if premises:
                raise ReasoningError("aggregate conclusions carry operands, not positional premises")
            if comparisons or absence:
                raise ReasoningError("aggregate witnesses cannot carry comparison or absence checks")
            if any(item.rule_id != self.rule.rule_id for item in aggregates):
                raise ReasoningError("aggregate evidence must reference its rule")
            self._check_aggregate_head(aggregates)
        else:
            if not isinstance(self.rule, GenericRule):
                raise ReasoningError("positional premises need a GenericRule")
            if len(premises) != len(self.rule.body):
                raise ReasoningError("derived witnesses need one premise per body atom")
            bindings = self._check_premises(premises)
            expected = tuple(Comparison(
                item.op,
                bindings[item.left] if isinstance(item.left, Variable) else item.left,
                bindings[item.right] if isinstance(item.right, Variable) else item.right)
                for item in self.rule.filters)
            if comparisons != expected:
                raise ReasoningError("witness comparisons must match every grounded rule filter")
            for check in comparisons:
                if not check.holds(bindings):
                    raise ReasoningError("witness comparison does not hold")
            if len(absence) != len(self.rule.negated):
                raise ReasoningError("witness absence checks must cover every rule negation")
            for position, probe in enumerate(absence):
                if probe.position != position:
                    raise ReasoningError("absence positions must cover rule negations in order")
                if probe.predicate not in probe.scope:
                    raise ReasoningError("absence scope must include the checked predicate")
                atom = self.rule.negated[probe.position]
                if atom.predicate != probe.predicate:
                    raise ReasoningError("absence predicate does not match its rule atom")
                ground = tuple(bindings.get(term, term) if not isinstance(term, (str, Literal))
                                else term for term in atom.args)
                if tuple(ground) != probe.args:
                    raise ReasoningError("absence args do not match the derived binding")
        object.__setattr__(self, "premises", premises)
        object.__setattr__(self, "comparisons", comparisons)
        object.__setattr__(self, "absence", absence)
        object.__setattr__(self, "aggregates", aggregates)

    def _check_premises(self, premises: tuple[TypedFact, ...]) -> dict:
        bindings: dict = {}
        rule = self.rule
        if not isinstance(rule, GenericRule):
            raise ReasoningError("positional premises need a GenericRule")
        for atom, premise in zip(rule.body, premises):
            if atom.predicate != premise.predicate or len(atom.args) != len(premise.values):
                raise ReasoningError("witness premise does not match its rule atom")
            updated = _unify(atom, premise, bindings)
            if updated is None:
                raise ReasoningError("witness premise bindings conflict")
            bindings = updated
        rule = self.rule
        if not isinstance(rule, GenericRule):
            raise ReasoningError("positional premises need a GenericRule")
        head = rule.head
        grounded = []
        for term in head.args:
            if isinstance(term, (str, Literal)):
                grounded.append(term)
            elif term in bindings:
                grounded.append(bindings[term])
            else:
                raise ReasoningError("witness rule does not derive its conclusion")
        if TypedFact(cast(str, head.predicate), cast("tuple[str | Literal, ...]", tuple(grounded))) != self.fact:
            raise ReasoningError("witness rule does not derive its conclusion")
        return bindings

    def _check_aggregate_head(self, aggregates: tuple[AggregateDerivation, ...]) -> None:
        rule = self.rule
        if not isinstance(rule, AggregateRule):
            raise ReasoningError("aggregate evidence needs an AggregateRule")
        group_names = [variable.name for variable in rule.group]
        if len(aggregates) != len(rule.aggregations):
            raise ReasoningError("aggregate evidence must cover every rule aggregation")
        for derivation, aggregation in zip(aggregates, rule.aggregations):
            if derivation.op != aggregation.op or derivation.target != aggregation.target.name:
                raise ReasoningError("aggregate operation and target must match the rule")
            if len(derivation.group) != len(group_names):
                raise ReasoningError("aggregate group does not match its rule")
            if derivation.group != aggregates[0].group or derivation.operands != aggregates[0].operands:
                raise ReasoningError("aggregate evidence must share one group and operand set")
        scope = dict(zip(group_names, aggregates[0].group)) if aggregates else {}
        targets = {item.target: item.result for item in aggregates}
        head = rule.head
        grounded = []
        for term in head.args:
            if isinstance(term, (str, Literal)):
                grounded.append(term)
            elif term.name in targets:
                grounded.append(targets[term.name])
            elif term.name in scope:
                grounded.append(scope[term.name])
            else:
                raise ReasoningError("aggregate witness leaves a head variable unbound")
        if TypedFact(cast(str, head.predicate), cast("tuple[str | Literal, ...]", tuple(grounded))) != self.fact:
            raise ReasoningError("aggregate witness does not derive its conclusion")


def _canonical_witnesses(nodes: tuple[TypedFactWitness, ...], truncated: bool) -> tuple[TypedFactWitness, ...]:
    if not isinstance(truncated, bool):
        raise ReasoningError("truncated must be a bool")
    if not isinstance(nodes, (tuple, list)) or not all(isinstance(node, TypedFactWitness) for node in nodes):
        raise ReasoningError("proof nodes must be TypedFactWitness values")
    by_fact = {node.fact: node for node in nodes}
    if len(by_fact) != len(nodes):
        raise ReasoningError("proof facts must be unique")
    known = set(by_fact)
    if not truncated:
        for node in nodes:
            for premise in (*node.premises, *(item for derivation in node.aggregates
                                               for item in derivation.operands)):
                if premise not in known:
                    raise ReasoningError("untruncated proofs must include every premise")
    dependents: dict[TypedFact, list[TypedFact]] = defaultdict(list)
    waiting: dict[TypedFact, int] = {}
    for node in nodes:
        parents = {premise for premise in
                   (*node.premises, *(item for derivation in node.aggregates
                                       for item in derivation.operands)) if premise in known}
        waiting[node.fact] = len(parents)
        for parent in parents:
            dependents[parent].append(node.fact)
    ready = sorted([fact for fact, count in waiting.items() if count == 0], key=_sort_key)
    ordered: list[TypedFactWitness] = []
    while ready:
        fact = ready.pop(0)
        ordered.append(by_fact[fact])
        for child in sorted(dependents[fact], key=_sort_key):
            waiting[child] -= 1
            if waiting[child] == 0:
                ready.append(child)
        ready.sort(key=_sort_key)
    if len(ordered) != len(nodes):
        raise ReasoningError("proof graph must be acyclic")
    return tuple(ordered)


@dataclass(frozen=True)
class TypedExplanation:
    """Reachable derivation subgraph, not a claim of complete support."""

    root: TypedFact
    nodes: tuple[TypedFactWitness, ...]
    truncated: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.root, TypedFact):
            raise ReasoningError("explanation root must be a TypedFact")
        nodes = _canonical_witnesses(self.nodes, self.truncated)
        if self.root not in {node.fact for node in nodes} and (nodes or not self.truncated):
            raise ReasoningError("explanation must include its root unless no witness was stored")
        object.__setattr__(self, "nodes", nodes)


@dataclass(frozen=True)
class TypedProofTrace:
    """Canonical one-witness-per-fact record bound to one generation."""

    program: GenericProgram
    nodes: tuple[TypedFactWitness, ...]
    horizon: str
    truncated: bool = False
    _by_fact: Mapping[TypedFact, TypedFactWitness] = field(init=False, repr=False, compare=False, hash=False)

    def __post_init__(self) -> None:
        if not isinstance(self.program, GenericProgram):
            raise ReasoningError("proof program must be a GenericProgram")
        nodes = _canonical_witnesses(self.nodes, self.truncated)
        rules = {rule.rule_id: rule for rule in (*self.program.rules, *self.program.aggregates)}
        if any(node.rule is not None and node.rule != rules.get(node.rule.rule_id) for node in nodes):
            raise ReasoningError("proof rules must belong to their program")
        known = {node.fact for node in nodes}
        scope = tuple(sorted(self.program.closed))
        for node in nodes:
            for probe in node.absence:
                if probe.scope != scope:
                    raise ReasoningError("absence scope must match the proof program")
                if TypedFact(probe.predicate, probe.args) in known:
                    raise ReasoningError("absence check is contradicted by a proof fact")
        _text(self.horizon, "horizon")
        object.__setattr__(self, "nodes", nodes)
        object.__setattr__(self, "_by_fact", MappingProxyType({node.fact: node for node in nodes}))

    def is_current_for(self, program: GenericProgram, facts: Iterable[TypedFact]) -> bool:
        """Generation check: same program digest and same input horizon."""
        if not isinstance(program, GenericProgram):
            raise ReasoningError("program must be a GenericProgram")
        try:
            known = list(facts)
        except TypeError as exc:
            raise ReasoningError("facts must be iterable") from exc
        if not all(isinstance(item, TypedFact) for item in known):
            raise ReasoningError("facts must contain only TypedFact values")
        return program.digest == self.program.digest and _digest_facts(known) == self.horizon

    def explain(self, fact: TypedFact, *, max_nodes: int = 1000) -> TypedExplanation:
        if not isinstance(fact, TypedFact):
            raise ReasoningError("explanation root must be a TypedFact")
        if isinstance(max_nodes, bool) or not isinstance(max_nodes, int) or max_nodes < 1:
            raise ReasoningError("max_nodes must be a positive integer")
        if fact not in self._by_fact:
            return TypedExplanation(fact, (), True)
        queued = {fact}
        queue = deque([fact])
        nodes = []
        truncated = self.truncated
        while queue:
            node = self._by_fact[queue.popleft()]
            nodes.append(node)
            for premise in (*node.premises, *(item for derivation in node.aggregates
                                               for item in derivation.operands)):
                if premise in queued:
                    continue
                if premise not in self._by_fact or len(queued) >= max_nodes:
                    truncated = True
                else:
                    queued.add(premise)
                    queue.append(premise)
        return TypedExplanation(fact, tuple(nodes), truncated)


class _TypedProofCollector:
    """Invocation-local bounded storage; closure evaluation never waits for it."""

    def __init__(self, program: GenericProgram, limits: ReasoningLimits) -> None:
        self.program = program
        self.limits = limits
        self.nodes: list[TypedFactWitness] = []
        self.edges = 0
        self.truncated = False
        self._inputs: set[TypedFact] = set()

    def input(self, fact: TypedFact) -> None:
        self._inputs.add(fact)

    def start(self) -> None:
        ordered = sorted(self._inputs, key=_sort_key)
        if len(ordered) > self.limits.max_proof_nodes:
            self.truncated = True
            ordered = ordered[:self.limits.max_proof_nodes]
        for fact in ordered:
            self.nodes.append(TypedFactWitness(fact, None))
        self._inputs.clear()

    def derived(self, fact: TypedFact, rule, premises: tuple[TypedFact, ...],
                comparisons: tuple[Comparison, ...], absence: tuple[AbsenceCheck, ...],
                aggregates: tuple[AggregateDerivation, ...]) -> None:
        cost = (len(premises) + len(comparisons) + len(absence)
                + sum(len(item.operands) for item in aggregates))
        if len(self.nodes) >= self.limits.max_proof_nodes or self.edges + cost > self.limits.max_proof_edges:
            self.truncated = True
            return
        self.nodes.append(TypedFactWitness(fact, rule, premises, comparisons, absence, aggregates))
        self.edges += cost

    def finish(self, horizon: str) -> TypedProofTrace:
        return TypedProofTrace(self.program, tuple(self.nodes), horizon, self.truncated)

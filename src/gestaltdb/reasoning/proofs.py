"""Bounded, immutable single-witness DAGs, independent of ontology and storage.

Nodes are fact witnesses; each derived witness retains its exact rule and ordered
premises. Input nodes may retain one canonical opaque evidence reference. No
enumeration of alternative proofs or support-count maintenance is attempted.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from heapq import heapify, heappop, heappush, heapreplace
from types import MappingProxyType
from typing import Mapping

from .model import Fact, Program, ReasoningError, ReasoningLimits, Rule, Variable, _text


@dataclass(frozen=True)
class FactWitness:
    fact: Fact
    rule: Rule | None = None
    premises: tuple[Fact, ...] = ()
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.fact, Fact):
            raise ReasoningError("witness fact must be a Fact")
        if not isinstance(self.premises, (tuple, list)) or not all(isinstance(p, Fact) for p in self.premises):
            raise ReasoningError("witness premises must be Fact values")
        if not isinstance(self.evidence, (tuple, list)) or len(self.evidence) > 1:
            raise ReasoningError("a witness retains at most one evidence reference")
        for reference in self.evidence:
            _text(reference, "evidence reference")
        object.__setattr__(self, "premises", tuple(self.premises))
        object.__setattr__(self, "evidence", tuple(self.evidence))
        if self.rule is None:
            if self.premises:
                raise ReasoningError("input witnesses cannot have premises")
            return
        if not isinstance(self.rule, Rule) or len(self.premises) != len(self.rule.body) or self.evidence:
            raise ReasoningError("derived witnesses need one premise per body atom and no input reference")
        bindings = {}
        for atom, premise in zip(self.rule.body, self.premises):
            if atom.predicate != premise.predicate:
                raise ReasoningError("witness premise does not match its rule")
            for term, value in ((atom.subject, premise.subject), (atom.object, premise.object)):
                if isinstance(term, Variable):
                    if term in bindings and bindings[term] != value:
                        raise ReasoningError("witness premise bindings conflict")
                    bindings[term] = value
                elif term != value:
                    raise ReasoningError("witness premise does not match its rule")
        head = self.rule.head
        subject = bindings[head.subject] if isinstance(head.subject, Variable) else head.subject
        object_value = bindings[head.object] if isinstance(head.object, Variable) else head.object
        if self.fact != Fact(subject, head.predicate, object_value):
            raise ReasoningError("witness rule does not derive its conclusion")


def _canonical_nodes(nodes: tuple[FactWitness, ...], truncated: bool) -> tuple[FactWitness, ...]:
    if not isinstance(truncated, bool):
        raise ReasoningError("truncated must be a bool")
    if not isinstance(nodes, (tuple, list)) or not all(isinstance(node, FactWitness) for node in nodes):
        raise ReasoningError("proof nodes must be FactWitness values")
    by_fact = {node.fact: node for node in nodes}
    if len(by_fact) != len(nodes):
        raise ReasoningError("proof facts must be unique")
    waiting = {}
    dependents = defaultdict(list)
    for node in nodes:
        parents = set(node.premises)
        if not truncated and not parents <= by_fact.keys():
            raise ReasoningError("untruncated proofs must include all premises")
        stored = parents & by_fact.keys()
        waiting[node.fact] = len(stored)
        for parent in stored:
            dependents[parent].append(node.fact)
    ready = [fact for fact, count in waiting.items() if count == 0]
    heapify(ready)
    ordered = []
    while ready:
        fact = heappop(ready)
        ordered.append(by_fact[fact])
        for child in dependents[fact]:
            waiting[child] -= 1
            if waiting[child] == 0:
                heappush(ready, child)
    if len(ordered) != len(nodes):
        raise ReasoningError("proof graph must be acyclic")
    return tuple(ordered)


@dataclass(frozen=True)
class Explanation:
    """Reachable witness subgraph, not a claim of complete alternative support."""

    root: Fact
    nodes: tuple[FactWitness, ...]
    truncated: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.root, Fact):
            raise ReasoningError("explanation root must be a Fact")
        nodes = _canonical_nodes(self.nodes, self.truncated)
        if self.root not in {node.fact for node in nodes} and (nodes or not self.truncated):
            raise ReasoningError("explanation must include its root unless no witness was stored")
        object.__setattr__(self, "nodes", nodes)


@dataclass(frozen=True)
class ProofTrace:
    """Canonical DAG retaining one witness per stored fact.

    ``truncated`` reports any storage omission, conservatively propagated to
    every explanation. It does not mean logical closure is incomplete.
    """

    program: Program
    nodes: tuple[FactWitness, ...]
    truncated: bool = False
    _by_fact: Mapping[Fact, FactWitness] = field(init=False, repr=False, compare=False, hash=False)

    def __post_init__(self) -> None:
        if not isinstance(self.program, Program):
            raise ReasoningError("proof program must be a Program")
        nodes = _canonical_nodes(self.nodes, self.truncated)
        rules = set(self.program.rules)
        if any(node.rule is not None and node.rule not in rules for node in nodes):
            raise ReasoningError("proof rules must belong to their program")
        object.__setattr__(self, "nodes", nodes)
        object.__setattr__(self, "_by_fact", MappingProxyType({node.fact: node for node in nodes}))

    def explain(self, fact: Fact, *, max_nodes: int = 1000) -> Explanation:
        if not isinstance(fact, Fact):
            raise ReasoningError("explanation root must be a Fact")
        if isinstance(max_nodes, bool) or not isinstance(max_nodes, int) or max_nodes < 1:
            raise ReasoningError("max_nodes must be a positive integer")
        if fact not in self._by_fact:
            return Explanation(fact, (), True)
        queued = {fact}
        queue = deque([fact])
        nodes = []
        truncated = self.truncated
        while queue:
            node = self._by_fact[queue.popleft()]
            nodes.append(node)
            for premise in node.premises:
                if premise in queued:
                    continue
                if premise not in self._by_fact or len(queued) >= max_nodes:
                    truncated = True
                else:
                    queued.add(premise)
                    queue.append(premise)
        return Explanation(fact, nodes, truncated)


@dataclass(frozen=True)
class _LargestFact:
    fact: Fact

    def __lt__(self, other: _LargestFact) -> bool:
        return self.fact > other.fact


class _ProofCollector:
    """Invocation-local bounded storage; canonical input selection is streaming."""

    def __init__(self, program: Program, limits: ReasoningLimits) -> None:
        self.program = program
        self.limits = limits
        self.nodes: list[FactWitness] = []
        self.edges = 0
        self.truncated = False
        self._inputs: dict[Fact, str | None] = {}
        self._largest: list[_LargestFact] = []

    def input(self, fact: Fact, reference: str | None) -> None:
        if fact in self._inputs:
            previous = self._inputs[fact]
            if reference is not None and (previous is None or reference < previous):
                self._inputs[fact] = reference
            return
        if len(self._inputs) < self.limits.max_proof_nodes:
            heappush(self._largest, _LargestFact(fact))
        else:
            self.truncated = True
            if fact >= self._largest[0].fact:
                return
            removed = heapreplace(self._largest, _LargestFact(fact))
            del self._inputs[removed.fact]
        self._inputs[fact] = reference

    def start(self) -> None:
        for fact, reference in sorted(self._inputs.items()):
            evidence = ()
            if reference is not None:
                if self.edges < self.limits.max_proof_edges:
                    evidence = (reference,)
                    self.edges += 1
                else:
                    self.truncated = True
            self.nodes.append(FactWitness(fact, evidence=evidence))
        self._inputs.clear()
        self._largest.clear()

    def derived(self, fact: Fact, rule: Rule, premises: list[Fact | None] | None) -> None:
        if (len(self.nodes) >= self.limits.max_proof_nodes or premises is None
                or self.edges + len(premises) > self.limits.max_proof_edges):
            self.truncated = True
            return
        self.nodes.append(FactWitness(fact, rule, tuple(premises)))
        self.edges += len(premises)

    def can_record(self, rule: Rule) -> bool:
        return (len(self.nodes) < self.limits.max_proof_nodes
                and self.edges + len(rule.body) <= self.limits.max_proof_edges)

    def finish(self) -> ProofTrace:
        return ProofTrace(self.program, self.nodes, self.truncated)

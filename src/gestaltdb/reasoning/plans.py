"""Reusable, context-bound positive-SCC plans and opt-in indexed execution.

Only standard-library/inward contracts. The existing evaluator remains default.
Plans contain no facts, sources, clocks or mutable run state. Row estimates are
actual current index-bucket cardinalities, not a calibrated cost optimizer.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from heapq import heappop, heappush
from typing import Callable, Iterable, cast

from .engine import _Budget, _Index, _matches, _value
from .model import EvaluationResult, Fact, Program, ReasoningError, ReasoningLimitError, ReasoningLimits, Variable, check_cancelled
from .proofs import _ProofCollector
from .protocols import Evidence
from .relational import CompilationIdentity, _digest


@dataclass(frozen=True)
class PredicateComponent:
    predicates: tuple[str, ...]
    rule_ids: tuple[str, ...]
    recursive: bool


def _components(program):
    graph, reverse = defaultdict(set), defaultdict(set)
    for rule in program.rules:
        head = rule.head.predicate
        graph[head]
        for atom in rule.body:
            graph[atom.predicate].add(head)
            reverse[head].add(atom.predicate)
    # Iterative Kosaraju: long hierarchies cannot overflow Python recursion.
    visited, finished = set(), []
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
    visited, components = set(), []
    for node in reversed(finished):
        if node in visited:
            continue
        visited.add(node)
        members, pending = [], [node]
        while pending:
            current = pending.pop()
            members.append(current)
            for parent in sorted(reverse[current]):
                if parent not in visited:
                    visited.add(parent)
                    pending.append(parent)
        components.append(tuple(sorted(members)))
    owner = {name: index for index, members in enumerate(components) for name in members}
    successors = [set() for _ in components]
    indegree = [0] * len(components)
    for parent, children in graph.items():
        for child in children:
            left, right = owner[parent], owner[child]
            if left != right and right not in successors[left]:
                successors[left].add(right)
                indegree[right] += 1
    ready = []
    for index, degree in enumerate(indegree):
        if not degree:
            heappush(ready, (components[index], index))
    rules_by_head = defaultdict(list)
    for rule in program.rules:
        rules_by_head[owner[rule.head.predicate]].append(rule.rule_id)
    ordered = []
    while ready:
        members, index = heappop(ready)
        recursive = len(members) > 1 or any(name in graph[name] for name in members)
        ordered.append(PredicateComponent(members, tuple(rules_by_head[index]), recursive))
        for child in successors[index]:
            indegree[child] -= 1
            if not indegree[child]:
                heappush(ready, (components[child], child))
    return tuple(ordered)


def _access_candidates(atom):
    subject, object_value = isinstance(atom.subject, Variable), isinstance(atom.object, Variable)
    if not subject and not object_value:
        return ["exact-membership"]
    if not subject:
        return ["subject-index", "exact-membership"]
    if not object_value:
        return ["object-index", "exact-membership"]
    if atom.subject == atom.object:
        return ["predicate-scan", "exact-membership"]
    return ["predicate-scan", "subject-index", "object-index", "exact-membership"]


@dataclass(frozen=True)
class PreparedProgram:
    """Immutable source program, exact compatibility key and compiled delta sites.

    Compilation bounds count rules/atom occurrences, not wall time or RSS.
    Source authentication is still the caller/adapter's responsibility.
    """

    program: Program
    identity: CompilationIdentity
    strategy: str = "indexed-smallest-bucket"
    max_rules: int = field(default=10_000, repr=False, compare=False)
    max_atoms: int = field(default=100_000, repr=False, compare=False)
    components: tuple[PredicateComponent, ...] = field(init=False)
    occurrences: tuple[tuple[str, int, str], ...] = field(init=False)
    schedules: tuple[tuple[tuple[str, tuple[tuple[str, int], ...]], ...], ...] = field(init=False, repr=False)
    _plan_digest: str = field(init=False, repr=False)

    def __post_init__(self):
        if not isinstance(self.program, Program) or not isinstance(self.identity, CompilationIdentity):
            raise ReasoningError("Program and CompilationIdentity required")
        if self.identity.program_digest != self.program.digest:
            raise ReasoningError("compilation identity must bind this program")
        if (self.identity.semantics_profile != "positive-binary-entity-v1"
                or self.identity.executor_version != "indexed-scc-v1"
                or self.strategy != "indexed-smallest-bucket"):
            raise ReasoningError("unsupported plan profile, executor or strategy")
        for name in ("max_rules", "max_atoms"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ReasoningError(f"{name} must be a positive integer")
        if len(self.program.rules) > self.max_rules:
            raise ReasoningLimitError("plan max_rules exceeded")
        count = 0
        occurrences = []
        for rule in self.program.rules:
            count += len(rule.body) + 1
            if count > self.max_atoms:
                raise ReasoningLimitError("plan max_atoms exceeded")
            seen = set()
            for position, atom in enumerate(rule.body):
                if atom not in seen:
                    occurrences.append((rule.rule_id, position, atom.predicate))
                    seen.add(atom)
        components = _components(self.program)
        object.__setattr__(self, "components", components)
        object.__setattr__(self, "occurrences", tuple(occurrences))
        owner = {rule_id: index for index, component in enumerate(components) for rule_id in component.rule_ids}
        schedules = [defaultdict(list) for _ in components]
        for rule_id, position, predicate in occurrences:
            schedules[owner[rule_id]][predicate].append((rule_id, position))
        object.__setattr__(self, "schedules", tuple(
            tuple((predicate, tuple(sites)) for predicate, sites in sorted(schedule.items()))
            for schedule in schedules))
        object.__setattr__(self, "_plan_digest", _digest([
            "gestaltdb-indexed-plan-v1", self.identity.digest, self.strategy,
            [[list(c.predicates), list(c.rule_ids), c.recursive] for c in components], occurrences]))

    @property
    def digest(self):
        return self._plan_digest

    def explain_plan(self):
        occurrences = frozenset(self.occurrences)
        return {"schema_version": "gestaltdb-indexed-plan-v1", "kind": "prepared-only",
                "plan_digest": self.digest, "identity": self.identity.digest,
                "program_digest": self.program.digest, "strategy": self.strategy,
                "cost_model": "heuristic-current-bucket-cardinality",
                "join_order": "dynamic-smallest-bound-bucket",
                "parallel": False, "physical_operators": ["predicate-scan", "endpoint-index-nested-loop", "exact-hash-membership"],
                "components": [{"predicates": list(c.predicates), "rule_ids": list(c.rule_ids),
                                "recursive": c.recursive} for c in self.components],
                "rules": [{"rule_id": r.rule_id, "body": [
                    {"position": position, "predicate": atom.predicate,
                     "access_candidates": _access_candidates(atom),
                     "delta_candidate": (r.rule_id, position, atom.predicate) in occurrences,
                     "estimated_rows": None, "actual_rows": None}
                    for position, atom in enumerate(r.body)]} for r in self.program.rules]}


@dataclass(frozen=True, order=True)
class OperatorObservation:
    rule_id: str
    body_position: int
    source: str
    access_path: str
    lookups: int
    estimated_rows: int
    actual_rows: int
    matched_rows: int


def _path(atom, bindings):
    subject, object_value = _value(atom.subject, bindings), _value(atom.object, bindings)
    if subject is not None and object_value is not None:
        return "exact-membership"
    if subject is not None:
        return "subject-index"
    if object_value is not None:
        return "object-index"
    return "predicate-scan"


class _Observer:
    def __init__(self):
        # One aggregate per program body site/source/access path, never per row.
        self.rows = defaultdict(lambda: [0, 0, 0, 0])

    def _key(self, rule, position, delta, bindings):
        return rule.rule_id, position, "delta" if delta else "stable", _path(rule.body[position], bindings)

    def lookup(self, rule, position, delta, bindings, candidates):
        row = self.rows[self._key(rule, position, delta, bindings)]
        row[0] += 1
        row[1] += len(candidates)

    def attempt(self, rule, position, delta, bindings, matched):
        row = self.rows[self._key(rule, position, delta, bindings)]
        row[2] += 1
        row[3] += matched

    def finish(self):
        return tuple(OperatorObservation(*key, *values) for key, values in sorted(self.rows.items()))


@dataclass(frozen=True)
class RelationStatistics:
    predicate: str
    rows: int
    distinct_subjects: int
    distinct_objects: int
    largest_subject_bucket: int
    largest_object_bucket: int


def _statistics(index):
    subjects, objects, subject_max, object_max = (defaultdict(int) for _ in range(4))
    for (predicate, _), bucket in index.by_subject.items():
        subjects[predicate] += 1
        subject_max[predicate] = max(subject_max[predicate], len(bucket))
    for (predicate, _), bucket in index.by_object.items():
        objects[predicate] += 1
        object_max[predicate] = max(object_max[predicate], len(bucket))
    return tuple(RelationStatistics(p, len(rows), subjects[p], objects[p], subject_max[p], object_max[p])
                 for p, rows in sorted(index.by_predicate.items()))


@dataclass(frozen=True)
class PlannedRun:
    plan: PreparedProgram
    result: EvaluationResult
    operators: tuple[OperatorObservation, ...]
    component_rounds: tuple[int, ...]
    emitted_facts: tuple[tuple[str, int], ...]
    input_statistics: tuple[RelationStatistics, ...]
    output_statistics: tuple[RelationStatistics, ...]
    candidate_rows: int
    telemetry_enabled: bool

    def explain_plan(self):
        description = self.plan.explain_plan()
        description["kind"] = "executed"
        if self.telemetry_enabled:
            sites = defaultdict(list)
            for observation in self.operators:
                sites[observation.rule_id, observation.body_position].append(observation)
            for rule in description["rules"]:
                for atom in rule["body"]:
                    observations = sites[rule["rule_id"], atom["position"]]
                    atom.update(estimated_rows=sum(o.estimated_rows for o in observations),
                                actual_rows=sum(o.actual_rows for o in observations),
                                matched_rows=sum(o.matched_rows for o in observations),
                                selected_indexes=sorted({o.access_path for o in observations}))
        description["execution"] = {
            "iterations_unit": "component-delta-rounds", "component_rounds": list(self.component_rounds),
            "candidate_rows": self.candidate_rows, "telemetry_enabled": self.telemetry_enabled,
            "emitted_facts": dict(self.emitted_facts),
            "input_relations": [{name: getattr(stats, name) for name in stats.__dataclass_fields__}
                                for stats in self.input_statistics],
            "output_relations": [{name: getattr(stats, name) for name in stats.__dataclass_fields__}
                                 for stats in self.output_statistics],
            "operators": [{name: getattr(observation, name) for name in observation.__dataclass_fields__}
                          for observation in self.operators],
        }
        return description


class PlannedDatalogEngine:
    """Opt-in SCC scheduler, sharing the default indexed matcher and proof logic.

    Iteration limits/counts are total executed component delta rounds, not the
    default global synchronous rounds. Witness choice can differ by schedule,
    but premise body positions, validity and deterministic replay are preserved.
    """

    def evaluate(self, plan: PreparedProgram, facts: Iterable[Fact | Evidence], *,
                 identity: CompilationIdentity, limits: ReasoningLimits,
                 collect_proofs: bool = False, collect_telemetry: bool = False,
                 cancel: Callable[[], bool] | None = None) -> PlannedRun:
        if not isinstance(plan, PreparedProgram) or not isinstance(limits, ReasoningLimits):
            raise ReasoningError("PreparedProgram and ReasoningLimits required")
        plan.identity.require_compatible(identity)
        if not isinstance(collect_proofs, bool):
            raise ReasoningError("collect_proofs must be a bool")
        if not isinstance(collect_telemetry, bool):
            raise ReasoningError("collect_telemetry must be a bool")
        check_cancelled(cancel)
        program = plan.program
        if len(program.rules) > limits.max_rules:
            raise ReasoningLimitError("max_rules exceeded")
        try:
            iterator = iter(facts)
        except TypeError as exc:
            raise ReasoningError("facts must be iterable") from exc
        initial = set()
        collector = _ProofCollector(program, limits) if collect_proofs else None
        for count, value in enumerate(iterator, 1):
            if count > limits.max_input_facts:
                raise ReasoningLimitError("max_input_facts exceeded")
            if count % 1024 == 0:
                check_cancelled(cancel)
            fact = value.fact if isinstance(value, Evidence) else value
            if not isinstance(fact, Fact):
                raise ReasoningError("facts must contain Fact or Evidence")
            initial.add(fact)
            if len(initial) > limits.max_facts:
                raise ReasoningLimitError("max_facts exceeded")
            if collector is not None:
                collector.input(fact, value.reference_id if isinstance(value, Evidence) else None)
        if collector is not None:
            collector.start()
        full = _Index(sorted(initial))
        del initial
        input_statistics = _statistics(full) if collect_telemetry else ()
        budget = _Budget(limits)
        observer = _Observer() if collect_telemetry else None
        rules = {rule.rule_id: rule for rule in program.rules}
        rounds: list[int] = []
        emitted: dict[str, int] = defaultdict(int)
        iterations = 0
        for component, schedule in zip(plan.components, plan.schedules):
            if not component.rule_ids:
                rounds.append(0)
                continue
            dispatch = {predicate: tuple((rules[rule_id], position) for rule_id, position in sites)
                        for predicate, sites in schedule}
            # External SCC predecessors are already complete. All stable facts
            # seed the first pass; later passes use only this component's delta.
            delta, local_rounds = full, 0
            while delta.facts:
                check_cancelled(cancel)
                active = [(rule, position) for predicate in sorted(dispatch.keys() & delta.by_predicate.keys())
                          for rule, position in dispatch.get(predicate, ())
                          if all(atom.predicate in full.by_predicate for atom in rule.body)]
                if not active:
                    break
                if iterations >= limits.max_iterations:
                    raise ReasoningLimitError("max_iterations exceeded (component delta rounds)")
                added: set[Fact] = set()
                for rule, pivot in active:
                    track = collector is not None and collector.can_record(rule)
                    for bindings, premises in _matches(rule, pivot, full, delta, budget,
                                                       track_premises=track, observer=observer):
                        conclusion = Fact(cast(str, _value(rule.head.subject, bindings)), rule.head.predicate,
                                          cast(str, _value(rule.head.object, bindings)))
                        if conclusion in full.facts or conclusion in added:
                            continue
                        if len(full.facts) + len(added) >= limits.max_facts:
                            raise ReasoningLimitError("max_facts exceeded")
                        added.add(conclusion)
                        emitted[rule.rule_id] += 1
                        if collector is not None:
                            collector.derived(conclusion, rule, premises)
                ordered = sorted(added)
                for fact in ordered:
                    full.add(fact)
                delta = _Index(ordered)
                iterations += 1
                local_rounds += 1
            rounds.append(local_rounds)
        result = EvaluationResult(tuple(full.facts), iterations, program.digest,
                                  collector.finish() if collector is not None else None)
        return PlannedRun(plan, result, observer.finish() if observer is not None else (),
                          tuple(rounds), tuple(sorted(emitted.items())), input_statistics,
                          _statistics(full) if collect_telemetry else (), budget.candidate_rows, collect_telemetry)

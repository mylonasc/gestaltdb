"""Experimental optional columnar positive-Datalog evaluator.

Not an Evaluator replacement: no witnesses, support maintenance or publication.
Python handles bounded input/output conversion and rule/round orchestration;
Polars handles filtering, projection, equijoins and exact set operations. There
are no Python row callbacks inside joins/fixpoint operators. Eager expression
plans are rebuilt per occurrence/round; telemetry makes that overhead explicit.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import time
from typing import Callable, Iterable

from ..batches import CompactRelation, SymbolDictionary, UINT64_MAX, _positive
from ..model import (
    EvaluationResult, Fact, Program, ReasoningError, ReasoningLimitError,
    ReasoningLimits, Variable, check_cancelled,
)
from ..protocols import Evidence, InputContext
from ..relational import CompilationIdentity, RelationSchema
from .arrow import to_arrow


def _polars():
    try:
        import polars
    except ImportError as exc:
        raise ImportError("Polars prototype requires gestaltdb[polars]") from exc
    return polars


@dataclass(frozen=True)
class PolarsRun:
    """Complete binary closure plus per-invocation identity and measured counters."""

    result: EvaluationResult
    identity: CompilationIdentity
    telemetry: tuple[tuple[str, int | float], ...]

    @property
    def metrics(self) -> dict[str, int | float]:
        return dict(self.telemetry)


class _FrameOps:
    """Internal entity-ID operators; anti-joins here are set difference, not NAF."""

    def __init__(self, pl, limits, max_intermediate_rows):
        self.pl, self.limits = pl, limits
        self.max_intermediate_rows = _positive(max_intermediate_rows, "max_intermediate_rows")
        self.metrics = {"operator_s": 0.0, "expression_s": 0.0, "preflight_s": 0.0,
                        "candidate_rows": 0, "peak_intermediate_rows": 0,
                        "join_calls": 0, "atom_plans": 0}

    def reserve(self, rows):
        if rows > self.max_intermediate_rows:
            raise ReasoningLimitError("max_intermediate_rows exceeded before materialization")
        if self.metrics["candidate_rows"] + rows > self.limits.max_candidate_rows:
            raise ReasoningLimitError("max_candidate_rows exceeded (columnar output accounting)")
        self.metrics["candidate_rows"] += rows
        self.metrics["peak_intermediate_rows"] = max(self.metrics["peak_intermediate_rows"], rows)

    def perform(self, operation):
        start = time.perf_counter()
        result = operation()
        self.metrics["operator_s"] += time.perf_counter() - start
        return result

    def join(self, left, right, keys):
        # Count matching key multiplicities before allocating the potentially
        # quadratic result. Count-query state is bounded by the input frames,
        # not by the join's output. This is NOT an exact allocator/RSS bound.
        start = time.perf_counter()
        if max(left.height, right.height) > (1 << 32) - 1:
            raise ReasoningLimitError("native group-count uint32 capacity exceeded")
        if left.height * right.height > UINT64_MAX:
            raise ReasoningLimitError("join multiplicity uint64 capacity exceeded")
        pl = self.pl
        counts = left.group_by(keys).len(name="_left_count").join(
            right.group_by(keys).len(name="_right_count"), on=keys, how="inner")
        count = counts.select((pl.col("_left_count").cast(pl.UInt64)
                               * pl.col("_right_count").cast(pl.UInt64)).sum()).item()
        self.metrics["preflight_s"] += time.perf_counter() - start
        self.reserve(count)
        self.metrics["join_calls"] += 1
        return self.perform(lambda: left.join(right, on=keys, how="inner"))

    def filter_join(self, left, right, keys, *, how):
        if how not in {"semi", "anti"}:
            raise ReasoningError("internal filter join supports semi/anti only")
        self.reserve(left.height)  # Safe upper bound before executing.
        return self.perform(lambda: left.join(right, on=keys, how=how))

    def atom(self, atom, frame, variables, dictionary):
        pl = self.pl
        start = time.perf_counter()
        filters, columns, seen = [], [], set()
        for name, term in (("s", atom.subject), ("o", atom.object)):
            if isinstance(term, Variable):
                if term in seen:
                    filters.append(pl.col("s") == pl.col("o"))
                else:
                    columns.append(pl.col(name).alias(variables[term]))
                    seen.add(term)
            else:
                filters.append(pl.col(name) == dictionary.encode(term))
        # The common unit column preserves zero-row truth for ground atoms and
        # gives disconnected bodies an explicitly preflighted equijoin key.
        columns.append((pl.col("s") * 0).alias("_unit"))
        self.metrics["expression_s"] += time.perf_counter() - start
        self.metrics["atom_plans"] += 1
        self.reserve(frame.height)

        def execute():
            selected = frame.filter(*filters) if filters else frame
            return selected.select(columns).unique()

        return self.perform(execute)


class PolarsDatalogPrototype:
    """Explicit closure-only experiment over the existing binary entity profile.

    ``max_candidate_rows`` counts conservative materialized/output rows, NOT the
    indexed Python engine's attempted unifications. Unsupported proofs fail
    before consuming input. Limits do not interrupt a running native operator;
    use worker isolation for hard deadlines/RSS. No context is authenticated here.
    """

    def evaluate(self, program: Program, facts: Iterable[Fact | Evidence], *,
                 context: InputContext, limits: ReasoningLimits,
                 max_intermediate_rows: int = 100_000, collect_proofs: bool = False,
                 cancel: Callable[[], bool] | None = None) -> PolarsRun:
        if not isinstance(program, Program) or not isinstance(limits, ReasoningLimits):
            raise ReasoningError("Program and ReasoningLimits required")
        if not isinstance(context, InputContext):
            raise ReasoningError("context must be an InputContext")
        if not isinstance(collect_proofs, bool) or collect_proofs:
            raise ReasoningError("Polars prototype does not support witness/proof collection")
        check_cancelled(cancel)
        _positive(max_intermediate_rows, "max_intermediate_rows")
        if len(program.rules) > limits.max_rules:
            raise ReasoningLimitError("max_rules exceeded")
        pl = _polars()
        start = time.perf_counter()
        try:
            iterator = iter(facts)
        except TypeError as exc:
            raise ReasoningError("facts must be iterable") from exc
        initial = set()
        for count, value in enumerate(iterator, 1):
            if count > limits.max_input_facts:
                raise ReasoningLimitError("max_input_facts exceeded")
            if count % 1024 == 0:
                check_cancelled(cancel)
            fact = value.fact if isinstance(value, Evidence) else value
            if not isinstance(fact, Fact):
                raise ReasoningError("Fact or Evidence required")
            initial.add(fact)
            if len(initial) > limits.max_facts:
                raise ReasoningLimitError("max_facts exceeded")
        input_s = time.perf_counter() - start
        start = time.perf_counter()
        symbols = {value for fact in initial for value in (fact.subject, fact.predicate, fact.object)}
        for rule in program.rules:
            for atom in (*rule.body, rule.head):
                symbols.add(atom.predicate)
                symbols.update(term for term in (atom.subject, atom.object) if isinstance(term, str))
        dictionary = SymbolDictionary(context, tuple(symbols))
        identity = CompilationIdentity(program.digest, context, dictionary.dictionary_id,
                                       "positive-binary-entity-v1", "polars-prototype-v1")
        grouped = defaultdict(list)
        for fact in sorted(initial):
            grouped[fact.predicate].append((dictionary.encode(fact.subject), dictionary.encode(fact.object)))
        empty = pl.DataFrame(schema={"s": pl.UInt64, "o": pl.UInt64})
        full = {}
        for predicate, rows in grouped.items():
            batch = CompactRelation.from_encoded_rows(RelationSchema(predicate, ("s", "o")), dictionary, rows,
                                                       max_input_rows=limits.max_input_facts,
                                                       max_rows=limits.max_facts)
            full[predicate] = pl.from_arrow(to_arrow(batch))
        conversion_s = time.perf_counter() - start
        del grouped, initial
        start = time.perf_counter()
        dispatch = defaultdict(list)
        for rule in program.rules:
            variables = {variable: f"v{index}" for index, variable in enumerate(
                sorted(set().union(*(atom.variables for atom in rule.body)), key=lambda v: v.name))}
            seen = set()
            for position, atom in enumerate(rule.body):
                if atom not in seen:
                    dispatch[atom.predicate].append((rule, position, variables))
                    seen.add(atom)
        planning_s = time.perf_counter() - start
        ops = _FrameOps(pl, limits, max_intermediate_rows)
        delta, iterations = full.copy(), 0
        start = time.perf_counter()
        while delta:
            # Native operators run to completion once launched; cancellation is
            # honored between rounds, never by interrupting a running kernel.
            check_cancelled(cancel)
            active = [(rule, pivot, variables) for predicate in sorted(delta)
                      for rule, pivot, variables in dispatch.get(predicate, ())
                      if all(atom.predicate in full for atom in rule.body)]
            if not active:
                break
            if iterations >= limits.max_iterations:
                raise ReasoningLimitError("max_iterations exceeded")
            added = {}
            size = sum(frame.height for frame in full.values())
            for rule, pivot, variables in active:
                joined = ops.atom(rule.body[pivot], delta[rule.body[pivot].predicate], variables, dictionary)
                for position, atom in enumerate(rule.body):
                    if position == pivot or not joined.height:
                        continue
                    other = ops.atom(atom, full[atom.predicate], variables, dictionary)
                    keys = sorted(set(joined.columns) & set(other.columns))
                    joined = ops.join(joined, other, keys)
                if not joined.height:
                    continue
                start_plan = time.perf_counter()

                def expression(term):
                    return (pl.col(variables[term]) if isinstance(term, Variable)
                            else pl.col("_unit") + pl.lit(dictionary.encode(term), dtype=pl.UInt64))

                columns = [expression(rule.head.subject).alias("s"), expression(rule.head.object).alias("o")]
                ops.metrics["expression_s"] += time.perf_counter() - start_plan
                ops.reserve(joined.height)
                candidate = ops.perform(lambda: joined.select(columns).unique())
                old = full.get(rule.head.predicate, empty)
                candidate = ops.filter_join(candidate, old, ["s", "o"], how="anti")
                previous = added.get(rule.head.predicate, empty)
                candidate = ops.filter_join(candidate, previous, ["s", "o"], how="anti")
                if size + sum(frame.height for frame in added.values()) + candidate.height > limits.max_facts:
                    raise ReasoningLimitError("max_facts exceeded")
                if candidate.height:
                    ops.reserve(previous.height + candidate.height)
                    added[rule.head.predicate] = ops.perform(lambda: pl.concat([previous, candidate]))
            iterations += 1
            for predicate, frame in added.items():
                old = full.get(predicate, empty)
                ops.reserve(old.height + frame.height)
                full[predicate] = ops.perform(lambda: pl.concat([old, frame]))
            delta = added
        fixpoint_s = time.perf_counter() - start
        start = time.perf_counter()
        decoded = tuple(Fact(dictionary.decode(s), predicate, dictionary.decode(o))
                        for predicate, frame in full.items() for s, o in frame.iter_rows())
        result = EvaluationResult(decoded, iterations, program.digest)
        metrics = {**ops.metrics, "input_s": input_s, "conversion_s": conversion_s,
                   "planning_s": planning_s, "fixpoint_s": fixpoint_s,
                   "decode_s": time.perf_counter() - start, "dictionary_symbols": len(dictionary.symbols)}
        return PolarsRun(result, identity, tuple(sorted(metrics.items())))

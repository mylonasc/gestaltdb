Validate incremental reasoning and recovery
============================================================

Live reasoning requires explicit handling of cancellation, stale state and
recovery. Incremental paths must equal full
rebuilds exactly, or fail closed demanding explicit rematerialization.

**Standalone example, base install only.** Remove one of two edges, then compare
the maintained closure with a fresh rebuild. Logical equality is required; lower
work on every topology is not.

.. code-block:: python

   from gestaltdb.reasoning import (
       GenericProgram, GenericRule, TypedAtom, TypedFact, Variable, rematerialize, retract_facts,
   )
   from gestaltdb.reasoning.model import ReasoningLimits

   x, y, z = Variable("x"), Variable("y"), Variable("z")
   program = GenericProgram([
       GenericRule("direct", [TypedAtom("edge", [x, y])], [], TypedAtom("reach", [x, y])),
       GenericRule("extend", [TypedAtom("reach", [x, y]), TypedAtom("edge", [y, z])],
                   [], TypedAtom("reach", [x, z])),
   ])
   base = [TypedFact("edge", ["a", "b"]), TypedFact("edge", ["b", "c"])]
   victim = base[1]
   material = rematerialize(program, base, limits=ReasoningLimits())
   out = retract_facts(program, material.facts, [victim],
                       limits=ReasoningLimits(), index=material.index)
   assert set(out.facts) == set(rematerialize(
       program, [fact for fact in base if fact != victim],
       limits=ReasoningLimits()).facts)
   assert TypedFact("reach", ["a", "b"]) in out.facts
   assert TypedFact("reach", ["a", "c"]) not in out.facts

Recovery contracts
----------------------------------------

- **Crash at every generation boundary**: cancelling on any check raises
  without partial results; the staged closure and index objects are never
  mutated in place, so a retry starts from the last pinned generation.
- **Stale plans rejected**: a program-digest or horizon mismatch fails
  closed before any work; catalog drift and horizon movement fail at the
  pinned-generation boundary.
- **Orphan recovery on capable backends**: audit is read-only and reclaim
  needs explicit consent plus horizon (:doc:`operations`). Maintenance itself
  does not repair unpublished temporal artifacts.
- **Redundant cycles are no-ops**: re-inserting known facts or retracting
  absent ones returns empty deltas with a byte-identical index digest.
- **Rule revision rematerializes**: new rulesets evaluate under the new
  digest; the old index is never repaired across a revision.

Measured evidence
----------------------------------------

``benchmarks/reasoning_maintenance.py`` freezes the workload evidence in
``benchmarks/results/reasoning-re18-maintenance.json`` (two 8-link and two
40-link chains, 5 repeats): small inserts ~0.5ms, small retractions
~1.1ms, rule revision ~16.5ms at n=8; at n=40 (1,720-fact closure, 1,640
persisted supports) inserts ~12.5ms, retractions ~28.5ms, full revision
~8.1s. Incremental work stays local while complete support discovery
dominates rebuilds — measure before assuming an advantage on deep
recursions. Peak RSS stays under 55MB for both sizes; write amplification
(facts plus supports per base fact) is 10x at n=8 and 42x at n=40.

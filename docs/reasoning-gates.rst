:orphan:

Incremental workload gates and live-reasoning workflows
============================================================

RE-E3 exits here: every live-reasoning path has a crash, cancellation,
staleness and recovery gate, plus measured workload evidence and a
repeatable update/audit workflow. The rule is unchanged from RE-14 onward:
no correctness trade for latency — incremental paths must equal full
rebuilds exactly, or fail closed demanding explicit rematerialization.

.. code-block:: python

   from gestaltdb.reasoning import propagate_insertions, rematerialize, retract_facts
   from gestaltdb.reasoning.model import ReasoningLimits

   material = rematerialize(program, base, limits=ReasoningLimits())
   out = retract_facts(program, material.facts, [victim],
                       limits=ReasoningLimits(), index=material.index)
   assert set(out.facts) == set(rematerialize(
       program, [fact for fact in base if fact != victim],
       limits=ReasoningLimits()).facts)
   assert out.work < rematerialize(program, base, limits=ReasoningLimits()).work

Gates
----------------------------------------

- **Crash at every generation boundary**: cancelling on any check raises
  without partial results; the staged closure and index objects are never
  mutated in place, so a retry starts from the last pinned generation.
- **Stale plans rejected**: a program-digest or horizon mismatch fails
  closed before any work; catalog drift and horizon movement fail at the
  pinned-generation boundary.
- **Orphan recovery on capable backends**: audit is read-only and reclaim
  needs explicit consent plus horizon; backend-gated tests enforce this
  where storage backends are installed and skip otherwise.
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

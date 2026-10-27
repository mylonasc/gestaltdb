"""Explicit, opt-in random-walk policies; legacy kernels remain unchanged."""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from .validation import _validate_count
from .walks import RandomWalkBatch
from .walk_schema import WalkSchema
from .walk_time import TemporalWalkBatch, TemporalWalkPolicy
from .walk_transitions import choose_transition, node2vec_bias

if TYPE_CHECKING:
    from .engine import SamplerEngine


@dataclass(frozen=True)
class WalkPolicy:
    """Hard avoidance policy (RWS-04), with explicit exhaustion behavior.

    ``avoid_recent`` includes the current node: 1 forbids self-loops, 2 also
    forbids returning to the previous node. ``non_backtracking`` forbids that
    return independently. ``relax`` drops only avoidance, not other filters.
    Restart jumps clear history. Parallel edges to a forbidden node all fail.
    """

    non_backtracking: bool = False
    avoid_recent: int = 0
    avoidance_exhaustion: str = "terminate"
    strategy: str = "uniform"
    schema: WalkSchema | None = None
    chronology: TemporalWalkPolicy | None = None
    transition_unit: str = "edge"
    neighbor_weight_aggregation: str = "sum"
    node2vec: bool = False
    return_factor: float = 1.0
    inout_factor: float = 1.0
    bias_adjacency: str = "snapshot"
    weighted_first_step: bool = False

    def __post_init__(self):
        if not isinstance(self.non_backtracking, bool):
            raise ValueError("non_backtracking must be a boolean")
        _validate_count(self.avoid_recent, "avoid_recent")
        if self.avoidance_exhaustion not in {"terminate", "restart", "relax"}:
            raise ValueError("avoidance_exhaustion must be terminate, restart or relax")
        if self.strategy not in {"uniform", "weighted"}:
            raise ValueError("strategy must be uniform or weighted")
        if self.schema is not None and not isinstance(self.schema, WalkSchema):
            raise TypeError("schema must be a WalkSchema")
        if self.chronology is not None and not isinstance(self.chronology, TemporalWalkPolicy):
            raise TypeError("chronology must be a TemporalWalkPolicy")
        if self.transition_unit not in {"edge", "neighbor"}:
            raise ValueError("transition_unit must be edge or neighbor")
        if self.neighbor_weight_aggregation not in {"sum", "mean", "max"}:
            raise ValueError("neighbor_weight_aggregation must be sum, mean or max")
        for name in ("node2vec", "weighted_first_step"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a boolean")
        for name in ("return_factor", "inout_factor"):
            value = getattr(self, name)
            if (isinstance(value, (bool, np.bool_))
                    or not isinstance(value, (int, float, np.integer, np.floating))
                    or not np.isfinite(value) or value <= 0 or not np.isfinite(1.0 / value)):
                raise ValueError(f"{name} must be positive finite with finite reciprocal")
        if self.bias_adjacency not in {"snapshot", "eligible"}:
            raise ValueError("bias_adjacency must be snapshot or eligible")
        if self.weighted_first_step and not self.node2vec:
            raise ValueError("weighted_first_step requires node2vec=True")
        if self.bias_adjacency == "eligible" and self.schema is not None:
            if self.schema.transitions or self.schema.metapath is not None:
                raise ValueError("eligible bias adjacency supports static schemas, not state machines/metapaths")


def sample_policy_walks(
    engine: SamplerEngine, start_nodes, walk_length: int, *,
    policy: WalkPolicy | None = None, direction: str = "out", relations=None,
    temporal=None, window_policy: str = "overlap", dead_end: str = "terminate",
    restart_probability: float = 0.0,
) -> RandomWalkBatch:
    """Reference constrained kernel; memory is bounded per walk and candidate set."""
    policy = WalkPolicy() if policy is None else policy
    if not isinstance(policy, WalkPolicy):
        raise TypeError("policy must be a WalkPolicy")
    engine.sample_random_walks([], walk_length, direction=direction, relations=relations,
                              temporal=temporal, window_policy=window_policy, dead_end=dead_end,
                              restart_probability=restart_probability, strategy=policy.strategy)
    starts = engine._validated_ids(start_nodes, engine.snapshot.num_nodes, "start_nodes")
    relation_filter = None if relations is None else set(
        engine._validated_ids(relations, engine.snapshot.num_relations, "relations").tolist()
    )
    schema = policy.schema or WalkSchema()
    schema.validate_snapshot(engine)
    chronology = policy.chronology
    if chronology is not None and not engine.snapshot.temporal:
        raise ValueError("chronological walks require a temporal snapshot")
    nodes = np.full((starts.size, walk_length + 1), -1, dtype=np.int64)
    edges = np.full((starts.size, walk_length), -1, dtype=np.int64)
    rels = np.full_like(edges, -1)
    lengths = np.zeros(starts.size, dtype=np.int64)
    times = np.zeros_like(edges) if chronology is not None else None
    nodes[:, 0] = starts
    for row, start in enumerate(starts.tolist()):
        current, previous = start, None
        state, position = schema.initial_state, 0
        clock = chronology.initial_time if chronology is not None else None
        history = deque([start], maxlen=max(2, policy.avoid_recent))
        for step in range(walk_length):
            restart = restart_probability > 0 and engine.rng.random() < restart_probability
            if not restart:
                candidates, endpoints, next_states, schema_weights = schema.candidates(
                    engine, current, state, position, direction, relation_filter, temporal, window_policy,
                )
                candidate_times = None
                if chronology is not None:
                    feasible, candidate_times, time_weights = chronology.candidates(
                        engine.snapshot, candidates, clock, temporal,
                    )
                    candidates, endpoints, next_states, schema_weights, candidate_times = (
                        values[feasible] for values in (
                            candidates, endpoints, next_states, schema_weights * time_weights, candidate_times
                        )
                    )
                action = dead_end
                if candidates.size:
                    forbidden = set(list(history)[-policy.avoid_recent:]) if policy.avoid_recent else set()
                    if policy.non_backtracking and previous is not None:
                        forbidden.add(previous)
                    if forbidden:
                        allowed = ~np.isin(endpoints, list(forbidden))
                        if np.any(allowed):
                            candidates = candidates[allowed]
                            endpoints, next_states, schema_weights = (
                                values[allowed] for values in (endpoints, next_states, schema_weights)
                            )
                            if candidate_times is not None:
                                candidate_times = candidate_times[allowed]
                        elif policy.avoidance_exhaustion != "relax":
                            candidates = candidates[:0]
                            action = policy.avoidance_exhaustion
                if candidates.size:
                    weights = schema_weights
                    use_static = (previous is not None or policy.weighted_first_step) if policy.node2vec else (
                        policy.strategy == "weighted"
                    )
                    if use_static:
                        static = engine.snapshot.edge_weights[candidates]
                        weights = weights * (static / (float(static.max()) or 1))
                    if policy.node2vec and previous is not None:
                        weights = weights * node2vec_bias(
                            engine, previous, endpoints, policy, schema, relation_filter, temporal, window_policy, clock,
                        )
                    has_weights = (use_static or (policy.node2vec and previous is not None)
                                   or bool(schema.relation_weights)
                                   or any(arc.weight != 1 for arc in schema.transitions)
                                   or (chronology is not None and chronology.recency_half_life_us is not None))
                    if not has_weights:
                        weights = None
                    chosen = choose_transition(engine, candidates, endpoints, weights, policy)
                    edge = int(candidates[chosen])
                    previous, current = current, int(endpoints[chosen])
                    state, position = int(next_states[chosen]), position + 1
                    if chronology is not None:
                        clock = int(candidate_times[chosen])
                        times[row, step] = clock
                    history.append(current)
                    edges[row, step] = edge
                    rels[row, step] = int(engine.snapshot.rel_int[edge])
                elif action == "restart":
                    restart = True
                else:
                    break
            if restart:
                current, previous = start, None
                state, position = schema.initial_state, 0
                clock = chronology.initial_time if chronology is not None else None
                history.clear()
                history.append(start)
            nodes[row, step + 1] = current
            lengths[row] = step + 1
    if chronology is not None:
        versions = np.full(edges.shape, None, dtype=object)
        mask = edges >= 0
        versions[mask] = engine.snapshot.edge_version_ids[edges[mask]]
        return TemporalWalkBatch(
            nodes, edges, rels, lengths, times, mask, versions,
            deepcopy(engine.snapshot.metadata.get("source_provenance")),
        )
    return RandomWalkBatch(nodes, edges, rels, lengths)

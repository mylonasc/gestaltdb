"""Explicit, opt-in random-walk policies; legacy kernels remain unchanged."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from .validation import _validate_count
from .walks import RandomWalkBatch
from .walk_schema import WalkSchema

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
    nodes = np.full((starts.size, walk_length + 1), -1, dtype=np.int64)
    edges = np.full((starts.size, walk_length), -1, dtype=np.int64)
    rels = np.full_like(edges, -1)
    lengths = np.zeros(starts.size, dtype=np.int64)
    nodes[:, 0] = starts
    for row, start in enumerate(starts.tolist()):
        current, previous = start, None
        state, position = schema.initial_state, 0
        history = deque([start], maxlen=max(2, policy.avoid_recent))
        for step in range(walk_length):
            restart = restart_probability > 0 and engine.rng.random() < restart_probability
            if not restart:
                candidates, endpoints, next_states, schema_weights = schema.candidates(
                    engine, current, state, position, direction, relation_filter, temporal, window_policy,
                )
                action = dead_end
                if candidates.size:
                    forbidden = set(list(history)[-policy.avoid_recent:]) if policy.avoid_recent else set()
                    if policy.non_backtracking and previous is not None:
                        forbidden.add(previous)
                    allowed = ~np.isin(endpoints, list(forbidden))
                    if np.any(allowed):
                        candidates = candidates[allowed]
                        endpoints, next_states, schema_weights = (
                            values[allowed] for values in (endpoints, next_states, schema_weights)
                        )
                    elif policy.avoidance_exhaustion != "relax":
                        candidates = candidates[:0]
                        action = policy.avoidance_exhaustion
                if candidates.size:
                    weights = schema_weights
                    if policy.strategy == "weighted":
                        static = engine.snapshot.edge_weights[candidates]
                        weights = weights * (static / (float(static.max()) or 1))
                    # Keep the default uniform RNG path compatible with legacy walks.
                    if policy.schema is None and policy.strategy == "uniform":
                        weights = None
                    chosen = int(engine._choose_candidates(np.arange(candidates.size), 1, replace=False, weights=weights)[0])
                    edge = int(candidates[chosen])
                    previous, current = current, int(endpoints[chosen])
                    state, position = int(next_states[chosen]), position + 1
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
                history.clear()
                history.append(start)
            nodes[row, step + 1] = current
            lengths[row] = step + 1
    return RandomWalkBatch(nodes, edges, rels, lengths)

"""Explicit edge/neighbor transition units and node2vec bias (RWS-07)."""

from __future__ import annotations

import numpy as np


def choose_transition(engine, candidates, endpoints, weights, policy):
    """Return an aligned candidate position, retaining the actual traversed edge."""
    positions = np.arange(candidates.size)
    if policy.transition_unit == "edge":
        return int(engine._choose_candidates(positions, 1, replace=False, weights=weights)[0])
    neighbors, inverse = np.unique(endpoints, return_inverse=True)
    group_weights = None
    if weights is not None:
        scaled = weights / (float(np.max(weights)) or 1)
        group_weights = np.zeros(neighbors.size)
        if policy.neighbor_weight_aggregation == "max":
            np.maximum.at(group_weights, inverse, scaled)
        else:
            np.add.at(group_weights, inverse, scaled)
            if policy.neighbor_weight_aggregation == "mean":
                group_weights /= np.bincount(inverse, minlength=neighbors.size)
    group = int(engine._choose_candidates(np.arange(neighbors.size), 1, replace=False, weights=group_weights)[0])
    selected = positions[inverse == group]
    selected_weights = weights[selected] if weights is not None else None
    return int(engine._choose_candidates(selected, 1, replace=False, weights=selected_weights)[0])


def node2vec_bias(engine, previous, endpoints, policy, schema, relation_filter, temporal, window_policy, clock):
    """Bias by return, distance one, or farther under explicit adjacency scope."""
    if policy.bias_adjacency == "snapshot":
        incident = engine.snapshot.incident.edge_range(previous)
        neighbors = set(engine.snapshot.src_int[incident].tolist()) | set(engine.snapshot.dst_int[incident].tolist())
    else:
        edges, adjacent, _, _ = schema.candidates(
            engine, previous, schema.initial_state, 0, "any", relation_filter, temporal, window_policy,
        )
        mask = engine.snapshot.edge_weights[edges] > 0 if (
            policy.strategy == "weighted" or policy.node2vec
        ) else np.ones(edges.size, dtype=bool)
        if policy.chronology is not None:
            feasible, _, _ = policy.chronology.candidates(engine.snapshot, edges, clock, temporal)
            mask &= feasible
        neighbors = set(adjacent[mask].tolist())
    bias = np.ones(endpoints.size)
    returning = endpoints == previous
    bias[returning] = 1 / policy.return_factor
    bias[~returning & ~np.isin(endpoints, list(neighbors))] = 1 / policy.inout_factor
    return bias / float(bias.max())

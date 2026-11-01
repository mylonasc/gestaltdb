"""Bounded typed/metapath and finite-state walk schemas (RWS-05)."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

import numpy as np

from .validation import _integer_array, _validate_count


def _ids(values, name):
    if values is None:
        return None
    values = _integer_array(values, name)
    if values.size and values.min() < 0:
        raise ValueError(f"{name} must contain non-negative IDs")
    return tuple(int(value) for value in values)


def _weight(value, name):
    if (isinstance(value, (bool, np.bool_))
            or not isinstance(value, (int, float, np.integer, np.floating))
            or not np.isfinite(value) or value < 0):
        raise ValueError(f"{name} must be finite and non-negative")
    return float(value)


@dataclass(frozen=True)
class WalkTransition:
    """One state-machine arc; direction is relative to the current walk node.

    Attributes:
        source_state: State this arc leaves.
        target_state: State this arc enters.
        relation: Required compact relation ID, or ``None`` for any relation
            (still intersected with global relation filters).
        source_types: Allowed compact source node types, or ``None``.
        target_types: Allowed compact target node types, or ``None``.
        direction: ``"out"``, ``"in"``, or ``"any"``; overrides the walk
            direction for this arc only.
        weight: Non-negative arc weight; ``0`` skips the arc. Arc weights
            multiply relation weights and optional static edge weights.

    See Also:
        ``docs/random-walks.rst`` RWS-05 for a runnable state-machine example.
    """

    source_state: int
    target_state: int
    relation: int | None = None
    source_types: tuple[int, ...] | None = None
    target_types: tuple[int, ...] | None = None
    direction: str = "out"
    weight: float = 1.0

    def __post_init__(self):
        for name in ("source_state", "target_state"):
            _validate_count(getattr(self, name), name)
        if self.relation is not None:
            _validate_count(self.relation, "relation")
        if self.direction not in {"out", "in", "any"}:
            raise ValueError("transition direction must be out, in or any")
        object.__setattr__(self, "source_types", _ids(self.source_types, "source_types"))
        object.__setattr__(self, "target_types", _ids(self.target_types, "target_types"))
        object.__setattr__(self, "weight", _weight(self.weight, "transition weight"))


@dataclass(frozen=True)
class WalkSchema:
    """Typed targets, cyclic metapath OR finite-state transitions, and weights.

    State machines sample (transition, edge) alternatives. Overlapping arcs
    intentionally count as separate choices and may lead to different states.
    Arc weights multiply relation weights and optional static edge weights.
    Schema progress resets on every restart. All IDs are compact snapshot IDs;
    ``relation_weights`` defaults to 1.0 and ``0`` disables a relation.

    Attributes:
        node_types: Allowed compact target node types for every step.
        metapath: Cyclic compact relation IDs (mutually exclusive with
            ``transitions``). The relation advances only on edge steps.
        relation_weights: Per-relation multipliers (default 1.0).
        transitions: Finite-state arcs (mutually exclusive with ``metapath``).
        initial_state: Starting state.
        max_states: State bound; arc states must be below it.
        max_transitions: Arc-count bound.

    See Also:
        ``docs/random-walks.rst`` RWS-05 for metapath/state-machine examples.
    """

    node_types: tuple[int, ...] | None = None
    metapath: tuple[int, ...] | None = None
    relation_weights: Mapping[int, float] | None = None
    transitions: tuple[WalkTransition, ...] = ()
    initial_state: int = 0
    max_states: int = 256
    max_transitions: int = 4096

    def __post_init__(self):
        for name in ("node_types", "metapath"):
            object.__setattr__(self, name, _ids(getattr(self, name), name))
        if self.metapath is not None and not self.metapath:
            raise ValueError("metapath must not be empty")
        object.__setattr__(self, "transitions", tuple(self.transitions))
        if self.metapath is not None and self.transitions:
            raise ValueError("metapath and finite-state transitions are mutually exclusive")
        for name in ("initial_state", "max_states", "max_transitions"):
            _validate_count(getattr(self, name), name)
        if self.max_states < 1 or self.initial_state >= self.max_states:
            raise ValueError("initial_state exceeds positive max_states")
        if len(self.transitions) > self.max_transitions:
            raise ValueError("transition count exceeds max_transitions")
        for arc in self.transitions:
            if not isinstance(arc, WalkTransition):
                raise TypeError("transitions must contain WalkTransition values")
            if max(arc.source_state, arc.target_state) >= self.max_states:
                raise ValueError("transition state exceeds max_states")
        weights = {}
        for relation, weight in (self.relation_weights or {}).items():
            _validate_count(relation, "relation_weights key")
            weights[int(relation)] = _weight(weight, "relation weight")
        object.__setattr__(self, "relation_weights", MappingProxyType(weights))

    def validate_snapshot(self, engine):
        """Fail on unknown compact relations/types before any walk draws."""
        relations = list(self.metapath or ()) + list(self.relation_weights)
        types = list(self.node_types or ())
        for arc in self.transitions:
            if arc.relation is not None:
                relations.append(arc.relation)
            types.extend(arc.source_types or ())
            types.extend(arc.target_types or ())
        engine._validated_ids(relations, engine.snapshot.num_relations, "schema relations")
        # Probe in bounded chunks; validation must not materialize every node's
        # type while iterating a small seed batch over a large memmap snapshot.
        missing = set(types)
        values = engine.snapshot.node_type_ids
        for offset in range(0, values.size, 4096) if missing else ():
            missing.difference_update(np.unique(values[offset:offset + 4096]).tolist())
            if not missing:
                break
        if missing:
            raise ValueError("schema contains unknown node type IDs")

    def candidates(self, engine, current, state, position, direction, relations, temporal, window_policy):
        """Return aligned edge, endpoint, next-state and LOG-weight arrays.

        Weights are log-space so relation, arc, static, node2vec, and recency
        terms compose without underflow; callers exponentiate after masking.
        """
        snapshot = engine.snapshot
        chunks = []
        arcs = [arc for arc in self.transitions if arc.source_state == state] if self.transitions else [None]
        for arc in arcs:
            if arc is not None and arc.weight == 0:
                continue
            step_relations = relations
            relation = arc.relation if arc is not None else (
                self.metapath[position % len(self.metapath)] if self.metapath else None
            )
            if relation is not None:
                step_relations = {relation} if relations is None or relation in relations else set()
            if arc is not None and arc.source_types is not None:
                if int(snapshot.node_type_ids[current]) not in arc.source_types:
                    continue
            step_direction = arc.direction if arc is not None else direction
            edges = engine._candidate_edges(current, step_direction, step_relations, temporal, window_policy)
            endpoints = engine._neighbors_for_edges(current, edges, step_direction)
            mask = np.ones(edges.size, dtype=bool)
            for types in (self.node_types, arc.target_types if arc is not None else None):
                if types is not None:
                    mask &= np.isin(snapshot.node_type_ids[endpoints], types)
            edges, endpoints = edges[mask], endpoints[mask]
            weights = np.array([self.relation_weights.get(int(rel), 1.0) for rel in snapshot.rel_int[edges]]) if (
                self.relation_weights
            ) else np.ones(edges.size)
            arc_weight = arc.weight if arc is not None else 1.0
            positive = weights > 0
            chunks.append((edges[positive], endpoints[positive],
                           arc.target_state if arc is not None else state,
                           np.log(weights[positive]) + np.log(arc_weight)))
        if not chunks:
            empty = np.empty(0, dtype=np.int64)
            return empty, empty, empty, np.empty(0)
        edge_parts, endpoint_parts, states, weights_parts = [], [], [], []
        for edges, endpoints, state, log_weights in chunks:
            edge_parts.append(edges)
            endpoint_parts.append(endpoints)
            states.append(np.full(edges.size, state, dtype=np.int64))
            weights_parts.append(log_weights)
        return tuple(np.concatenate(parts) for parts in (edge_parts, endpoint_parts, states, weights_parts))

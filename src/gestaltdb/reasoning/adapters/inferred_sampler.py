"""Immutable sampler artifacts from explicitly selected inferred views.

The scenario milestone (``test_reasoning_inferred_consumers``) answers a goal
from a session and reconciles a managed world. This slice builds the second
consumer: an immutable ``SamplerSnapshot`` derived from one pinned session.

Selection is explicit and generation-bound. The caller names a unary node
predicate (for example ``person``) and one or more binary edge predicates
(for example ``memberOf``). Nodes are the inferred facts under the node
predicate; edges are session facts under the edge predicates whose source
endpoint is an inferred node. This is not multi-hop traversal: seed filtering
alone never constrains the artifact, and no hidden Cypher rewrite, array
mutation or weight/path multiplicity change is performed. One session fact
yields one snapshot edge with weight ``1.0``.

Provenance travels in snapshot metadata under ``reasoning_generation``:
database identity, commit horizon, program/catalog digests, scope
world/valid-time, the explicit selection, and inferred/base fact counts.
A caller-supplied ``expected_generation`` mismatch fails closed before any
file is written. An already-populated output directory also fails closed, so
old pinned artifacts stay unchanged and each new generation gets a new path.
Snapshots never retain a live database handle.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping

from ..model import ReasoningError, _text
from ..typed import TypedFact, _sort_key
from ...sampling.snapshot import SamplerSnapshot
from .sessions import ReasoningSession


def _generation_of(session: ReasoningSession, node_predicate: str, edge_predicates: tuple[str, ...]) -> dict:
    return {
        "database_id": session.database_id,
        "commit_horizon": session.commit_horizon,
        "program_digest": session.program.digest,
        "catalog_digest": session.catalog.digest,
        "world": session.scope.world,
        "valid_time_us": session.scope.valid_time_us,
        "node_predicate": node_predicate,
        "edge_predicates": list(edge_predicates),
    }


def build_inferred_snapshot(
    session: ReasoningSession,
    output_path: str | Path,
    *,
    node_predicate: str = "person",
    edge_predicates: Iterable[str] = ("memberOf",),
    expected_generation: Mapping[str, object] | None = None,
) -> SamplerSnapshot:
    """Build an immutable snapshot from one pinned session's inferred view."""
    if not isinstance(session, ReasoningSession):
        raise ReasoningError("session must be a ReasoningSession")
    node_predicate = _text(node_predicate, "node_predicate")
    try:
        predicates = tuple(_text(name, "edge predicate") for name in edge_predicates)
    except TypeError as exc:
        raise ReasoningError("edge_predicates must be iterable") from exc
    if not predicates:
        raise ReasoningError("edge_predicates must name at least one predicate")
    if expected_generation is not None:
        if not isinstance(expected_generation, Mapping):
            raise ReasoningError("expected_generation must be a mapping")
        actual = _generation_of(session, node_predicate, predicates)
        for key, value in expected_generation.items():
            if actual.get(key) != value:
                raise ReasoningError(
                    f"generation/view mismatch on {key!r}; rebuild from the pinned session explicitly"
                )
    inferred_nodes = sorted(
        {fact.values[0] for fact in session.facts
         if fact.predicate == node_predicate and len(fact.values) == 1 and isinstance(fact.values[0], str)}
    )
    node_set = set(inferred_nodes)
    selected: list[TypedFact] = []
    for fact in sorted(session.facts, key=_sort_key):
        if fact.predicate not in predicates or len(fact.values) != 2:
            continue
        src, dst = fact.values
        if not isinstance(src, str) or not isinstance(dst, str):
            continue
        if src not in node_set:
            continue
        selected.append(fact)
    endpoints: set[str] = set(inferred_nodes)
    for fact in selected:
        endpoints.update(fact.values)  # type: ignore[arg-type]
    external_nodes = sorted(endpoints)
    external_relations = sorted(set(predicates))
    node_to_int = {node: idx for idx, node in enumerate(external_nodes)}
    rel_to_int = {rel: idx for idx, rel in enumerate(external_relations)}
    src_int = [node_to_int[fact.values[0]] for fact in selected]  # type: ignore[index]
    dst_int = [node_to_int[fact.values[1]] for fact in selected]  # type: ignore[index]
    rel_int = [rel_to_int[fact.predicate] for fact in selected]
    external_edges = [f"{fact.values[0]}|{fact.predicate}|{fact.values[1]}#{idx}"  # type: ignore[index]
                      for idx, fact in enumerate(selected)]
    target = Path(output_path)
    if target.exists() and any(target.iterdir()):
        raise ReasoningError("output_path already holds a snapshot; use a new path per generation")
    generation = _generation_of(session, node_predicate, predicates)
    generation["inferred_node_count"] = len(inferred_nodes)
    generation["selected_edge_count"] = len(selected)
    metadata = {"reasoning_generation": generation}
    return SamplerSnapshot.from_edge_arrays(
        target,
        external_node_ids=external_nodes,
        external_edge_ids=external_edges,
        external_relation_ids=external_relations,
        src_int=src_int,
        dst_int=dst_int,
        rel_int=rel_int,
        metadata=metadata,
    )

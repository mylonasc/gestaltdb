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

from ..model import ReasoningError, ReasoningLimitError, _text, check_cancelled
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
    """Legacy unverified builder: partial mapping pins are not source admission.

    For independent source authentication and exact shared generation/state
    pins use authenticated_inferred_sampler.build_authenticated_inferred_snapshot.
    """
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
    arrays, counts = _project_inferred(session, node_predicate, predicates)
    target = Path(output_path)
    if target.exists() and any(target.iterdir()):
        raise ReasoningError("output_path already holds a snapshot; use a new path per generation")
    generation = _generation_of(session, node_predicate, predicates)
    generation.update(counts)
    return SamplerSnapshot.from_edge_arrays(target, **arrays, metadata={"reasoning_generation": generation})


def _project_inferred(session, node_predicate, predicates, *, cancel=None, max_nodes=None, max_edges=None):
    """One deterministic projection shared by legacy and authenticated consumers."""
    node_set = set()
    for fact in session.facts:
        check_cancelled(cancel)
        if fact.predicate == node_predicate and len(fact.values) == 1 and isinstance(fact.values[0], str):
            node_set.add(fact.values[0])
            if max_nodes is not None and len(node_set) > max_nodes:
                raise ReasoningLimitError("inferred projection exceeded max_nodes")
    inferred_nodes = sorted(node_set)
    node_set = set(inferred_nodes)
    selected: list[TypedFact] = []
    for fact in sorted(session.facts, key=_sort_key):
        check_cancelled(cancel)
        if fact.predicate not in predicates or len(fact.values) != 2:
            continue
        src, dst = fact.values
        if not isinstance(src, str) or not isinstance(dst, str):
            continue
        if src not in node_set:
            continue
        selected.append(fact)
        if max_edges is not None and len(selected) > max_edges:
            raise ReasoningLimitError("inferred projection exceeded max_edges")
    endpoints: set[str] = set(inferred_nodes)
    for fact in selected:
        check_cancelled(cancel)
        endpoints.update(fact.values)  # type: ignore[arg-type]
        if max_nodes is not None and len(endpoints) > max_nodes:
            raise ReasoningLimitError("inferred projection exceeded max_nodes")
    external_nodes = sorted(endpoints)
    external_relations = sorted(set(predicates))
    node_to_int = {node: idx for idx, node in enumerate(external_nodes)}
    rel_to_int = {rel: idx for idx, rel in enumerate(external_relations)}
    src_int = [node_to_int[fact.values[0]] for fact in selected]  # type: ignore[index]
    dst_int = [node_to_int[fact.values[1]] for fact in selected]  # type: ignore[index]
    rel_int = [rel_to_int[fact.predicate] for fact in selected]
    external_edges = [f"{fact.values[0]}|{fact.predicate}|{fact.values[1]}#{idx}"  # type: ignore[index]
                      for idx, fact in enumerate(selected)]
    check_cancelled(cancel)
    return ({"external_node_ids": external_nodes, "external_edge_ids": external_edges,
             "external_relation_ids": external_relations, "src_int": src_int,
             "dst_int": dst_int, "rel_int": rel_int},
            {"inferred_node_count": len(inferred_nodes), "selected_edge_count": len(selected)})

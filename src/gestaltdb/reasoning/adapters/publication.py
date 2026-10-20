"""Explicit single-point publication, using fresh recomputation, not witness TMS.

Only this adapter writes claims. It holds the database-wide temporal writer
guard across extraction, inference, target comparison and one logical commit.
The reserved output agent is excluded by the input policy, preventing cyclic
self-support. There is deliberately no arbitrary-result or interval publisher.
"""
from __future__ import annotations

from dataclasses import dataclass

from ...epistemic import Claim
from ...graphdb import GraphDB
from ...ontology.compiler import CompiledOntology
from ...temporal import TemporalInstant, TemporalInterval
from ...versioning import ClaimVersionWrite, normalize_temporal_instant
from ..model import ReasoningError, ReasoningLimitError, ReasoningLimits, _text
from ..protocols import InputContext
from .claims import ClaimMapping, ClaimSelection, ClaimSourceLimits, PinnedClaimSource, _digest


@dataclass(frozen=True)
class PublicationResult:
    context: InputContext
    generation_id: str
    commit_id: int | None
    asserted: int
    corrected: int
    retracted: int
    unchanged: int


def reconcile_point(
    graph: GraphDB, compiled: CompiledOntology, *, valid_time,
    source_world: str, target_world: str, publication_key: str,
    selection: ClaimSelection, mapping: ClaimMapping,
    limits: ReasoningLimits, source_limits: ClaimSourceLimits = ClaimSourceLimits(),
    max_target_claims: int = 100_000,
) -> PublicationResult:
    """Recompute and atomically replace one managed single-point closure.

    Output validity is exactly ``[t, t+1 microsecond)``. Distinct points/keys have
    separate claim identities. ``target_world`` must differ from source_world.
    The target namespace is reserved for this operation; unrelated claims are
    never retracted. Full recomputation handles loss of the last independent
    support, cycles and revision changes. Existing result objects are not accepted:
    stale-input policy is always fresh/latest under the shared writer guard.

    Work/claim limits are fail-closed before publication. Lock duration includes
    inference and backend authentication. This is not incremental maintenance or
    an interval-closure API, nor does it install rules in the persistent catalog.
    """
    if not isinstance(graph, GraphDB) or not isinstance(compiled, CompiledOntology):
        raise ReasoningError("GraphDB and CompiledOntology are required")
    for name, value in (("source_world", source_world), ("target_world", target_world), ("publication_key", publication_key)):
        _text(value, name)
    if source_world == target_world:
        raise ReasoningError("publication requires a separate target world")
    if not isinstance(selection, ClaimSelection) or selection.include_derived:
        raise ReasoningError("publication requires a non-derived ClaimSelection")
    if not isinstance(mapping, ClaimMapping) or not isinstance(limits, ReasoningLimits) or not isinstance(source_limits, ClaimSourceLimits):
        raise ReasoningError("explicit mapping and limits required")
    if isinstance(max_target_claims, bool) or not isinstance(max_target_claims, int) or max_target_claims < 1:
        raise ReasoningError("max_target_claims must be a positive integer")
    instant = normalize_temporal_instant(valid_time)
    interval = (instant, TemporalInstant(instant.epoch_microseconds + 1))
    namespace = "ontology-publication:" + _digest([publication_key, source_world, target_world, instant.epoch_microseconds]).split(":")[1]
    # Private lock access is deliberately confined to this persistence adapter.
    # It is shared with every temporal writer, including other GraphDB handles.
    with graph._temporal_writer_guard():
        if any(item.kind == "incomplete_commit" for item in graph.list_temporal_orphans().artifacts):
            raise ReasoningError("unreclaimed temporal publication requires explicit orphan recovery")
        with graph.read_view(valid_time=instant) as view:
            with PinnedClaimSource(view, world=source_world, ontology_revision=compiled.revision_id,
                                   selection=selection, mapping=mapping, limits=source_limits) as source:
                # Exact input references, not a single proof witness, identify this
                # generation. Bounds are applied during extraction before collection.
                evidence = []
                for item in source.iter_evidence():
                    if len(evidence) >= limits.max_input_facts:
                        raise ReasoningLimitError("publication exceeded max_input_facts")
                    evidence.append(item)
                from ..engine import DatalogEngine

                result = DatalogEngine().evaluate(compiled.program, evidence, limits=limits)
                source.verify_source()
                context = source.context
            generation = _digest([
                "ontology-point-publication-v1", view.provenance.database_id, namespace,
                compiled.revision_id, selection.policy_id, mapping.mapping_id,
                sorted((item.fact.subject, item.fact.predicate, item.fact.object, item.reference_id) for item in evidence),
            ])
            existing = {}
            for version in view.iter_claims_as_of(world=target_world, agent="gestaltdb:ontology", source=namespace):
                if len(existing) >= max_target_claims:
                    raise ReasoningLimitError("publication exceeded max_target_claims")
                claim = version.claim
                if (claim.provenance.get("engine") != "gestaltdb.ontology"
                        or claim.provenance.get("publication_key") != publication_key
                        or version.valid != TemporalInterval(*interval)):
                    raise ReasoningError("publication namespace contains unmanaged claims")
                existing[claim.claim_id] = version
            desired = {}
            for fact in result.facts:
                claim = Claim(fact.subject, fact.predicate, fact.object, "positive", "gestaltdb:ontology",
                              source=namespace, world=target_world, provenance={
                                  "engine": "gestaltdb.ontology", "publication_key": publication_key,
                                  "generation": generation, "input_context": context.context_id,
                                  "ontology_revision": compiled.revision_id,
                                  "valid_point_us": instant.epoch_microseconds,
                              })
                desired[claim.claim_id] = claim
            writes = []
            asserted = corrected = retracted = unchanged = 0
            for claim_id, claim in sorted(desired.items()):
                previous = existing.get(claim_id)
                if previous is None:
                    writes.append(ClaimVersionWrite.assertion(claim, interval))
                    asserted += 1
                elif previous.claim.provenance.get("generation") != generation:
                    writes.append(ClaimVersionWrite.correction(claim, supersedes_version_id=previous.version_id, valid=interval))
                    corrected += 1
                else:
                    unchanged += 1
            for claim_id, previous in sorted(existing.items()):
                if claim_id not in desired:
                    writes.append(ClaimVersionWrite.retraction(claim_id, valid=interval,
                                  supersedes_version_id=previous.version_id, reason="ontology point reconciliation"))
                    retracted += 1
        commit = None
        if writes:
            commit = graph.commit_versions(writes, metadata={"engine": "gestaltdb.ontology", "generation": generation,
                                                           "input_context": context.context_id})
        return PublicationResult(context, generation, None if commit is None else commit.commit_id,
                                 asserted, corrected, retracted, unchanged)

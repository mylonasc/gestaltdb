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


class GenerationStaleError(ReasoningError):
    """Staged base no longer matches the managed namespace; retry explicitly."""


@dataclass(frozen=True)
class StagedPointPublication:
    """Inference-complete staged generation; no writes have been published.

    ``base_fingerprint`` binds the exact visible target state the stage was
    computed against. ``publish_staged`` fails closed when the namespace
    moved, so readers only ever observe old-or-new generations.
    """

    namespace: str
    publication_key: str
    source_world: str
    target_world: str
    valid_point_us: int
    generation: str
    input_context_id: str
    ontology_revision: str
    policy_id: str
    mapping_id: str
    database_id: str
    desired: tuple[tuple[str, str, str], ...]
    base_fingerprint: str

    def __post_init__(self):
        for name in ("namespace", "publication_key", "source_world", "target_world",
                     "generation", "input_context_id", "ontology_revision",
                     "policy_id", "mapping_id", "database_id", "base_fingerprint"):
            _text(getattr(self, name), name)
        if isinstance(self.valid_point_us, bool) or not isinstance(self.valid_point_us, int) or self.valid_point_us < 0:
            raise ReasoningError("valid_point_us must be a non-negative integer")
        if not isinstance(self.desired, (tuple, list)):
            raise ReasoningError("desired must be a tuple or list")
        cleaned = []
        for triple in self.desired:
            if not isinstance(triple, (tuple, list)) or len(triple) != 3:
                raise ReasoningError("desired triples must be (subject, predicate, object)")
            cleaned.append((_text(triple[0], "subject"), _text(triple[1], "predicate"), _text(triple[2], "object")))
        object.__setattr__(self, "desired", tuple(sorted(set(cleaned))))


def _stage_fingerprint(entries: Iterable[tuple[str, str]]) -> str:
    return _digest(["ontology-point-base-v1", sorted((claim_id, generation) for claim_id, generation in entries)])


def stage_point(
    graph: GraphDB, compiled: CompiledOntology, *, valid_time,
    source_world: str, target_world: str, publication_key: str,
    selection: ClaimSelection, mapping: ClaimMapping,
    limits: ReasoningLimits, source_limits: ClaimSourceLimits = ClaimSourceLimits(),
) -> StagedPointPublication:
    """Run extraction and inference without holding the temporal writer guard.

    Only the final ``publish_staged`` step takes the guard, keeping lock
    duration to comparison plus one logical commit. The stage records the
    visible target fingerprint it was computed against.
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
    instant = normalize_temporal_instant(valid_time)
    interval = (instant, TemporalInstant(instant.epoch_microseconds + 1))
    namespace = "ontology-publication:" + _digest([publication_key, source_world, target_world, instant.epoch_microseconds]).split(":")[1]
    with graph.read_view(valid_time=instant) as view:
        with PinnedClaimSource(view, world=source_world, ontology_revision=compiled.revision_id,
                               selection=selection, mapping=mapping, limits=source_limits) as source:
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
        base = []
        for version in view.iter_claims_as_of(world=target_world, agent="gestaltdb:ontology", source=namespace):
            claim = version.claim
            if (claim.provenance.get("engine") != "gestaltdb.ontology"
                    or claim.provenance.get("publication_key") != publication_key
                    or version.valid != TemporalInterval(*interval)):
                raise ReasoningError("publication namespace contains unmanaged claims")
            base.append((claim.claim_id, str(claim.provenance.get("generation", ""))))
        database_id = view.provenance.database_id
    desired = tuple((fact.subject, fact.predicate, fact.object) for fact in result.facts)
    return StagedPointPublication(namespace, publication_key, source_world, target_world,
                                  instant.epoch_microseconds, generation, context.context_id,
                                  compiled.revision_id, selection.policy_id, mapping.mapping_id,
                                  database_id, desired, _stage_fingerprint(base))


def publish_staged(
    graph: GraphDB, staged: StagedPointPublication, *, max_target_claims: int = 100_000,
) -> PublicationResult:
    """Compare the staged base fingerprint and publish exactly once on match.

    Holds the writer guard only for orphan audit, target comparison and one
    logical commit. Any namespace movement raises ``GenerationStaleError``;
    callers retry by re-staging explicitly. No stale closure is published.
    """
    if not isinstance(staged, StagedPointPublication):
        raise ReasoningError("a StagedPointPublication is required")
    if isinstance(max_target_claims, bool) or not isinstance(max_target_claims, int) or max_target_claims < 1:
        raise ReasoningError("max_target_claims must be a positive integer")
    if not isinstance(graph, GraphDB):
        raise ReasoningError("GraphDB is required")
    instant = TemporalInstant(staged.valid_point_us)
    interval = (instant, TemporalInstant(instant.epoch_microseconds + 1))
    with graph._temporal_writer_guard():
        if any(item.kind == "incomplete_commit" for item in graph.list_temporal_orphans().artifacts):
            raise ReasoningError("unreclaimed temporal publication requires explicit orphan recovery")
        with graph.read_view(valid_time=instant) as view:
            if view.provenance.database_id != staged.database_id:
                raise ReasoningError("staged generation belongs to a different database")
            current = {}
            for version in view.iter_claims_as_of(world=staged.target_world, agent="gestaltdb:ontology",
                                                  source=staged.namespace):
                if len(current) >= max_target_claims:
                    raise ReasoningLimitError("publication exceeded max_target_claims")
                claim = version.claim
                if (claim.provenance.get("engine") != "gestaltdb.ontology"
                        or claim.provenance.get("publication_key") != staged.publication_key
                        or version.valid != TemporalInterval(*interval)):
                    raise ReasoningError("publication namespace contains unmanaged claims")
                current[claim.claim_id] = version
            if _stage_fingerprint([(claim_id, str(version.claim.provenance.get("generation", "")))
                                   for claim_id, version in current.items()]) != staged.base_fingerprint:
                raise GenerationStaleError("target namespace moved since staging; re-stage and retry")
            desired = {}
            for subject, predicate, obj in staged.desired:
                claim = Claim(subject, predicate, obj, "positive", "gestaltdb:ontology",
                              source=staged.namespace, world=staged.target_world, provenance={
                                  "engine": "gestaltdb.ontology", "publication_key": staged.publication_key,
                                  "generation": staged.generation, "input_context": staged.input_context_id,
                                  "ontology_revision": staged.ontology_revision,
                                  "valid_point_us": staged.valid_point_us,
                              })
                desired[claim.claim_id] = claim
            writes = []
            asserted = corrected = retracted = unchanged = 0
            for claim_id, claim in sorted(desired.items()):
                previous = current.get(claim_id)
                if previous is None:
                    writes.append(ClaimVersionWrite.assertion(claim, interval))
                    asserted += 1
                elif previous.claim.provenance.get("generation") != staged.generation:
                    writes.append(ClaimVersionWrite.correction(claim, supersedes_version_id=previous.version_id, valid=interval))
                    corrected += 1
                else:
                    unchanged += 1
            for claim_id, previous in sorted(current.items()):
                if claim_id not in desired:
                    writes.append(ClaimVersionWrite.retraction(claim_id, valid=interval,
                                  supersedes_version_id=previous.version_id, reason="ontology point reconciliation"))
                    retracted += 1
            context = InputContext(staged.input_context_id, "ontology-point-publication",
                                   staged.ontology_revision, "explicit-staged-generation-v1", staged.mapping_id)
        commit = None
        if writes:
            commit = graph.commit_versions(writes, metadata={"engine": "gestaltdb.ontology", "generation": staged.generation,
                                                             "input_context": staged.input_context_id})
        return PublicationResult(context, staged.generation, None if commit is None else commit.commit_id,
                                 asserted, corrected, retracted, unchanged)


def audit_publication_orphans(graph: GraphDB, *, through_commit=None):
    """Read-only orphan inventory; never mutates or repairs."""
    if not isinstance(graph, GraphDB):
        raise ReasoningError("GraphDB is required")
    return graph.list_temporal_orphans(through_commit=through_commit)


def reclaim_publication_orphans(graph: GraphDB, *, through_commit: int, consent: bool = False):
    """Delete validated unpublished artifacts only with explicit consent.

    ``consent`` must be ``True``; otherwise this fails closed without touching
    storage. ``through_commit`` follows the existing temporal reclaim horizon.
    """
    if consent is not True:
        raise ReasoningError("orphan reclamation requires explicit consent=True")
    if isinstance(through_commit, bool) or not isinstance(through_commit, int) or through_commit < 0:
        raise ReasoningError("through_commit must be a non-negative integer")
    if not isinstance(graph, GraphDB):
        raise ReasoningError("GraphDB is required")
    return graph.reclaim_temporal_orphans(through_commit=through_commit)


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

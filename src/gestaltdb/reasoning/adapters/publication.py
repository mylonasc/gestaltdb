"""Explicit single-point publication, using fresh recomputation, not witness TMS.

Only this adapter writes claims. It holds the database-wide temporal writer
guard across extraction, inference, target comparison and one logical commit.
The reserved output agent is excluded by the input policy, preventing cyclic
self-support. Managed interval generations extend the same staged
compare-and-publish pattern to caller-derived range validity; each validity
interval is its own namespace and gaps are never filled.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

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


@dataclass(frozen=True)
class StagedRangePublication:
    """One staged interval generation: caller-derived claims over one validity.

    Each validity interval is its own reserved namespace; gaps between
    generations are never filled and disjoint validity is never enveloped.
    Derivation is the caller's temporal evaluation (for example a range
    closure); this stage binds exact source/rule identities plus the
    caller's evidence digest into the generation fingerprint.
    """

    namespace: str
    publication_key: str
    source_world: str
    target_world: str
    valid_start_us: int
    valid_end_us: int | None
    generation: str
    evidence_id: str
    input_context_id: str
    ontology_revision: str
    policy_id: str
    mapping_id: str
    database_id: str
    desired: tuple[tuple[str, str, str], ...]
    base_fingerprint: str

    def __post_init__(self):
        for name in ("namespace", "publication_key", "source_world", "target_world",
                     "generation", "evidence_id", "input_context_id", "ontology_revision",
                     "policy_id", "mapping_id", "database_id", "base_fingerprint"):
            _text(getattr(self, name), name)
        if isinstance(self.valid_start_us, bool) or not isinstance(self.valid_start_us, int) or self.valid_start_us < 0:
            raise ReasoningError("valid_start_us must be a non-negative integer")
        if self.valid_end_us is not None and (
                isinstance(self.valid_end_us, bool) or not isinstance(self.valid_end_us, int)
                or self.valid_end_us <= self.valid_start_us):
            raise ReasoningError("valid_end_us must be None or greater than valid_start_us")
        if not isinstance(self.desired, (tuple, list)):
            raise ReasoningError("desired must be a tuple or list")
        cleaned = []
        for triple in self.desired:
            if not isinstance(triple, (tuple, list)) or len(triple) != 3:
                raise ReasoningError("desired triples must be (subject, predicate, object)")
            cleaned.append((_text(triple[0], "subject"), _text(triple[1], "predicate"), _text(triple[2], "object")))
        object.__setattr__(self, "desired", tuple(sorted(set(cleaned))))


def _range_namespace(publication_key: str, source_world: str, target_world: str,
                     start_us: int, end_us: int | None) -> str:
    return "ontology-range-publication:" + _digest(
        [publication_key, source_world, target_world, start_us, end_us]).split(":")[1]


def _range_target_fingerprint(graph, view, namespace, world):
    """Include exact target versions, including retractions, to reject ABA.

    The first implementation scans pinned history; a namespace revision index
    can optimize it without weakening the compare-and-publish contract.
    """
    managed = set()
    entries = []
    for version in graph.iter_claim_versions(through_commit=view.provenance.commit_horizon):
        claim = version.claim
        if (claim is not None and claim.world == world and claim.agent == "gestaltdb:ontology"
                and claim.source == namespace):
            managed.add(version.logical_id)
        if version.logical_id in managed:
            entries.append((version.logical_id, version.version_id))
    return _stage_fingerprint(entries)


def stage_range(graph: GraphDB, desired: Iterable[tuple[str, str, str]], *, valid_start_us: int,
                valid_end_us: int | None, evidence_id: str, ontology_revision: str,
                source_world: str, target_world: str, publication_key: str,
                selection: ClaimSelection, mapping: ClaimMapping) -> StagedRangePublication:
    """Stage caller-derived interval claims without holding the writer guard.

    Only target-namespace comparison plus one logical commit take the guard
    in ``publish_staged_range``. Validity is exactly the caller's range —
    never widened, never gap-filled. ``reconcile_point`` behavior is
    untouched; point generations keep their single-quantum namespaces.
    """
    if not isinstance(graph, GraphDB):
        raise ReasoningError("GraphDB is required")
    for name, value in (("source_world", source_world), ("target_world", target_world),
                        ("publication_key", publication_key), ("evidence_id", evidence_id),
                        ("ontology_revision", ontology_revision)):
        _text(value, name)
    if source_world == target_world:
        raise ReasoningError("publication requires a separate target world")
    if not isinstance(selection, ClaimSelection) or selection.include_derived:
        raise ReasoningError("publication requires a non-derived ClaimSelection")
    if not isinstance(mapping, ClaimMapping):
        raise ReasoningError("explicit ClaimMapping is required")
    try:
        triples = [tuple(triple) for triple in desired]
    except TypeError as exc:
        raise ReasoningError("desired must be iterable") from exc
    if isinstance(valid_start_us, bool) or not isinstance(valid_start_us, int) or valid_start_us < 0:
        raise ReasoningError("valid_start_us must be a non-negative integer")
    if valid_end_us is not None and (
            isinstance(valid_end_us, bool) or not isinstance(valid_end_us, int) or valid_end_us <= valid_start_us):
        raise ReasoningError("valid_end_us must be None or greater than valid_start_us")
    namespace = _range_namespace(publication_key, source_world, target_world, valid_start_us, valid_end_us)
    sample = TemporalInstant(valid_start_us)
    with graph.read_view(valid_time=sample) as view:
        for version in view.iter_claims_as_of(world=target_world, agent="gestaltdb:ontology", source=namespace):
            claim = version.claim
            if (claim.provenance.get("engine") != "gestaltdb.ontology"
                    or claim.provenance.get("publication_key") != publication_key
                    or version.valid != TemporalInterval(sample, None if valid_end_us is None
                                                         else TemporalInstant(valid_end_us))):
                raise ReasoningError("publication namespace contains unmanaged claims")
        database_id = view.provenance.database_id
        base_fingerprint = _range_target_fingerprint(graph, view, namespace, target_world)
    generation = _digest(["ontology-range-publication-v2", database_id, namespace, evidence_id, ontology_revision,
                          selection.policy_id, mapping.mapping_id, sorted(set(triples))])
    return StagedRangePublication(
        namespace=namespace, publication_key=publication_key, source_world=source_world,
        target_world=target_world, valid_start_us=valid_start_us, valid_end_us=valid_end_us,
        generation=generation, evidence_id=evidence_id, input_context_id="explicit-range-evidence-v1",
        ontology_revision=ontology_revision, policy_id=selection.policy_id, mapping_id=mapping.mapping_id,
        database_id=database_id, desired=tuple(sorted(set(triples))), base_fingerprint=base_fingerprint,
    )


def publish_staged_range(graph: GraphDB, staged: StagedRangePublication, *,
                         max_target_claims: int = 100_000) -> PublicationResult:
    """Compare the staged base fingerprint and publish exactly once on match.

    Holds the writer guard only for orphan audit, target comparison and one
    logical commit. Namespace movement raises ``GenerationStaleError``;
    callers re-stage explicitly. Validity is published exactly as staged.
    """
    if not isinstance(staged, StagedRangePublication):
        raise ReasoningError("a StagedRangePublication is required")
    if isinstance(max_target_claims, bool) or not isinstance(max_target_claims, int) or max_target_claims < 1:
        raise ReasoningError("max_target_claims must be a positive integer")
    if not isinstance(graph, GraphDB):
        raise ReasoningError("GraphDB is required")
    interval = TemporalInterval(TemporalInstant(staged.valid_start_us),
                                None if staged.valid_end_us is None else TemporalInstant(staged.valid_end_us))
    with graph._temporal_writer_guard():
        if any(item.kind == "incomplete_commit" for item in graph.list_temporal_orphans().artifacts):
            raise ReasoningError("unreclaimed temporal publication requires explicit orphan recovery")
        with graph.read_view(valid_time=TemporalInstant(staged.valid_start_us)) as view:
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
                        or version.valid != interval):
                    raise ReasoningError("publication namespace contains unmanaged claims")
                current[claim.claim_id] = version
            desired = {}
            for subject, predicate, obj in staged.desired:
                claim = Claim(subject, predicate, obj, "positive", "gestaltdb:ontology",
                              source=staged.namespace, world=staged.target_world, provenance={
                                  "engine": "gestaltdb.ontology", "publication_key": staged.publication_key,
                                   "generation": staged.generation, "evidence": staged.evidence_id,
                                   "input_context": staged.input_context_id,
                                   "ontology_revision": staged.ontology_revision,
                                  "valid_start_us": staged.valid_start_us,
                                  "valid_end_us": staged.valid_end_us,
                              })
                desired[claim.claim_id] = claim
            current_fingerprint = _range_target_fingerprint(graph, view, staged.namespace, staged.target_world)
            already_published = (current.keys() == desired.keys() and all(
                version.claim.provenance.get("generation") == staged.generation
                for version in current.values()))
            if current_fingerprint != staged.base_fingerprint and not already_published:
                raise GenerationStaleError("target namespace moved since staging; re-stage and retry")
            writes = []
            asserted = corrected = retracted = unchanged = 0
            for claim_id, claim in sorted(desired.items()):
                previous = current.get(claim_id)
                if previous is None:
                    writes.append(ClaimVersionWrite.assertion(claim, (interval.start, interval.end)))
                    asserted += 1
                elif previous.claim.provenance.get("generation") != staged.generation:
                    writes.append(ClaimVersionWrite.correction(claim, supersedes_version_id=previous.version_id,
                                                              valid=(interval.start, interval.end)))
                    corrected += 1
                else:
                    unchanged += 1
            for claim_id, previous in sorted(current.items()):
                if claim_id not in desired:
                    writes.append(ClaimVersionWrite.retraction(claim_id, valid=(interval.start, interval.end),
                                  supersedes_version_id=previous.version_id, reason="ontology range reconciliation"))
                    retracted += 1
            context = InputContext(staged.input_context_id, "ontology-range-publication",
                                   staged.ontology_revision, "explicit-staged-range-v1", staged.mapping_id)
        commit = None
        if writes:
            commit = graph.commit_versions(writes, metadata={"engine": "gestaltdb.ontology", "generation": staged.generation,
                                                             "input_context": staged.input_context_id})
        return PublicationResult(context, staged.generation, None if commit is None else commit.commit_id,
                                 asserted, corrected, retracted, unchanged)


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

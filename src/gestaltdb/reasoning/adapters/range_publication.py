"""Authenticated range computation to one guarded logical publication commit.

Legacy caller-derived stage_range remains separate. No supplied closure, stage
metadata or expected-generation token bypasses re-admission and target CAS.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import chain

from ...epistemic import Claim, ClaimObjectKind, ClaimPolarity
from ...graphdb import GraphDB
from ...temporal import TemporalInstant, TemporalInterval
from ...versioning import ClaimVersionWrite
from ..catalog import RuleCatalog
from ..generation import GenerationIdentity
from ..model import ReasoningError, ReasoningLimitError, ReasoningLimits, _text, check_cancelled
from ..typed import GenericProgram
from .claims import ClaimMapping, ClaimSelection, _digest, _verify_graph
from .publication import GenerationStaleError, PublicationResult
from .range_generation import RangeReasoningResult, RangeSourceLimits, evaluate_pinned_range, admit_range_result


@dataclass(frozen=True)
class RangePublicationLimits:
    max_target_history: int = 100_000
    max_target_claims: int = 100_000
    max_outputs: int = 100_000
    max_writes: int = 100_000

    def __post_init__(self):
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ReasoningError(f"{name} must be a positive integer")


@dataclass(frozen=True, order=True)
class PublishedIntervalFact:
    subject: str
    predicate: str
    object: str
    start_us: int
    end_us: int

    def __post_init__(self):
        for name in ("subject", "predicate", "object"):
            _text(getattr(self, name), name)
        for value in (self.start_us, self.end_us):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ReasoningError("publication validity must have finite integer bounds")
        if self.start_us >= self.end_us:
            raise ReasoningError("publication validity requires start < end")

    @property
    def valid(self):
        return TemporalInterval(TemporalInstant(self.start_us), TemporalInstant(self.end_us))


def _namespace(key, source_world, target_world, source):
    return "ontology-bound-range:" + _digest([key, source_world, target_world,
        source.valid_start_us, source.valid_end_us]).split(":")[1]


def _interval_source(namespace, start, end):
    return namespace + ":" + _digest([start, end]).split(":")[1]


def _outputs(result, bounds, cancel):
    facts = set()
    for fact in chain((b.fact for b in result.closure.base), (d.fact for d in result.closure.derivations)):
        check_cancelled(cancel)
        if len(fact.values) != 2 or not all(isinstance(v, str) and v for v in fact.values):
            raise ReasoningError("range publication requires binary entity output; explicit conversion required")
        facts.add(fact)
        if len(facts) > bounds.max_outputs:
            raise ReasoningLimitError("range publication exceeded max_outputs")
    source = result.binding.identity.source
    rows = []
    for fact in sorted(facts, key=lambda f: (f.predicate, f.values)):
        check_cancelled(cancel)
        for interval in result.closure.answers(fact).intervals:
            if (interval.start is None or interval.end is None or interval.start < source.valid_start_us
                    or interval.end > source.valid_end_us):
                raise ReasoningError("output validity falls outside authenticated range")
            if len(rows) >= bounds.max_outputs:
                raise ReasoningLimitError("range publication exceeded max_outputs")
            rows.append(PublishedIntervalFact(fact.values[0], fact.predicate, fact.values[1], interval.start, interval.end))
    return tuple(sorted(rows))


def _content(result, rows):
    identity = result.binding.identity
    source = identity.source
    # Do not churn unchanged output just because our own previous publication
    # advanced the database horizon. Exact source versions/policies stay bound.
    return _digest(["bound-range-content-v1", identity.program_digest, identity.catalog_digest,
                    identity.context.world, identity.context.source_policy_id, identity.context.mapping_id,
                    source.valid_start_us, source.valid_end_us,
                    [[e.version_id, e.logical_id, e.fact.predicate, list(e.fact.values),
                      [[i.start, i.end] for i in e.valid.intervals]] for e in result.evidence],
                    [[r.subject, r.predicate, r.object, r.start_us, r.end_us] for r in rows]])


@dataclass(frozen=True)
class StagedRangeGeneration:
    namespace: str
    publication_key: str
    target_world: str
    program: GenericProgram
    catalog: RuleCatalog
    result: RangeReasoningResult
    desired: tuple[PublishedIntervalFact, ...]
    content_id: str
    base_fingerprint: str

    def __post_init__(self):
        for name in ("namespace", "publication_key", "target_world", "content_id", "base_fingerprint"):
            _text(getattr(self, name), name)
        if (not isinstance(self.program, GenericProgram) or not isinstance(self.catalog, RuleCatalog)
                or not isinstance(self.result, RangeReasoningResult)):
            raise ReasoningError("typed program, catalog and authenticated range result required")
        identity = self.result.binding.identity
        if (identity.context.world == self.target_world or self.result.binding.selection.include_derived
                or identity.program_digest != self.program.digest or identity.catalog_digest != self.catalog.digest
                or self.namespace != _namespace(self.publication_key, identity.context.world, self.target_world, identity.source)):
            raise ReasoningError("staged range generation policy/program/catalog/namespace mismatch")
        if not isinstance(self.desired, (tuple, list)) or not all(isinstance(r, PublishedIntervalFact) for r in self.desired):
            raise ReasoningError("desired must contain PublishedIntervalFact values")
        object.__setattr__(self, "desired", tuple(self.desired))


def _target_state(view, namespace, key, target_world, source, bounds, cancel):
    """Discover old interval namespaces too, including now-obsolete validity.

    Every operation in a reserved logical interval must retain exactly that
    interval; partial external mutations fail closed rather than being missed
    by looking only at its start. Canonical point winners select active versions.
    """
    graph, store, provenance, serializer = view._graph, view._graph.store, view.provenance, view._graph.serializer
    def check():
        check_cancelled(cancel)
        if (view._graph is not graph or graph.store is not store or graph.serializer is not serializer
                or view.provenance != provenance or view.valid_time != TemporalInstant(provenance.valid_time_us)):
            raise ReasoningError("publication target view/store/decoder was rebound")
    check()
    _verify_graph(provenance, graph)
    history, spans = [], {}
    for version in view.iter_claim_versions():
        check()
        if len(history) >= bounds.max_target_history:
            raise ReasoningLimitError("range publication exceeded max_target_history")
        history.append(version)
        claim = version.claim
        if claim is None or not isinstance(claim.source, str) or not claim.source.startswith(namespace + ":"):
            continue
        p = claim.provenance
        start, end = p.get("valid_start_us"), p.get("valid_end_us")
        if (claim.world != target_world or claim.agent != "gestaltdb:ontology"
                or claim.polarity is not ClaimPolarity.POSITIVE or claim.object_kind is not ClaimObjectKind.ENTITY
                or p.get("engine") != "gestaltdb.ontology" or p.get("range_namespace") != namespace
                or p.get("publication_key") != key or isinstance(start, bool) or not isinstance(start, int)
                or isinstance(end, bool) or not isinstance(end, int)
                or not source.valid_start_us <= start < end <= source.valid_end_us
                or claim.source != _interval_source(namespace, start, end)
                or not all(isinstance(p.get(field), str) and p.get(field) for field in (
                    "range_content", "reasoning_generation", "program_digest", "catalog_digest", "input_context",
                    "source_provenance_token", "selection_policy", "mapping_id"))
                or p.get("source_valid_start_us") != source.valid_start_us
                or p.get("source_valid_end_us") != source.valid_end_us
                or p.get("semantics_profile") != "positive-typed-range-v1"
                or p.get("dictionary_id") != "typed-values-v1"):
            raise ReasoningError("reserved range namespace contains unmanaged claims")
        span = TemporalInterval(TemporalInstant(start), TemporalInstant(end))
        if version.valid != span or (version.logical_id in spans and spans[version.logical_id] != span):
            raise ReasoningError("managed range interval was changed externally")
        spans[version.logical_id] = span
        if len(spans) > bounds.max_target_claims:
            raise ReasoningLimitError("range publication exceeded max_target_claims")
    for version in history:
        check()
        if version.logical_id in spans and version.valid != spans[version.logical_id]:
            raise ReasoningError("managed range interval was partially changed externally")
    current = {}
    for logical_id, span in sorted(spans.items()):
        check()
        version = view.get_claim_as_of(logical_id, valid_time=span.start)
        check()
        if version is not None:
            if version.valid != span:
                raise ReasoningError("managed range winner validity mismatch")
            current[logical_id] = version
    _verify_graph(provenance, graph)
    check()
    # Include tombstones/history, not only active winners: a target that was
    # populated then emptied after staging must not pass an empty-state ABA.
    return current, tuple(sorted(v.version_id for v in history if v.logical_id in spans))


def _fingerprint(state):
    current, history = state
    return _digest(["bound-range-target-v1", history,
                    sorted((key, version.version_id) for key, version in current.items())])


def stage_range_generation(graph: GraphDB, program: GenericProgram, catalog: RuleCatalog, *,
                            valid_start_us: int, valid_end_us: int, source_world: str,
                            target_world: str, publication_key: str, selection: ClaimSelection,
                            mapping: ClaimMapping, limits: ReasoningLimits,
                            source_limits: RangeSourceLimits = RangeSourceLimits(),
                            publication_limits: RangePublicationLimits = RangePublicationLimits(),
                            expected_generation: GenerationIdentity | None = None, cancel=None) -> StagedRangeGeneration:
    """Compute from a pinned source and capture the target state for publication.

    ``source_world`` selects assertions; ``target_world`` must be different.
    Staging returns a value to pass to ``publish_range_generation``; publication
    rechecks source and target history and commits the interval batch. The
    complete runnable setup (database, program/catalog, policies, stage, publish,
    and interval reads) is in :doc:`reasoning-bound-range-publication`.
    """
    if not isinstance(graph, GraphDB) or not isinstance(publication_limits, RangePublicationLimits):
        raise ReasoningError("GraphDB and RangePublicationLimits required")
    _text(publication_key, "publication_key")
    _text(target_world, "target_world")
    if source_world == target_world or not isinstance(selection, ClaimSelection) or selection.include_derived:
        raise ReasoningError("publication requires a separate target world and non-derived selection")
    check_cancelled(cancel)
    store, serializer = graph.store, graph.serializer
    with graph.read_view(valid_time=valid_start_us) as view:
        result = evaluate_pinned_range(view, program, catalog, valid_start_us=valid_start_us, valid_end_us=valid_end_us,
            world=source_world, selection=selection, mapping=mapping, limits=limits,
            source_limits=source_limits, expected_generation=expected_generation, cancel=cancel)
        desired = _outputs(result, publication_limits, cancel)
        if graph.store is not store or graph.serializer is not serializer:
            raise ReasoningError("publication graph store/decoder was rebound")
        namespace = _namespace(publication_key, source_world, target_world, result.binding.identity.source)
        current = _target_state(view, namespace, publication_key, target_world, result.binding.identity.source,
                                publication_limits, cancel)
        return StagedRangeGeneration(namespace, publication_key, target_world, program, catalog, result,
                                     desired, _content(result, desired), _fingerprint(current))


def publish_range_generation(graph: GraphDB, staged: StagedRangeGeneration, *, limits: ReasoningLimits,
                              source_limits: RangeSourceLimits = RangeSourceLimits(),
                              publication_limits: RangePublicationLimits = RangePublicationLimits(),
                              expected_generation: GenerationIdentity | None = None,
                              freshness: str = "latest", cancel=None) -> PublicationResult:
    """Re-admit computation and CAS the entire interval batch in one logical commit.

    Pass the value returned by ``stage_range_generation``. Source/target movement
    can reject that value; restage explicitly rather than modifying its pins.
    See :doc:`reasoning-bound-range-publication` for a self-contained example
    covering publication, validity gaps, and idempotent retry.
    """
    if (not isinstance(graph, GraphDB) or not isinstance(staged, StagedRangeGeneration)
            or not isinstance(publication_limits, RangePublicationLimits)
            or not isinstance(source_limits, RangeSourceLimits) or not isinstance(limits, ReasoningLimits)):
        raise ReasoningError("GraphDB, StagedRangeGeneration and explicit reasoning/source/publication limits required")
    if freshness not in ("latest", "historical"):
        raise ReasoningError("freshness must be latest or historical")
    binding = staged.result.binding
    identity = binding.identity
    if identity.source.commit_horizon > source_limits.max_commits:
        raise ReasoningLimitError("range publication exceeded max_commits")
    store, serializer = graph.store, graph.serializer
    def check_binding():
        if graph.store is not store or graph.serializer is not serializer:
            raise ReasoningError("publication graph store/decoder was rebound")
    if expected_generation is not None:
        identity.require_compatible(expected_generation)
    if len(staged.desired) > publication_limits.max_outputs:
        raise ReasoningLimitError("range publication exceeded max_outputs")
    check_cancelled(cancel)
    with graph.temporal.write_guard():
        check_binding()
        if any(item.kind == "incomplete_commit" for item in graph.temporal.list_temporal_orphans().artifacts):
            raise ReasoningError("unreclaimed temporal publication requires explicit orphan recovery")
        binding.verify_source(graph, freshness=freshness)
        with graph.read_view(valid_time=identity.source.valid_start_us,
                             through_commit=identity.source.commit_horizon) as original:
            admit_range_result(original, staged.result, staged.program, staged.catalog,
                expected_generation=identity, limits=limits, source_limits=source_limits, cancel=cancel)
        desired_rows = _outputs(staged.result, publication_limits, cancel)
        check_binding()
        if desired_rows != staged.desired or _content(staged.result, desired_rows) != staged.content_id:
            raise ReasoningError("staged interval output/content was altered; re-stage explicitly")
        with graph.read_view(valid_time=identity.source.valid_start_us) as latest:
            horizon = latest.commit_horizon
            state = _target_state(latest, staged.namespace, staged.publication_key, staged.target_world,
                                  identity.source, publication_limits, cancel)
        if _fingerprint(state) != staged.base_fingerprint:
            raise GenerationStaleError("managed range target versions moved; re-stage explicitly")
        current = state[0]
        metadata = {"engine": "gestaltdb.ontology", "publication_key": staged.publication_key,
            "range_namespace": staged.namespace, "range_content": staged.content_id,
            "reasoning_generation": identity.digest, "program_digest": identity.program_digest,
            "catalog_digest": identity.catalog_digest, "input_context": identity.context.context_id,
            "source_world": identity.context.world, "source_commit_horizon": identity.source.commit_horizon,
            "source_provenance_token": identity.source.provenance_token,
            "source_valid_start_us": identity.source.valid_start_us, "source_valid_end_us": identity.source.valid_end_us,
            "selection_policy": identity.context.source_policy_id, "mapping_id": identity.context.mapping_id,
            "semantics_profile": identity.semantics_profile, "dictionary_id": identity.dictionary_id,
            "publication_freshness": freshness}
        desired = {}
        for row in desired_rows:
            check_cancelled(cancel)
            claim = Claim(row.subject, row.predicate, row.object, "positive", "gestaltdb:ontology",
                source=_interval_source(staged.namespace, row.start_us, row.end_us), world=staged.target_world,
                provenance={**metadata, "derived_by": "gestaltdb.ontology",
                            "valid_start_us": row.start_us, "valid_end_us": row.end_us})
            desired[claim.claim_id] = (claim, row.valid)
        writes = []
        def append_write(write):
            if len(writes) >= publication_limits.max_writes:
                raise ReasoningLimitError("range publication exceeded max_writes")
            writes.append(write)
        asserted = corrected = retracted = unchanged = 0
        for key, (claim, valid) in sorted(desired.items()):
            check_cancelled(cancel)
            old = current.get(key)
            if old is None:
                append_write(ClaimVersionWrite.assertion(claim, (valid.start, valid.end)))
                asserted += 1
            elif old.claim.provenance.get("range_content") != staged.content_id:
                append_write(ClaimVersionWrite.correction(claim, supersedes_version_id=old.version_id,
                                                          valid=(valid.start, valid.end)))
                corrected += 1
            else:
                unchanged += 1
        for key, old in sorted(current.items()):
            check_cancelled(cancel)
            if key not in desired:
                append_write(ClaimVersionWrite.retraction(key, valid=(old.valid.start, old.valid.end),
                    supersedes_version_id=old.version_id, reason="authenticated range reconciliation"))
                retracted += 1
        check_cancelled(cancel)
        check_binding()
        binding.verify_source(graph, freshness=freshness)
        with graph.read_view(valid_time=identity.source.valid_start_us) as final:
            if final.commit_horizon != horizon:
                raise GenerationStaleError("source/target moved while preparing publication")
        commit = graph.temporal.commit_versions(writes, metadata=metadata) if writes else None
        return PublicationResult(identity.context, staged.content_id, None if commit is None else commit.commit_id,
                                 asserted, corrected, retracted, unchanged)

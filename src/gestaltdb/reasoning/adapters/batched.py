"""Pinned batched claim extraction; borrows the caller's read view, never storage.

Batches preserve snapshot authentication and exact version-ID evidence. The
adapter never opens secondary handles, initializes identity, repairs indexes or
publishes. Predicate/subject pushdown uses the existing indexed claim query;
anything else filters in Python with the pushdown mode recorded in the profile.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from ...epistemic import Claim, ClaimObjectKind, ClaimPolarity
from ...ontology.compiler import CompiledOntology
from ...readview import GraphReadView, ProvenanceMismatchError, ReadViewProvenance
from ...temporal import TemporalInstant
from ...versioning import ClaimVersion, VersionOperation, normalize_version_id
from ..engine import DatalogEngine
from ..extraction import ExtractionCapability, ExtractionRequest, FactBatch, batch_evidence
from ..model import Fact, ReasoningError, ReasoningLimitError, ReasoningLimits, _text
from ..protocols import Evidence, InputContext
from .claims import (
    ClaimMapping,
    ClaimReasoningResult,
    ClaimSelection,
    ClaimSourceLimits,
    _context,
    _DERIVED_AGENTS,
    _verify_graph,
)


@dataclass(frozen=True)
class ExtractionProfile:
    """Per-invocation extraction counters; wall time belongs to the caller."""

    batches: int
    rows: int
    scanned: int
    verifications: int
    pushdown: str


class PinnedBatchedClaimSource:
    """Borrow one existing GraphReadView and yield bounded evidence batches.

    Use as a context manager or call close(). Closing invalidates this source
    and its outstanding iterators, not the caller's view/graph or results.
    """

    def __init__(
        self, view: GraphReadView, *, request: ExtractionRequest,
        ontology_revision: str, selection: ClaimSelection, mapping: ClaimMapping,
        limits: ClaimSourceLimits = ClaimSourceLimits(),
    ) -> None:
        if type(view) is not GraphReadView:
            raise ReasoningError("view must be a temporal GraphReadView")
        if not isinstance(request, ExtractionRequest):
            raise ReasoningError("request must be an ExtractionRequest")
        if not isinstance(selection, ClaimSelection) or not isinstance(mapping, ClaimMapping):
            raise ReasoningError("selection and mapping must be explicit claim policy values")
        if not isinstance(limits, ClaimSourceLimits):
            raise ReasoningError("source limits must be ClaimSourceLimits")
        _text(request.world, "world")
        _text(ontology_revision, "ontology_revision")
        self._original = view
        self._graph = view._graph
        self._provenance = ReadViewProvenance.from_dict(view.provenance.to_dict())
        self._time = TemporalInstant(self._provenance.valid_time_us)
        self._pinned = GraphReadView(self._graph, self._provenance)
        self._request = request
        self._selection, self._mapping, self._limits = selection, mapping, limits
        self._agents = None if selection.agents is None else frozenset(selection.agents)
        self._sources = None if selection.sources is None else frozenset(selection.sources)
        self._context = _context(self._provenance, request.world, ontology_revision, selection, mapping)
        self._capability = ExtractionCapability.probe(self._graph.store)
        self._closed = False
        self._verifications = 0
        self.verify_source()

    @property
    def context(self) -> InputContext:
        return self._context

    @property
    def provenance(self) -> ReadViewProvenance:
        return self._provenance

    @property
    def capability(self) -> ExtractionCapability:
        return self._capability

    def _check_binding(self) -> None:
        if self._closed:
            raise ReasoningError("batched claim source is closed")
        if (self._original._graph is not self._graph or self._original.provenance != self._provenance
                or self._original.valid_time != self._time):
            raise ProvenanceMismatchError("source view was rebound to a different context")

    def verify_source(self) -> None:
        self._check_binding()
        _verify_graph(self._provenance, self._graph)
        self._check_binding()
        self._verifications += 1

    def close(self) -> None:
        self._closed = True

    def __enter__(self) -> PinnedBatchedClaimSource:
        self.verify_source()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def _filtered(self) -> Iterator[Evidence]:
        request = self._request
        if request.predicates is not None and len(request.predicates) == 1:
            predicate = request.predicates[0]
            if request.subject is not None:
                query = {"subject": request.subject, "predicate": predicate}
                pushdown = "predicate+subject"
            else:
                query = {"predicate": predicate}
                pushdown = "predicate"
        else:
            query, pushdown = {}, "none"
        self._pushdown = pushdown
        iterator = iter(self._pinned.iter_claims_as_of(
            valid_time=self._time, world=request.world,
            polarity=ClaimPolarity.POSITIVE, object_kind=ClaimObjectKind.ENTITY, **query,
        ))
        count = 0
        while True:
            self._check_binding()
            try:
                version = next(iterator)
            except StopIteration:
                break
            self._check_binding()
            count += 1
            if count > self._limits.max_scanned_claims:
                raise ReasoningLimitError(f"claim extraction exceeded max_scanned_claims={self._limits.max_scanned_claims}")
            if not isinstance(version, ClaimVersion) or not isinstance(version.claim, Claim):
                raise ProvenanceMismatchError("source yielded an invalid claim version")
            claim = version.claim
            if (isinstance(version.commit_id, bool) or not isinstance(version.commit_id, int)
                    or not 0 < version.commit_id <= self._provenance.commit_horizon
                    or not version.valid.contains(self._time) or version.logical_id != claim.claim_id
                    or version.operation not in (VersionOperation.ASSERT, VersionOperation.CORRECT)
                    or claim.world != request.world or claim.polarity is not ClaimPolarity.POSITIVE
                    or claim.object_kind is not ClaimObjectKind.ENTITY):
                raise ProvenanceMismatchError("claim falls outside the pinned input context")
            if not isinstance(version.version_id, str):
                raise ProvenanceMismatchError("claim version ID is invalid")
            try:
                normalize_version_id(version.version_id)
            except ValueError as exc:
                raise ProvenanceMismatchError("claim version ID is invalid") from exc
            if request.predicates is not None and claim.predicate not in request.predicates:
                continue
            if request.subject is not None and claim.subject != request.subject:
                continue
            if request.object is not None and claim.object != request.object:
                continue
            if self._agents is not None and claim.agent not in self._agents:
                continue
            if self._sources is not None and claim.source not in self._sources:
                continue
            marker = _DERIVED_AGENTS.get(claim.agent)
            derived = marker is not None and claim.provenance.get("derived_by") == marker
            if derived and not self._selection.include_derived:
                continue
            self._scanned_yielded = getattr(self, "_scanned_yielded", 0) + 1
            yield Evidence(self._mapping.map(Fact(claim.subject, claim.predicate, claim.object)),
                           version.version_id)
        self.verify_source()

    def iter_batches(self) -> Iterator[FactBatch]:
        """Yield bounded batches; each batch is bracketed by verification."""
        self.verify_source()
        self._pushdown, self._scanned_yielded = "none", 0
        batches, rows, scanned = 0, 0, 0
        for batch in batch_evidence(self._filtered(), self._request):
            batches += 1
            rows += len(batch.evidence)
            self._check_binding()
            yield batch
        self.verify_source()
        self._profile = ExtractionProfile(batches, rows, self._scanned_yielded,
                                          self._verifications, self._pushdown)

    @property
    def profile(self) -> ExtractionProfile:
        """Counters from the most recent completed iter_batches pass."""
        profile = getattr(self, "_profile", None)
        if profile is None:
            raise ReasoningError("no completed batched extraction pass yet")
        return profile

    def evaluate(
        self, compiled: CompiledOntology, *, limits: ReasoningLimits,
        collect_proofs: bool = False,
    ) -> ClaimReasoningResult:
        if not isinstance(compiled, CompiledOntology) or compiled.revision_id != self.context.ontology_revision:
            raise ProvenanceMismatchError("compiled ontology does not match the source revision")
        self.verify_source()

        def flattened() -> Iterator[Evidence]:
            for batch in self.iter_batches():
                yield from batch.evidence

        result = DatalogEngine().evaluate(compiled.program, flattened(), limits=limits,
                                          collect_proofs=collect_proofs)
        self.verify_source()
        return ClaimReasoningResult(self.context, self._provenance, compiled,
                                    self._selection, self._mapping, result)

    def require_pinned_snapshot(self) -> None:
        """Fail closed unless the borrowed store can pin read snapshots."""
        self._capability.require_pinned_snapshot()

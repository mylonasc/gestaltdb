"""Read-only point-scoped claim evidence from an explicitly supplied read view.

This boundary may depend on GraphDB and temporal APIs; neither reasoning nor
ontology implementation imports it. View/database creation belongs to the caller:
the adapter never initializes database identity, repairs indexes or publishes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from types import MappingProxyType
from typing import Iterator, Mapping

from ...epistemic import Claim, ClaimObjectKind, ClaimPolarity
from ...graphdb import GraphDB, _DATABASE_ID_METADATA_KEY
from ...ontology.compiler import CompiledOntology
from ...readview import GraphReadView, ProvenanceMismatchError, ReadViewProvenance
from ...temporal import TemporalInstant
from ...versioning import ClaimVersion, VersionOperation, normalize_version_id
from ..engine import DatalogEngine
from ..model import EvaluationResult, Fact, ReasoningError, ReasoningLimitError, ReasoningLimits, _text
from ..proofs import Explanation
from ..protocols import Evidence, InputContext


_DERIVED_AGENTS = MappingProxyType({
    "gestaltdb:rules": "gestaltdb.rules",
    "gestaltdb:ontology": "gestaltdb.ontology",
})


def _digest(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _selection_values(values, name, *, allow_none=False):
    if values is None:
        return None
    if not isinstance(values, (tuple, list, set, frozenset)):
        raise ReasoningError(f"{name} must be an explicit collection or None")
    for value in values:
        if value is not None or not allow_none:
            _text(value, name)
    return tuple(sorted(set(values), key=lambda value: (value is not None, value or "")))


@dataclass(frozen=True)
class ClaimSelection:
    """None selects all; an empty collection selects none; sources=(None,) is unsourced-only."""

    agents: tuple[str, ...] | None = None
    sources: tuple[str | None, ...] | None = None
    include_derived: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "agents", _selection_values(self.agents, "agents"))
        object.__setattr__(self, "sources", _selection_values(self.sources, "sources", allow_none=True))
        if not isinstance(self.include_derived, bool):
            raise ReasoningError("include_derived must be a bool")

    @property
    def policy_id(self) -> str:
        return _digest(["gestaltdb-claim-selection-v1", self.agents, self.sources, self.include_derived])


@dataclass(frozen=True)
class ClaimMapping:
    """Explicit predicate aliases; unmapped predicates/identifiers are preserved."""

    predicate_aliases: tuple[tuple[str, str], ...] = ()
    _aliases: Mapping[str, str] = field(init=False, repr=False, compare=False, hash=False)

    def __post_init__(self) -> None:
        if not isinstance(self.predicate_aliases, (tuple, list)):
            raise ReasoningError("predicate_aliases must be a tuple or list of pairs")
        aliases = []
        for pair in self.predicate_aliases:
            if not isinstance(pair, (tuple, list)) or len(pair) != 2:
                raise ReasoningError("predicate aliases must be two-item pairs")
            aliases.append((_text(pair[0], "source predicate"), _text(pair[1], "target predicate")))
        if len({source for source, _ in aliases}) != len(aliases):
            raise ReasoningError("source predicate aliases must be unique")
        object.__setattr__(self, "predicate_aliases", tuple(sorted(aliases)))
        object.__setattr__(self, "_aliases", MappingProxyType(dict(aliases)))

    @property
    def mapping_id(self) -> str:
        return _digest(["gestaltdb-claim-mapping-v1", self.predicate_aliases, "preserve-unmapped"])

    def map(self, fact: Fact) -> Fact:
        if not isinstance(fact, Fact):
            raise ReasoningError("mapping input must be a Fact")
        return Fact(fact.subject, self._aliases.get(fact.predicate, fact.predicate), fact.object)


@dataclass(frozen=True)
class ClaimSourceLimits:
    """Bound candidates yielded by the underlying world/positive/entity query.

    Counts before agent/source/derived filtering. Backend-internal index scans
    and authentication costs are not bounded by this counter or an RSS limit.
    """

    max_scanned_claims: int = 100_000

    def __post_init__(self) -> None:
        value = self.max_scanned_claims
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ReasoningError("max_scanned_claims must be a positive integer")


def _context(provenance, world, revision, selection, mapping) -> InputContext:
    return InputContext(
        _digest(["gestaltdb-claim-context-v1", provenance.token, world, revision,
                 selection.policy_id, mapping.mapping_id]),
        world, revision, selection.policy_id, mapping.mapping_id,
    )


def _verify_graph(provenance: ReadViewProvenance, graph: GraphDB) -> None:
    # These two private accesses are confined to this integration boundary.
    # The public database_id getter can initialize identity; do not call it on
    # an uninitialized handle, and do not trust only its cached value on rebinding.
    if not isinstance(graph, GraphDB) or graph._database_id != provenance.database_id:
        raise ProvenanceMismatchError("source database identity is uninitialized or mismatched")
    if graph.store.get_metadata(_DATABASE_ID_METADATA_KEY) != provenance.database_id.encode("ascii"):
        raise ProvenanceMismatchError("persisted source database identity mismatch")
    provenance.verify_source(graph)


@dataclass(frozen=True)
class ClaimReasoningResult:
    """Complete closure plus its exact input policy, point and ontology revision."""

    context: InputContext
    provenance: ReadViewProvenance
    compiled: CompiledOntology
    selection: ClaimSelection
    mapping: ClaimMapping
    result: EvaluationResult

    def __post_init__(self) -> None:
        if not (isinstance(self.context, InputContext) and isinstance(self.provenance, ReadViewProvenance)
                and isinstance(self.compiled, CompiledOntology) and isinstance(self.selection, ClaimSelection)
                and isinstance(self.mapping, ClaimMapping) and isinstance(self.result, EvaluationResult)):
            raise ReasoningError("invalid claim result context or value types")
        provenance = ReadViewProvenance.from_dict(self.provenance.to_dict())
        if self.context != _context(provenance, self.context.world, self.compiled.revision_id,
                                    self.selection, self.mapping):
            raise ProvenanceMismatchError("result context/policy/ontology revision mismatch")
        if self.result.program_digest != self.compiled.program.digest:
            raise ProvenanceMismatchError("result program does not match the compiled ontology")
        object.__setattr__(self, "provenance", provenance)

    @property
    def facts(self) -> tuple[Fact, ...]:
        return self.result.facts

    def __contains__(self, fact: object) -> bool:
        return fact in self.result

    def match(
        self, *, subject: str | None = None, predicate: str | None = None,
        object: str | None = None,
    ) -> tuple[Fact, ...]:
        return self.result.match(subject=subject, predicate=predicate, object=object)

    def explain(self, fact: Fact, *, max_nodes: int = 1000) -> Explanation:
        return self.result.explain(fact, max_nodes=max_nodes)

    def verify_source(self, graph: GraphDB) -> None:
        _verify_graph(self.provenance, graph)


class PinnedClaimSource:
    """Borrow one existing GraphReadView; never create, close or repin its graph.

    Use as a context manager or call close(). Closing invalidates this source and
    its outstanding iterators, not the caller's view/graph or immutable results.
    Source checks bracket iteration and evaluation; per-item checks reject mutable
    view rebinding. Direct partial iteration is not an atomic authenticated batch.
    """

    def __init__(
        self, view: GraphReadView, *, world: str, ontology_revision: str,
        selection: ClaimSelection, mapping: ClaimMapping,
        limits: ClaimSourceLimits = ClaimSourceLimits(),
    ) -> None:
        if type(view) is not GraphReadView:
            raise ReasoningError("view must be a temporal GraphReadView")
        if not isinstance(selection, ClaimSelection) or not isinstance(mapping, ClaimMapping):
            raise ReasoningError("selection and mapping must be explicit claim policy values")
        if not isinstance(limits, ClaimSourceLimits):
            raise ReasoningError("source limits must be ClaimSourceLimits")
        _text(world, "world")
        _text(ontology_revision, "ontology_revision")
        self._original = view
        self._graph = view._graph
        self._provenance = ReadViewProvenance.from_dict(view.provenance.to_dict())
        self._time = TemporalInstant(self._provenance.valid_time_us)
        self._pinned = GraphReadView(self._graph, self._provenance)
        self._selection, self._mapping, self._limits = selection, mapping, limits
        self._agents = None if selection.agents is None else frozenset(selection.agents)
        self._sources = None if selection.sources is None else frozenset(selection.sources)
        self._context = _context(self._provenance, world, ontology_revision, selection, mapping)
        self._closed = False
        self.verify_source()

    @property
    def context(self) -> InputContext:
        return self._context

    @property
    def provenance(self) -> ReadViewProvenance:
        return self._provenance

    def _check_binding(self) -> None:
        if self._closed:
            raise ReasoningError("claim source is closed")
        if (self._original._graph is not self._graph or self._original.provenance != self._provenance
                or self._original.valid_time != self._time):
            raise ProvenanceMismatchError("source view was rebound to a different context")

    def verify_source(self) -> None:
        self._check_binding()
        _verify_graph(self._provenance, self._graph)
        self._check_binding()

    def close(self) -> None:
        self._closed = True

    def __enter__(self) -> PinnedClaimSource:
        self.verify_source()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def iter_evidence(self) -> Iterator[Evidence]:
        self.verify_source()
        iterator = iter(self._pinned.iter_claims_as_of(
            valid_time=self._time, world=self.context.world,
            polarity=ClaimPolarity.POSITIVE, object_kind=ClaimObjectKind.ENTITY,
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
                    or claim.world != self.context.world or claim.polarity is not ClaimPolarity.POSITIVE
                    or claim.object_kind is not ClaimObjectKind.ENTITY):
                raise ProvenanceMismatchError("claim falls outside the pinned input context")
            if not isinstance(version.version_id, str):
                raise ProvenanceMismatchError("claim version ID is invalid")
            try:
                normalize_version_id(version.version_id)
            except ValueError as exc:
                raise ProvenanceMismatchError("claim version ID is invalid") from exc
            selection = self._selection
            if self._agents is not None and claim.agent not in self._agents:
                continue
            if self._sources is not None and claim.source not in self._sources:
                continue
            marker = _DERIVED_AGENTS.get(claim.agent)
            derived = marker is not None and claim.provenance.get("derived_by") == marker
            if derived and not selection.include_derived:
                continue
            yield Evidence(self._mapping.map(Fact(claim.subject, claim.predicate, claim.object)), version.version_id)
        self.verify_source()

    def evaluate(
        self, compiled: CompiledOntology, *, limits: ReasoningLimits,
        collect_proofs: bool = False,
    ) -> ClaimReasoningResult:
        if not isinstance(compiled, CompiledOntology) or compiled.revision_id != self.context.ontology_revision:
            raise ProvenanceMismatchError("compiled ontology does not match the source revision")
        self.verify_source()
        result = DatalogEngine().evaluate(compiled.program, self.iter_evidence(), limits=limits,
                                         collect_proofs=collect_proofs)
        # Iteration verification alone does not cover mutations during inference.
        self.verify_source()
        return ClaimReasoningResult(self.context, self._provenance, compiled, self._selection, self._mapping, result)

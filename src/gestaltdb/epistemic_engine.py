"""Epistemic claim service: sourced assertions, status, and modal evaluation.

``EpistemicEngine`` owns claim writes, claim selection, and modal entailment on
top of one :class:`~gestaltdb.temporal_engine.TemporalEngine`. ``GraphDB``
constructs one engine per handle and exposes it as ``graph.epistemic``; it
keeps no claim wrapper methods. Internal consumers (Cypher ``kg.entails``,
reasoning adapters) take the epistemic engine or an already-pinned read view,
never the full graph facade.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterator, Mapping

if TYPE_CHECKING:
    from .temporal_engine import TemporalEngine
    from .readview import GraphReadView

from .epistemic import Claim, ClaimObjectKind, ClaimPolarity, ClaimStatus
from .modal import ACCESSIBILITY_PREDICATES, AccessibilityKind, ModalEntailmentResult
from .ingestion import IndexMaintenanceMode
from .temporal import TemporalIntervalInput
from .versioning import ClaimVersion
from ._graph.temporal_types import Hydrator, PointInput
from ._graph import temporal_entity_writes as _entity_writes
from ._graph.temporal_selection import _UNSET


class EpistemicEngine:
    """Own claim capabilities bound to one temporal scope."""

    def __init__(self, temporal: TemporalEngine) -> None:
        self._temporal: TemporalEngine = temporal

    # -- scope binding ----------------------------------------------------

    @property
    def temporal(self) -> TemporalEngine:
        """The backing temporal engine; scope-owned, borrowed for this handle."""
        return self._temporal

    def rebound(self, temporal: TemporalEngine) -> EpistemicEngine:
        """Bind a sibling engine to a new temporal scope."""
        return EpistemicEngine(temporal)

    def spawn_transaction_scope(self, temporal: TemporalEngine) -> EpistemicEngine:
        """Bind a transaction-child engine to a new temporal scope."""
        return EpistemicEngine(temporal)

    # -- claim writes ------------------------------------------------------

    def assert_claim(self, *, subject: str, predicate: str, object: object, polarity: ClaimPolarity | str, agent: str,
                     source: str | None = None, confidence: float | None = None, world: str = "default",
                     provenance: Mapping[str, object] | None = None, object_kind: ClaimObjectKind | str | None = None, valid: TemporalIntervalInput | None = None,
                     valid_from: PointInput | None = None, valid_to: PointInput | None = None, version_id: str | None = None,
                     metadata: Mapping[str, object] | None = None, index_mode: str | IndexMaintenanceMode = "maintain") -> ClaimVersion:
        """Append an immutable sourced assertion or denial.

        Examples:
            Assert and check a sourced claim (requires ``lmdb``)::

                >>> from tempfile import TemporaryDirectory
                >>> from gestaltdb.epistemic import ClaimStatus
                >>> from gestaltdb.graphdb import GraphDB
                >>> with TemporaryDirectory() as root:
                ...     graph = GraphDB.create(root + "/db", backend="lmdb", serializer="json")
                ...     try:
                ...         _ = graph.epistemic.assert_claim(subject="alice", predicate="member",
                ...             object="team", polarity="positive", agent="registry", valid=(0, 100))
                ...         assert graph.epistemic.claim_status("alice", "member", "team",
                ...             valid_time=50) is ClaimStatus.SUPPORTED
                ...     finally:
                ...         graph.close()
        """
        if object_kind is None:
            object_kind = ClaimObjectKind.ENTITY
        return _entity_writes.assert_claim(
            self._temporal.commit_versions, subject=subject,
            predicate=predicate, object=object, polarity=polarity,
            agent=agent, source=source, confidence=confidence, world=world,
            provenance=provenance, object_kind=object_kind, valid=valid,
            valid_from=valid_from, valid_to=valid_to, version_id=version_id,
            metadata=metadata, index_mode=index_mode,
        )

    def correct_claim(self, claim: Claim | str, *, supersedes_version_id: str,
                      confidence: object = _UNSET, provenance: object = _UNSET, valid: TemporalIntervalInput | None = None,
                      valid_from: PointInput | None = None, valid_to: PointInput | None = None, version_id: str | None = None,
                      metadata: Mapping[str, object] | None = None, index_mode: str | IndexMaintenanceMode = "maintain") -> ClaimVersion:
        """Append corrected confidence/provenance for one claim identity."""
        return _entity_writes.correct_claim(
            self._temporal.reader(), self._temporal.commit_versions, claim,
            supersedes_version_id=supersedes_version_id,
            confidence=confidence, provenance=provenance, valid=valid,
            valid_from=valid_from, valid_to=valid_to, version_id=version_id,
            metadata=metadata, index_mode=index_mode,
        )

    def retract_claim(self, claim_id: str, *, valid: TemporalIntervalInput | None = None, valid_from: PointInput | None = None,
                      valid_to: PointInput | None = None, supersedes_version_id: str | None = None, reason: str | None = None,
                      version_id: str | None = None, metadata: Mapping[str, object] | None = None, index_mode: str | IndexMaintenanceMode = "maintain") -> ClaimVersion:
        """Append a bitemporal retraction without deleting claim history."""
        return _entity_writes.retract_claim(
            self._temporal.commit_versions, claim_id, valid=valid,
            valid_from=valid_from, valid_to=valid_to,
            supersedes_version_id=supersedes_version_id, reason=reason,
            version_id=version_id, metadata=metadata, index_mode=index_mode,
        )

    def assert_world_accessibility(self, *, agent: str, from_world: str, to_world: str, kind: AccessibilityKind | str,
                                   valid: TemporalIntervalInput | None = None, valid_from: PointInput | None = None, valid_to: PointInput | None = None,
                                   source: str | None = None, provenance: Mapping[str, object] | None = None,
                                   version_id: str | None = None, metadata: Mapping[str, object] | None = None,
                                   index_mode: str | IndexMaintenanceMode = "maintain") -> ClaimVersion:
        """Append a positive temporal accessibility fact for one agent frame."""
        try:
            frame = AccessibilityKind(kind)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "kind must be 'belief', 'knowledge', or 'modal'") from exc
        return self.assert_claim(
            subject=from_world, predicate=ACCESSIBILITY_PREDICATES[frame],
            object=to_world, polarity=ClaimPolarity.POSITIVE, agent=agent,
            source=source, world=from_world,
            provenance={} if provenance is None else provenance,
            valid=valid, valid_from=valid_from, valid_to=valid_to,
            version_id=version_id, metadata=metadata, index_mode=index_mode)

    # -- claim reads -------------------------------------------------------

    def get_claim_version(self, version_id: str, *, system_time: PointInput | None = None,
                           through_commit: int | None = None) -> ClaimVersion | None:
        """Return a visible epistemic claim version by UUID."""
        return self._temporal.history().get_claim_version(
            version_id, system_time=system_time, through_commit=through_commit)

    def iter_claim_versions(self, claim_id: str | None = None, *, system_time: PointInput | None = None,
                             through_commit: int | None = None) -> Iterator[ClaimVersion]:
        """Iterate claim versions, optionally restricted to one claim ID."""
        return self._temporal.history().iter_claim_versions(
            claim_id, system_time=system_time, through_commit=through_commit)

    def get_claim_as_of(self, claim_id: str, *, valid_time: PointInput, system_time: PointInput | None = None,
                         through_commit: int | None = None, hydrate: Hydrator | None = None) -> ClaimVersion | None:
        """Resolve one claim identity at valid time and a system horizon."""
        return self._temporal.history(hydrate=hydrate).get_claim_as_of(
            claim_id, valid_time=valid_time, system_time=system_time,
            through_commit=through_commit)

    def iter_claims_as_of(self, **filters: Any) -> Iterator[ClaimVersion]:
        """Iterate visible claims matching indexed epistemic dimensions."""
        return self._temporal.history().iter_claims_as_of(**filters)

    def claim_status(self, subject: str, predicate: str, object: object, **filters: Any) -> ClaimStatus:
        """Return supported/refuted/both/unknown under open-world semantics."""
        return self._temporal.history().claim_status(
            subject, predicate, object, **filters)

    # -- modal evaluation ---------------------------------------------------

    def entails(self, view: GraphReadView, agent: str, proposition: object, mode: str, *, world: str,
                max_depth: int = 4, max_states: int = 1_000) -> ModalEntailmentResult:
        """Evaluate a bounded modal proposition under one pinned read view.

        Takes an already-pinned view instead of constructing one, so the
        caller selects the horizon explicitly and the engine never reaches
        back into the owning graph handle.
        """
        from .modal import ModalExpression, ModalOperator, evaluate_modal

        try:
            operator = ModalOperator(str(mode).upper())
        except ValueError as exc:
            raise ValueError(
                "mode must be BELIEVES, KNOWS, POSSIBLE, or NECESSARY") from exc
        if operator not in {
            ModalOperator.BELIEVES, ModalOperator.KNOWS,
            ModalOperator.POSSIBLE, ModalOperator.NECESSARY,
        }:
            raise ValueError(
                "mode must be BELIEVES, KNOWS, POSSIBLE, or NECESSARY")
        expression = ModalExpression.from_value({
            "operator": operator.value,
            "agent": agent,
            "formula": proposition,
        })
        return evaluate_modal(
            view, expression, world=world,
            max_depth=max_depth, max_states=max_states,
        )

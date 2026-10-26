"""Epistemic claim service: sourced assertions, status, and modal evaluation.

``EpistemicEngine`` owns claim writes, claim selection, and modal entailment on
top of one :class:`~gestaltdb.temporal_engine.TemporalEngine`. ``GraphDB``
constructs one engine per handle and exposes it as ``graph.epistemic``; it
keeps no claim wrapper methods. Internal consumers (Cypher ``kg.entails``,
reasoning adapters) take the epistemic engine or an already-pinned read view,
never the full graph facade.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .temporal_engine import TemporalEngine

from .epistemic import ClaimObjectKind, ClaimPolarity
from .modal import ACCESSIBILITY_PREDICATES, AccessibilityKind
from ._graph import temporal_entity_writes as _entity_writes
from ._graph.temporal_selection import _UNSET


class EpistemicEngine:
    """Own claim capabilities bound to one temporal scope."""

    def __init__(self, temporal):
        self._temporal: TemporalEngine = temporal

    # -- scope binding ----------------------------------------------------

    @property
    def temporal(self):
        """The backing temporal engine; scope-owned, borrowed for this handle."""
        return self._temporal

    def rebound(self, temporal):
        """Bind a sibling engine to a new temporal scope."""
        return EpistemicEngine(temporal)

    def spawn_transaction_scope(self, temporal):
        """Bind a transaction-child engine to a new temporal scope."""
        return EpistemicEngine(temporal)

    # -- claim writes ------------------------------------------------------

    def assert_claim(self, *, subject, predicate, object, polarity, agent,
                     source=None, confidence=None, world="default",
                     provenance=None, object_kind=None, valid=None,
                     valid_from=None, valid_to=None, version_id=None,
                     metadata=None, index_mode="maintain"):
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

    def correct_claim(self, claim, *, supersedes_version_id,
                      confidence=_UNSET, provenance=_UNSET, valid=None,
                      valid_from=None, valid_to=None, version_id=None,
                      metadata=None, index_mode="maintain"):
        """Append corrected confidence/provenance for one claim identity."""
        return _entity_writes.correct_claim(
            self._temporal.reader(), self._temporal.commit_versions, claim,
            supersedes_version_id=supersedes_version_id,
            confidence=confidence, provenance=provenance, valid=valid,
            valid_from=valid_from, valid_to=valid_to, version_id=version_id,
            metadata=metadata, index_mode=index_mode,
        )

    def retract_claim(self, claim_id, *, valid=None, valid_from=None,
                      valid_to=None, supersedes_version_id=None, reason=None,
                      version_id=None, metadata=None, index_mode="maintain"):
        """Append a bitemporal retraction without deleting claim history."""
        return _entity_writes.retract_claim(
            self._temporal.commit_versions, claim_id, valid=valid,
            valid_from=valid_from, valid_to=valid_to,
            supersedes_version_id=supersedes_version_id, reason=reason,
            version_id=version_id, metadata=metadata, index_mode=index_mode,
        )

    def assert_world_accessibility(self, *, agent, from_world, to_world, kind,
                                   valid=None, valid_from=None, valid_to=None,
                                   source=None, provenance=None,
                                   version_id=None, metadata=None,
                                   index_mode="maintain"):
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

    def get_claim_version(self, version_id, *, system_time=None,
                           through_commit=None):
        """Return a visible epistemic claim version by UUID."""
        return self._temporal.history().get_claim_version(
            version_id, system_time=system_time, through_commit=through_commit)

    def iter_claim_versions(self, claim_id=None, *, system_time=None,
                             through_commit=None):
        """Iterate claim versions, optionally restricted to one claim ID."""
        return self._temporal.history().iter_claim_versions(
            claim_id, system_time=system_time, through_commit=through_commit)

    def get_claim_as_of(self, claim_id, *, valid_time, system_time=None,
                         through_commit=None, hydrate=None):
        """Resolve one claim identity at valid time and a system horizon."""
        return self._temporal.history(hydrate=hydrate).get_claim_as_of(
            claim_id, valid_time=valid_time, system_time=system_time,
            through_commit=through_commit)

    def iter_claims_as_of(self, **filters):
        """Iterate visible claims matching indexed epistemic dimensions."""
        return self._temporal.history().iter_claims_as_of(**filters)

    def claim_status(self, subject, predicate, object, **filters):
        """Return supported/refuted/both/unknown under open-world semantics."""
        return self._temporal.history().claim_status(
            subject, predicate, object, **filters)

    # -- modal evaluation ---------------------------------------------------

    def entails(self, view, agent, proposition, mode, *, world,
                max_depth=4, max_states=1_000):
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

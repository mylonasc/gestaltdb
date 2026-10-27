"""Positive Horn rule service: catalog, fixpoint evaluation, maintenance.

``RulesEngine`` owns rule versions, bounded stratified evaluation, truth
maintenance, and derivation explanations on top of one
:class:`~gestaltdb.temporal_engine.TemporalEngine`. ``GraphDB`` constructs one
engine per handle and exposes it as ``graph.rules``; it keeps no rule wrapper
methods. Reasoning adapters consume the rules engine (or the temporal engine
directly) instead of the full graph facade.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable, Iterator

if TYPE_CHECKING:
    from .temporal_engine import TemporalEngine

from ._graph import stored_rules as _stored_rules
from ._graph.temporal_types import PointInput
from .ingestion import IndexMaintenanceMode
from .rules import ClaimExplanation, RuleRunResult, RuleVersion, TruthMaintenanceResult
from .temporal import TemporalInstant
from ._graph import truth_maintenance as _truth_maintenance
from ._graph.truth_maintenance import (
    TRUTH_MAINTENANCE_STATE_KEY as _TRUTH_STATE_KEY,
)


class RulesEngine:
    """Own rule capabilities bound to one temporal scope."""

    def __init__(self, temporal: TemporalEngine, *, truth_state_key: bytes | None = None) -> None:
        self._temporal: TemporalEngine = temporal
        self._truth_state_key = (
            _TRUTH_STATE_KEY if truth_state_key is None else truth_state_key
        )

    # -- scope binding ----------------------------------------------------

    @property
    def temporal(self) -> TemporalEngine:
        """The backing temporal engine; scope-owned, borrowed for this handle."""
        return self._temporal

    def rebound(self, temporal: TemporalEngine) -> RulesEngine:
        """Bind a sibling engine to a new temporal scope."""
        return RulesEngine(temporal, truth_state_key=self._truth_state_key)

    def spawn_transaction_scope(self, temporal: TemporalEngine) -> RulesEngine:
        """Bind a transaction-child engine to a new temporal scope."""
        return RulesEngine(temporal, truth_state_key=self._truth_state_key)

    # -- catalog -----------------------------------------------------------

    def load_catalog(self) -> tuple[RuleVersion, ...]:
        """Load validated rule versions in catalog order."""
        return _stored_rules.load_rule_catalog(self._temporal.store)

    def create_rule(self, rule_id: str, *, when: Iterable[object], then: object, not_when: Iterable[object] = (),
                    closed_world: Iterable[object] = (), version_id: str | None = None) -> RuleVersion:
        """Validate and append one immutable version of a Horn rule."""
        return _stored_rules.create_rule(
            self._temporal, rule_id, when=when, then=then,
            not_when=not_when, closed_world=closed_world,
            version_id=version_id,
        )

    def iter_rule_versions(self, rule_id: str | None = None, *, system_time: PointInput | None = None) -> Iterator[RuleVersion]:
        """Iterate immutable rule versions at a system-time horizon."""
        return _stored_rules.iter_rule_versions(
            self._temporal.store, rule_id, system_time=system_time)

    def get_rule(self, rule_id: str, *, system_time: PointInput | None = None) -> RuleVersion | None:
        """Return the latest version of a named rule at a horizon."""
        return _stored_rules.get_rule(
            self._temporal.store, rule_id, system_time=system_time)

    def program(self, *, system_time: PointInput | None) -> tuple[tuple[RuleVersion, ...], tuple[tuple[str, ...], ...]]:
        """Return active rule versions and their stratification schedule."""
        return _stored_rules.rule_program(
            self._temporal.store, system_time=system_time)

    # -- evaluation --------------------------------------------------------

    def run(self, *, as_of: PointInput, system_time: PointInput | None = None, world: str | None = None,
            max_iterations: int = 100, max_derivations: int = 10_000,
            max_justifications: int = 100_000, index_mode: str | IndexMaintenanceMode = "maintain") -> RuleRunResult:
        """Evaluate active rules semi-naively and persist a bounded fixpoint.

        Examples:
            Persist a conclusion, explain it, and maintain it after evidence
            loss (requires ``lmdb``)::

                >>> from tempfile import TemporaryDirectory
                >>> from gestaltdb.graphdb import GraphDB
                >>> with TemporaryDirectory() as root:
                ...     graph = GraphDB.create(root + "/db", backend="lmdb", serializer="json")
                ...     try:
                ...         base = graph.epistemic.assert_claim(subject="alice", predicate="member",
                ...             object="team", agent="registry", polarity="positive", valid=(0, 100))
                ...         _ = graph.rules.create_rule("classify", when=[("?x", "member", "?team")],
                ...             then=("?x", "is", "person"))
                ...         _ = graph.rules.run(as_of=50)
                ...         conclusion = next(graph.epistemic.iter_claims_as_of(valid_time=50, predicate="is"))
                ...         assert graph.rules.explain(conclusion.logical_id, valid_time=50) is not None
                ...         _ = graph.epistemic.retract_claim(base.logical_id, valid=(0, 100))
                ...         _ = graph.rules.maintain(as_of=50)
                ...         assert not tuple(graph.epistemic.iter_claims_as_of(valid_time=50, predicate="is"))
                ...     finally:
                ...         graph.close()
        """
        return _stored_rules.run_rules(
            self._temporal, as_of=as_of, system_time=system_time,
            world=world, max_iterations=max_iterations,
            max_derivations=max_derivations,
            max_justifications=max_justifications, index_mode=index_mode,
        )

    def last_maintenance_instant(self) -> TemporalInstant | None:
        """Return the valid time of the most recent run or maintenance pass."""
        return _truth_maintenance.last_truth_maintenance_instant(
            self._temporal, self._truth_state_key)

    def maintain(self, *, as_of: PointInput | None = None, system_time: PointInput | None = None, world: str | None = None,
                 max_iterations: int = 100, max_derivations: int = 10_000,
                 max_justifications: int = 100_000, index_mode: str | IndexMaintenanceMode = "maintain") -> TruthMaintenanceResult:
        """Reconcile derived claims with the current rule fixpoint."""
        return _truth_maintenance.maintain_truth(
            self._temporal, self._truth_state_key, as_of=as_of,
            system_time=system_time, world=world,
            max_iterations=max_iterations, max_derivations=max_derivations,
            max_justifications=max_justifications, index_mode=index_mode,
        )

    def explain(self, claim_id: str, *, valid_time: PointInput, system_time: PointInput | None = None,
                max_depth: int = 100, max_nodes: int = 10_000) -> ClaimExplanation | None:
        """Return a finite derivation graph for a visible historical claim."""
        return _truth_maintenance.explain_claim(
            self._temporal, claim_id=claim_id, valid_time=valid_time,
            system_time=system_time, max_depth=max_depth, max_nodes=max_nodes,
        )

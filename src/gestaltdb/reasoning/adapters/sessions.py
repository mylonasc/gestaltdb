"""Resumable incremental reasoning sessions over exact commit deltas.

A session pins one database, program, rule catalog, world/selection/mapping
scope and source horizon, then advances over the exact versions committed
since its horizon. Asserted versions become typed facts through an explicit
claim mapping; corrections retire their superseded version atomically;
retractions retire the named versions. Logical insertions propagate through
existing supports and logical retractions use DRed against the pinned
dependency index. Unknown history (a delta referencing versions the session
never observed) rebuilds explicitly from a full scan instead of guessing.

This first milestone advances in memory with an explicit rebuild fallback.
Durable cross-process persistence of session state lives in
``gestaltdb.reasoning.adapters.session_store``: a session
is a value, and ``advance_session`` returns the next value. Callers persist
it if they need resumes across restarts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ...epistemic import Claim, ClaimObjectKind, ClaimPolarity
from ...graphdb import GraphDB
from ...readview import ProvenanceMismatchError
from ...temporal import TemporalInstant
from ...versioning import ClaimVersion, VersionOperation
from ..catalog import RuleCatalog
from ..evidence_ledger import AssertionVersion, EvidenceLedger
from ..maintenance import propagate_insertions, rematerialize, retract_facts
from ..maintenance import DependencyIndex
from ..model import ReasoningError, ReasoningLimitError, ReasoningLimits, _text, check_cancelled
from ..typed import GenericProgram, TypedFact, _sort_key
from .claims import ClaimMapping, ClaimSelection, _DERIVED_AGENTS


@dataclass(frozen=True)
class SessionScope:
    """Pinned world, valid time, source policy and mapping for one session."""

    world: str
    valid_time_us: int
    selection: ClaimSelection
    mapping: ClaimMapping

    def __post_init__(self) -> None:
        _text(self.world, "world")
        if isinstance(self.valid_time_us, bool) or not isinstance(self.valid_time_us, int):
            raise ReasoningError("valid_time_us must be an integer")
        if not isinstance(self.selection, ClaimSelection) or not isinstance(self.mapping, ClaimMapping):
            raise ReasoningError("selection and mapping must be explicit claim policy values")


@dataclass(frozen=True)
class ReasoningSession:
    """Pinned generation plus exact asserted versions and maintained closure."""

    program: GenericProgram
    catalog: RuleCatalog
    scope: SessionScope
    database_id: str
    commit_horizon: int
    ledger: EvidenceLedger
    version_logical: tuple[tuple[str, str], ...]
    facts: tuple[TypedFact, ...]
    index: DependencyIndex

    def __post_init__(self) -> None:
        if not isinstance(self.program, GenericProgram) or not isinstance(self.catalog, RuleCatalog):
            raise ReasoningError("program and catalog required")
        if not isinstance(self.scope, SessionScope):
            raise ReasoningError("scope must be a SessionScope")
        _text(self.database_id, "database_id")
        if isinstance(self.commit_horizon, bool) or not isinstance(self.commit_horizon, int) or self.commit_horizon < 1:
            raise ReasoningError("commit_horizon must be a positive integer")
        if not isinstance(self.ledger, EvidenceLedger):
            raise ReasoningError("ledger must be an EvidenceLedger")
        for pair in self.version_logical:
            if not isinstance(pair, (tuple, list)) or len(pair) != 2:
                raise ReasoningError("version_logical must contain (version_id, logical_id) pairs")
            _text(pair[0], "version_id")
            _text(pair[1], "logical_id")
        object.__setattr__(self, "version_logical", tuple(sorted(set(self.version_logical))))
        if not isinstance(self.facts, (tuple, list)) or not all(isinstance(f, TypedFact) for f in self.facts):
            raise ReasoningError("facts must contain only TypedFact values")
        object.__setattr__(self, "facts", tuple(sorted(set(self.facts), key=_sort_key)))
        if not isinstance(self.index, DependencyIndex):
            raise ReasoningError("index must be a DependencyIndex")


@dataclass(frozen=True)
class SessionAdvance:
    """One advance outcome: the next session plus logical deltas."""

    session: ReasoningSession
    inserted: tuple[TypedFact, ...]
    retracted: tuple[TypedFact, ...]
    rebuilt: bool


class _SkipVersion(Exception):
    pass


def _typed_fact(claim: Claim, mapping: ClaimMapping) -> TypedFact:
    predicate = mapping._aliases.get(claim.predicate, claim.predicate)
    return TypedFact(predicate, (claim.subject, claim.object))


def _check_version(version: ClaimVersion, horizon: int, instant: TemporalInstant,
                   scope: SessionScope, agents, sources) -> Claim:
    """Mirror the pinned claim-source admission policy for session scope."""
    if not isinstance(version, ClaimVersion) or not isinstance(version.claim, Claim):
        raise ProvenanceMismatchError("source yielded an invalid claim version")
    claim = version.claim
    if (isinstance(version.commit_id, bool) or not isinstance(version.commit_id, int)
            or not 0 < version.commit_id <= horizon
            or version.logical_id != claim.claim_id
            or version.operation not in (VersionOperation.ASSERT, VersionOperation.CORRECT)):
        raise ProvenanceMismatchError("claim falls outside the pinned session scope")
    if (not version.valid.contains(instant) or claim.world != scope.world
            or claim.polarity is not ClaimPolarity.POSITIVE
            or claim.object_kind is not ClaimObjectKind.ENTITY):
        # Valid but out-of-scope input (another world, a different validity
        # window, non-entity content): skip, do not ingest.
        raise _SkipVersion()
    if not isinstance(version.version_id, str):
        raise ProvenanceMismatchError("claim version ID is invalid")
    if agents is not None and claim.agent not in agents:
        raise _SkipVersion()
    if sources is not None and claim.source not in sources:
        raise _SkipVersion()
    marker = _DERIVED_AGENTS.get(claim.agent)
    if marker is not None and claim.provenance.get("derived_by") == marker and not scope.selection.include_derived:
        raise _SkipVersion()
    return claim


def _filters(scope: SessionScope):
    agents = None if scope.selection.agents is None else frozenset(scope.selection.agents)
    sources = None if scope.selection.sources is None else frozenset(scope.selection.sources)
    return agents, sources


def _full_scan(graph: GraphDB, scope: SessionScope, horizon: int, *,
               max_versions: int, cancel: Callable[[], bool] | None = None) -> dict[str, tuple[str, TypedFact]]:
    """Collect active (version_id -> (logical_id, fact)) triples at one horizon."""
    instant = TemporalInstant(scope.valid_time_us)
    agents, sources = _filters(scope)
    seen: dict[str, tuple[str, TypedFact]] = {}
    with graph.read_view(valid_time=instant) as view:
        iterator = iter(view.iter_claims_as_of(world=scope.world, polarity=ClaimPolarity.POSITIVE,
                                               object_kind=ClaimObjectKind.ENTITY))
        count = 0
        while True:
            check_cancelled(cancel)
            try:
                version = next(iterator)
            except StopIteration:
                break
            count += 1
            if count > max_versions:
                raise ReasoningLimitError("session scan exceeded max_versions")
            if not isinstance(version, ClaimVersion):
                raise ProvenanceMismatchError("source yielded an invalid claim version")
            try:
                claim = _check_version(version, horizon, instant, scope, agents, sources)
            except _SkipVersion:
                continue
            seen[version.version_id] = (version.logical_id, _typed_fact(claim, scope.mapping))
    return seen


def _materialize_session(program: GenericProgram, catalog: RuleCatalog, scope: SessionScope,
                         database_id: str, horizon: int, seen: dict[str, tuple[str, TypedFact]],
                         limits: ReasoningLimits, cancel=None) -> ReasoningSession:
    ledger = EvidenceLedger(f"{database_id}/{scope.world}",
                            [AssertionVersion(version_id, fact) for version_id, (_, fact) in seen.items()])
    materialized = rematerialize(program, ledger.facts, limits=limits, cancel=cancel)
    return ReasoningSession(program, catalog, scope, database_id, horizon, ledger,
                            tuple((version_id, logical_id) for version_id, (logical_id, _) in seen.items()),
                            materialized.facts, materialized.index)


def start_session(graph: GraphDB, program: GenericProgram, catalog: RuleCatalog, *,
                  scope: SessionScope, limits: ReasoningLimits, max_versions: int = 100_000,
                  cancel: Callable[[], bool] | None = None) -> ReasoningSession:
    """Pin the current horizon and materialize one session from a full scan.

    A session is reasoning state, not a connection: it fixes a valid-time point,
    world, program/catalog, and commit horizon. ``advance_session`` returns new
    state; it does not mutate the original or publish inferred claims.

    Examples:
        Advance and persist a classification after new evidence (requires LMDB)::

            >>> from tempfile import TemporaryDirectory
            >>> from gestaltdb.graphdb import GraphDB
            >>> from gestaltdb.reasoning.model import ReasoningLimits, Variable
            >>> from gestaltdb.reasoning.typed import GenericProgram, GenericRule, TypedAtom, TypedFact
            >>> from gestaltdb.reasoning.adapters.claims import ClaimMapping, ClaimSelection
            >>> from gestaltdb.reasoning.adapters.sessions import SessionScope, start_session, advance_session
            >>> from gestaltdb.reasoning.adapters.session_generation import catalog_for_typed_program
            >>> from gestaltdb.reasoning.adapters.session_store import save_session, load_session
            >>> x, y = Variable("x"), Variable("y")
            >>> program = GenericProgram([GenericRule("r", [TypedAtom("member", [x, y])], [], TypedAtom("person", [x]))])
            >>> scope = SessionScope("default", 50, ClaimSelection(), ClaimMapping())
            >>> with TemporaryDirectory() as root:
            ...     graph = GraphDB.create(root + "/db", backend="lmdb", serializer="json")
            ...     try:
            ...         _ = graph.assert_claim(subject="alice", predicate="member", object="team",
            ...             agent="registry", polarity="positive", valid=(0, 100))
            ...         session = start_session(graph, program, catalog_for_typed_program(program),
            ...             scope=scope, limits=ReasoningLimits())
            ...         digest = save_session(session, root + "/session.json")
            ...         _ = graph.assert_claim(subject="bob", predicate="member", object="team",
            ...             agent="registry", polarity="positive", valid=(0, 100))
            ...         moved = advance_session(graph, session, limits=ReasoningLimits())
            ...         _ = save_session(moved.session, root + "/session.json", expected_session_digest=digest)
            ...         assert TypedFact("person", ["bob"]) in load_session(root + "/session.json").facts
            ...     finally:
            ...         graph.close()

    This demonstrates persistence and exact-file-state comparison. Source-verified
    durable resume additionally requires caller-held generation/state pins; see
    the reasoning-authenticated-sessions guide for the complete trusted-pin flow.
    """
    if not isinstance(graph, GraphDB):
        raise ReasoningError("GraphDB is required")
    if not isinstance(program, GenericProgram) or not isinstance(catalog, RuleCatalog):
        raise ReasoningError("program and catalog required")
    if not isinstance(scope, SessionScope):
        raise ReasoningError("scope must be a SessionScope")
    if not isinstance(limits, ReasoningLimits):
        raise ReasoningError("limits must be ReasoningLimits")
    if isinstance(max_versions, bool) or not isinstance(max_versions, int) or max_versions < 1:
        raise ReasoningError("max_versions must be a positive integer")
    missing = {rule.rule_id for rule in program.rules} - {entry.rule_key for entry in catalog.entries}
    if missing:
        raise ReasoningError(f"catalog does not cover program rules: {sorted(missing)}")
    instant = TemporalInstant(scope.valid_time_us)
    with graph.read_view(valid_time=instant) as view:
        database_id = view.provenance.database_id
        horizon = view.provenance.commit_horizon
    seen = _full_scan(graph, scope, horizon, max_versions=max_versions, cancel=cancel)
    return _materialize_session(program, catalog, scope, database_id, horizon, seen, limits, cancel)


def advance_session(graph: GraphDB, session: ReasoningSession, *, limits: ReasoningLimits,
                    max_versions: int = 100_000, max_advance_commits: int = 10_000,
                    cancel: Callable[[], bool] | None = None) -> SessionAdvance:
    """Consume exact commit deltas since the pinned horizon, or rebuild."""
    if not isinstance(graph, GraphDB):
        raise ReasoningError("GraphDB is required")
    if not isinstance(session, ReasoningSession):
        raise ReasoningError("session must be a ReasoningSession")
    if not isinstance(limits, ReasoningLimits):
        raise ReasoningError("limits must be ReasoningLimits")
    for name, value in (("max_versions", max_versions), ("max_advance_commits", max_advance_commits)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ReasoningError(f"{name} must be a positive integer")
    scope = session.scope
    instant = TemporalInstant(scope.valid_time_us)
    with graph.read_view(valid_time=instant) as view:
        if view.provenance.database_id != session.database_id:
            raise ReasoningError("session belongs to a different database")
        current = view.provenance.commit_horizon
    if current < session.commit_horizon:
        raise ReasoningError("source horizon moved backwards; re-stage explicitly")
    if current == session.commit_horizon:
        return SessionAdvance(session, (), (), False)
    if current - session.commit_horizon > max_advance_commits:
        return _rebuild(graph, session, current, limits=limits, max_versions=max_versions, cancel=cancel)
    agents, sources = _filters(scope)
    assertions: list[AssertionVersion] = []
    retirements: list[str] = []
    logical_of = dict(session.version_logical)
    for commit_id in range(session.commit_horizon + 1, current + 1):
        check_cancelled(cancel)
        commit = graph.temporal.get_temporal_commit(commit_id, _validate_supersession=False)
        if commit is None:
            return _rebuild(graph, session, current, limits=limits, max_versions=max_versions, cancel=cancel)
        for version in commit.versions:
            check_cancelled(cancel)
            if not isinstance(version, ClaimVersion):
                continue
            if version.operation == VersionOperation.RETRACT:
                # A retraction only wins at points inside its half-open valid
                # interval. Outside it, the pinned assertion remains active.
                if not version.valid.contains(instant):
                    continue
                # Retractions are logical: they retire every active version of
                # the named claim, however the retraction names it. Naming an
                # unobserved version or an unknown logical ID means foreign or
                # pre-history input, which is skipped, never guessed.
                matches = [vid for vid, logical in logical_of.items()
                           if logical == version.logical_id]
                retirements.extend(matches)
                for vid in matches:
                    del logical_of[vid]
                continue
            try:
                claim = _check_version(version, current, instant, scope, agents, sources)
            except _SkipVersion:
                continue
            if version.operation == VersionOperation.CORRECT:
                # A correction replaces the whole logical claim: retire every
                # active version under its identity, then assert the new one.
                matches = [vid for vid, logical in logical_of.items()
                           if logical == version.logical_id]
                retirements.extend(matches)
                for vid in matches:
                    del logical_of[vid]
            logical_of[version.version_id] = version.logical_id
            assertions.append(AssertionVersion(version.version_id, _typed_fact(claim, scope.mapping)))
    try:
        transition = session.ledger.transition(scope_id=session.ledger.scope_id,
                                               assertions=assertions, retractions=retirements,
                                               max_versions=max_versions)
    except (ReasoningError, ReasoningLimitError):
        return _rebuild(graph, session, current, limits=limits, max_versions=max_versions, cancel=cancel)
    facts = set(session.facts)
    index = session.index
    if transition.retracted:
        removed = retract_facts(session.program, session.facts, transition.retracted,
                                limits=limits, index=index, cancel=cancel)
        facts = set(removed.facts)
        index = removed.index
    if transition.inserted:
        propagated = propagate_insertions(session.program, sorted(facts, key=_sort_key),
                                          transition.inserted, limits=limits, index=index, cancel=cancel)
        facts = set(propagated.facts)
        index = propagated.index
    active = {item.version_id for item in transition.ledger.versions} - set(transition.ledger.retired)
    if set(logical_of) != active:
        raise ReasoningError("session version map diverged from the ledger; rebuild explicitly")
    updated = ReasoningSession(session.program, session.catalog, scope, session.database_id,
                               current, transition.ledger,
                               tuple((vid, logical_of[vid]) for vid in sorted(active)),
                               tuple(sorted(facts, key=_sort_key)), index)
    return SessionAdvance(updated, transition.inserted, transition.retracted, False)


def _rebuild(graph: GraphDB, session: ReasoningSession, horizon: int, *, limits: ReasoningLimits,
             max_versions: int, cancel=None) -> SessionAdvance:
    """Explicit full-scan rematerialization; never a guessed repair."""
    before = set(session.facts)
    seen = _full_scan(graph, session.scope, horizon, max_versions=max_versions, cancel=cancel)
    rebuilt = _materialize_session(session.program, session.catalog, session.scope,
                                   session.database_id, horizon, seen, limits, cancel)
    after = set(rebuilt.facts)
    return SessionAdvance(rebuilt, tuple(sorted(after - before, key=_sort_key)),
                          tuple(sorted(before - after, key=_sort_key)), True)

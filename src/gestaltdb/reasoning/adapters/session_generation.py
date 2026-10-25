"""Authenticated typed-point admission; metadata is never a closure proof.

Admission independently reads the exact historical source, including alternative
assertions, before binding a session. It does not recompute or certify closure.
Mutable graph snapshots, temporal ranges and compact dictionaries are not admitted.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...epistemic import ClaimObjectKind, ClaimPolarity
from ...graphdb import GraphDB
from ...readview import ProvenanceMismatchError
from ...temporal import TemporalInstant
from ..catalog import CatalogEntry, RuleCatalog
from ..generation import GenerationIdentity, TemporalSourceIdentity
from ..executors import TYPED_PROFILE, TYPED_DICTIONARY
from ..maintenance import _check_supported
from ..model import ReasoningError, ReasoningLimitError, check_cancelled
from ..typed import GenericProgram
from .claims import _context
from .generation import ClaimGenerationBinding
from .session_store import _digest, session_from_dict, session_to_dict
from .sessions import ReasoningSession, SessionScope, _check_version, _filters, _SkipVersion, _typed_fact


TYPED_SESSION_PROFILE = TYPED_PROFILE
TYPED_SESSION_DICTIONARY = TYPED_DICTIONARY


@dataclass(frozen=True)
class SessionAdmissionLimits:
    """Bound retained state, yielded candidates (before policy), and commit prefix.

    Backend-internal scans, envelope sizes and native calls are not RSS/work
    limits. Cancellation is cooperative between calls, never inside a kernel.
    """

    max_versions: int = 100_000
    max_facts: int = 100_000
    max_supports: int = 100_000
    max_commits: int = 10_000

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ReasoningError(f"{name} must be a positive integer")


def catalog_for_typed_program(program: GenericProgram) -> RuleCatalog:
    """Catalog positive typed rules by single-rule GenericProgram content digest.

    Version IDs default to the content digest. External catalogs may supply
    different version IDs/ordinals, but must cover exactly the same rule content.
    """
    if not isinstance(program, GenericProgram):
        raise ReasoningError("GenericProgram required")
    _check_supported(program)
    entries = []
    for rule in program.rules:
        digest = GenericProgram([rule]).digest
        entries.append(CatalogEntry(rule.rule_id, digest, digest, 1))
    return RuleCatalog(tuple(entries))


def _validate_session(session, limits, cancel):
    if not isinstance(session, ReasoningSession):
        raise ReasoningError("ReasoningSession required")
    if not isinstance(limits, SessionAdmissionLimits):
        raise ReasoningError("limits must be SessionAdmissionLimits")
    check_cancelled(cancel)
    for size, maximum, name in (
        (len(session.ledger.versions), limits.max_versions, "max_versions"),
        (len(session.version_logical), limits.max_versions, "max_versions"),
        (len(session.facts), limits.max_facts, "max_facts"),
        (len(session.index.supports), limits.max_supports, "max_supports"),
        (session.commit_horizon, limits.max_commits, "max_commits"),
    ):
        if size > maximum:
            raise ReasoningLimitError(f"session admission exceeded {name}")
    expected = catalog_for_typed_program(session.program)
    if {e.rule_key: e.content_digest for e in session.catalog.entries} != {
            e.rule_key: e.content_digest for e in expected.entries}:
        raise ReasoningError("catalog content does not match the typed program")
    payload = session_to_dict(session)
    digest = _digest(payload)
    # Reuse durable-session consistency checks rather than a weaker second
    # definition of index/ledger/fact admission. No closure proof is implied.
    session_from_dict({**payload, "session_digest": digest},
                      max_versions=limits.max_versions, max_supports=limits.max_supports)
    if session.ledger.scope_id != f"{session.database_id}/{session.scope.world}":
        raise ReasoningError("session ledger scope mismatch")
    check_cancelled(cancel)
    return digest


@dataclass(frozen=True)
class SessionGenerationBinding:
    """Source-verified metadata and an exact session-state pin, not a proof.

    Constructing this value manually does not authenticate anything. Use
    bind_session_generation; verify() rechecks the state pin and live source.
    """

    source_binding: ClaimGenerationBinding
    scope: SessionScope
    session_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.source_binding, ClaimGenerationBinding) or not isinstance(self.scope, SessionScope):
            raise ReasoningError("ClaimGenerationBinding and SessionScope required")
        identity = self.identity
        if (identity.semantics_profile != TYPED_SESSION_PROFILE
                or identity.dictionary_id != TYPED_SESSION_DICTIONARY
                or identity.source.valid_time_us != self.scope.valid_time_us
                or self.source_binding.selection != self.scope.selection
                or self.source_binding.mapping != self.scope.mapping
                or self.source_binding.extraction_request is not None
                or identity.context != _context(self.provenance, self.scope.world,
                                               identity.program_digest,
                                               self.scope.selection, self.scope.mapping)):
            raise ProvenanceMismatchError("typed session generation binding mismatch")
        if not isinstance(self.session_digest, str) or not self.session_digest.startswith("sha256:"):
            raise ReasoningError("session_digest must be a sha256 digest")

    @property
    def identity(self) -> GenerationIdentity:
        return self.source_binding.identity

    @property
    def provenance(self):
        return self.source_binding.provenance

    def verify(self, graph: GraphDB, session: ReasoningSession, *, freshness: str = "latest",
               limits: SessionAdmissionLimits = SessionAdmissionLimits(), cancel=None) -> None:
        """Verify state compatibility and source authentication, never publish."""
        if _validate_session(session, limits, cancel) != self.session_digest:
            raise ProvenanceMismatchError("session state drifted; rebind explicitly")
        # Re-admission also independently verifies active source assertions.
        rebound = bind_session_generation(graph, session, freshness=freshness, limits=limits, cancel=cancel)
        self.identity.require_compatible(rebound.identity)


def bind_session_generation(graph: GraphDB, session: ReasoningSession, *, freshness: str = "latest",
                            limits: SessionAdmissionLimits = SessionAdmissionLimits(),
                            cancel=None) -> SessionGenerationBinding:
    """Authenticate typed point input against the exact source prefix.

    Historical admission is explicit; latest rejects unrelated commits too.
    Source/program/catalog/scope/profile/dictionary mismatches require rebind
    or rebuild, never silent recoding. No database writes are performed.
    """
    if not isinstance(graph, GraphDB):
        raise ReasoningError("GraphDB required")
    if freshness not in ("latest", "historical"):
        raise ReasoningError("freshness must be latest or historical")
    digest = _validate_session(session, limits, cancel)
    scope = session.scope
    instant = TemporalInstant(scope.valid_time_us)
    agents, sources = _filters(scope)
    expected_versions = {}
    expected_logical = {}
    with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=instant, through_commit=session.commit_horizon) as view:
        provenance = view.provenance
        if (provenance.database_id != session.database_id
                or provenance.commit_horizon != session.commit_horizon):
            raise ProvenanceMismatchError("session source database/horizon mismatch")
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
            if count > limits.max_versions:
                raise ReasoningLimitError("session admission exceeded max_versions candidates")
            try:
                claim = _check_version(version, session.commit_horizon, instant, scope, agents, sources)
            except _SkipVersion:
                continue
            if version.version_id in expected_versions:
                raise ProvenanceMismatchError("source repeated an assertion version")
            expected_versions[version.version_id] = _typed_fact(claim, scope.mapping)
            expected_logical[version.version_id] = version.logical_id
        retired = set(session.ledger.retired)
        active = {item.version_id: item.fact for item in session.ledger.versions
                  if item.version_id not in retired}
        if active != expected_versions or dict(session.version_logical) != expected_logical:
            raise ProvenanceMismatchError("session evidence does not match authenticated source")
        identity = GenerationIdentity(
            TemporalSourceIdentity(provenance.database_id, provenance.commit_horizon,
                                   provenance.token, provenance.valid_time_us),
            _context(provenance, scope.world, session.program.digest, scope.selection, scope.mapping),
            session.program.digest, session.catalog.digest, TYPED_SESSION_PROFILE, TYPED_SESSION_DICTIONARY)
        source_binding = ClaimGenerationBinding(identity, provenance, scope.selection, scope.mapping)
        source_binding.verify_source(graph, freshness=freshness)
        check_cancelled(cancel)
    return SessionGenerationBinding(source_binding, scope, digest)

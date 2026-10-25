"""Explicit source-authenticated durable session consumers; v1 files unchanged.

Exact caller generation/state pins are checked before advance or persistence.
Source authentication is not an independent proof of the supplied closure.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...graphdb import GraphDB
from ..generation import GenerationIdentity
from ..model import ReasoningError, ReasoningLimits, check_cancelled
from .session_generation import SessionAdmissionLimits, SessionGenerationBinding, bind_session_generation
from .session_store import load_session, save_session, session_to_dict, _digest, _session_file_guard
from .sessions import ReasoningSession, SessionAdvance, advance_session


@dataclass(frozen=True)
class AuthenticatedSession:
    session: ReasoningSession
    binding: SessionGenerationBinding


@dataclass(frozen=True)
class AuthenticatedSessionAdvance:
    advance: SessionAdvance
    binding: SessionGenerationBinding


def _validate(graph, expected_generation, expected_session_digest, admission_limits, freshness, cancel):
    if not isinstance(graph, GraphDB) or not isinstance(expected_generation, GenerationIdentity):
        raise ReasoningError("GraphDB and exact expected GenerationIdentity required")
    if not isinstance(admission_limits, SessionAdmissionLimits):
        raise ReasoningError("SessionAdmissionLimits required")
    if (not isinstance(expected_session_digest, str) or len(expected_session_digest) != 71
            or not expected_session_digest.startswith("sha256:")
            or any(c not in "0123456789abcdef" for c in expected_session_digest[7:])):
        raise ReasoningError("exact expected_session_digest must be a sha256 digest")
    if freshness not in ("latest", "historical"):
        raise ReasoningError("freshness must be latest or historical")
    check_cancelled(cancel)


def _load(graph, path, expected_generation, expected_session_digest, admission_limits, max_bytes, freshness, cancel):
    session = load_session(path, max_versions=admission_limits.max_versions,
                           max_supports=admission_limits.max_supports, max_bytes=max_bytes)
    check_cancelled(cancel)
    if _digest(session_to_dict(session)) != expected_session_digest:
        raise ReasoningError("persisted session state does not match expected_session_digest")
    binding = bind_session_generation(graph, session, freshness=freshness, limits=admission_limits, cancel=cancel)
    expected_generation.require_compatible(binding.identity)
    return AuthenticatedSession(session, binding)


def load_authenticated_session(graph: GraphDB, path, *, expected_generation: GenerationIdentity,
                                expected_session_digest: str, freshness: str = "latest",
                                admission_limits: SessionAdmissionLimits = SessionAdmissionLimits(),
                                max_bytes: int = 256_000_000, cancel=None) -> AuthenticatedSession:
    """Load v1 bytes and independently verify the exact source and caller state pin.

    Atomic file replacement permits old-or-new reads. This read-only operation
    creates no file lock; it does not promise the file/source cannot move after
    return. Use the guarded advance API for persistence.

    The caller must obtain the expected generation and session digest from
    trusted application state. For a complete create/bind/save/load/advance
    example, including how to retain those pins, see
    :doc:`reasoning-authenticated-sessions`. Adopting pins from the file being
    checked would defeat the independent source/state comparison.
    """
    _validate(graph, expected_generation, expected_session_digest, admission_limits, freshness, cancel)
    return _load(graph, path, expected_generation, expected_session_digest,
                 admission_limits, max_bytes, freshness, cancel)


def advance_authenticated_session(graph: GraphDB, path, *, expected_generation: GenerationIdentity,
                                   expected_session_digest: str, limits: ReasoningLimits,
                                   freshness: str = "latest",
                                   admission_limits: SessionAdmissionLimits = SessionAdmissionLimits(),
                                   max_advance_commits: int = 10_000,
                                   max_bytes: int = 256_000_000, cancel=None) -> AuthenticatedSessionAdvance:
    """Authenticate old state, advance to latest, authenticate new state, then CAS.

    Explicit freshness='historical' is needed when the persisted source horizon
    has advanced. It admits the old input, never a stale new output: the new
    session is always verified against latest under the database writer guard.
    Lock ordering is database writer guard, then cooperating session-file lock.
    No database writes, publication, implicit program/catalog change or recoding.

    See :doc:`reasoning-authenticated-sessions` for the complete runnable
    trusted-pin workflow; ``start_session`` has the smaller introductory example
    for in-memory advancement and file persistence without source admission.
    """
    _validate(graph, expected_generation, expected_session_digest, admission_limits, freshness, cancel)
    if not isinstance(limits, ReasoningLimits):
        raise ReasoningError("ReasoningLimits required")
    store, serializer = graph.store, graph.serializer
    def check():
        check_cancelled(cancel)
        if graph.store is not store or graph.serializer is not serializer:
            raise ReasoningError("session source store/decoder was rebound")
    with graph._temporal_writer_guard(), _session_file_guard(path):
        check()
        loaded = _load(graph, path, expected_generation, expected_session_digest,
                       admission_limits, max_bytes, freshness, cancel)
        outcome = advance_session(graph, loaded.session, limits=limits,
            max_versions=admission_limits.max_versions, max_advance_commits=max_advance_commits, cancel=cancel)
        check()
        binding = bind_session_generation(graph, outcome.session, freshness="latest", limits=admission_limits, cancel=cancel)
        check()
        def before_save():
            check()
            binding.source_binding.verify_source(graph, freshness="latest")
            if graph.store is not store or graph.serializer is not serializer:
                raise ReasoningError("session source store/decoder was rebound")
            return False
        # Exact file state is rechecked by save_session under the same lock;
        # same-horizon edits and deletion cannot pass as an unchanged input.
        save_session(outcome.session, path, expected_horizon=loaded.session.commit_horizon,
                     expected_session_digest=expected_session_digest, max_bytes=max_bytes, cancel=before_save)
        return AuthenticatedSessionAdvance(outcome, binding)

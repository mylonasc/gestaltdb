"""Durable cross-process persistence for incremental reasoning sessions.

A persisted session is a versioned JSON document with an embedded
``session_digest`` binding database identity, commit horizon, scope, program,
catalog, evidence ledger, version map, closure facts and maintenance supports.
Writes are atomic (temporary file plus :func:`os.replace`); incomplete files
are never visible under the target name. Loading verifies the format version,
the session digest, program/catalog digests, the dependency-index binding and
ledger/version-map consistency before returning a value.

Crash/retry contract: ``save_session`` optionally checks ``expected_horizon``
against the file already present; ``advance_persisted_session`` loads, verifies
the live database, advances through exact commit deltas (or explicit rebuild),
then persists with a compare of the previously loaded horizon. A concurrent
writer changing the file between load and persist fails closed instead of
silently merging. Callers retry from a fresh load; old-or-new file visibility
holds because replacement is atomic.

Bounds: ``max_versions`` bounds ledger entries, ``max_supports`` bounds stored
supports, ``max_bytes`` bounds the encoded document. Unknown formats, digest
mismatches, conflicting version IDs, unsupported snapshots and backwards
horizons fail closed. Persistence never authenticates data by itself: resume
always re-verifies the live database identity and horizon.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Callable

from ...graphdb import GraphDB, _TemporalWriterLock
from ..catalog import RuleCatalog
from ..evidence_ledger import AssertionVersion, EvidenceLedger
from ..maintenance import DependencyIndex, Support
from ..model import ReasoningError, ReasoningLimitError, ReasoningLimits, Variable, _text, check_cancelled
from ..terms import Literal
from ..typed import (
    AggregateRule,
    Aggregation,
    Comparison,
    GenericProgram,
    GenericRule,
    TypedAtom,
    TypedFact,
    _sort_key,
)
from .claims import ClaimMapping, ClaimSelection
from .sessions import (
    ReasoningSession,
    SessionAdvance,
    SessionScope,
    advance_session,
)

SESSION_FORMAT = "gestaltdb-reasoning-session-v1"
SESSION_FORMAT_VERSION = 1
_SESSION_FILE_LOCKS = {}
_SESSION_FILE_LOCKS_GUARD = threading.Lock()


def _digest(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _encode_term(value: object) -> list:
    if isinstance(value, Variable):
        return ["variable", value.name]
    if isinstance(value, Literal):
        return ["literal", value.kind, value.lexical]
    return ["entity", _text(value, "term value")]


def _decode_term(raw: object) -> object:
    if not isinstance(raw, list) or len(raw) not in (2, 3):
        raise ReasoningError("invalid encoded term")
    kind = raw[0]
    if kind == "variable":
        if len(raw) != 2:
            raise ReasoningError("invalid variable encoding")
        return Variable(str(raw[1]))
    if kind == "literal":
        if len(raw) != 3 or raw[1] not in (
            "boolean", "integer", "float", "text", "bytes", "instant", "null",
        ):
            raise ReasoningError("invalid literal encoding")
        if not isinstance(raw[2], str):
            raise ReasoningError("literal lexical forms must be str")
        return Literal.from_lexical(str(raw[1]), raw[2])
    if kind == "entity":
        if len(raw) != 2:
            raise ReasoningError("invalid entity encoding")
        return _text(raw[1], "entity value")
    raise ReasoningError(f"unknown term kind: {kind!r}")


def _encode_predicate(predicate: object) -> object:
    if isinstance(predicate, Variable):
        return ["variable", predicate.name]
    return _text(predicate, "atom predicate")


def _decode_predicate(raw: object) -> object:
    if isinstance(raw, list):
        term = _decode_term(raw)
        if not isinstance(term, Variable):
            raise ReasoningError("atom predicate lists must encode a variable")
        return term
    return _text(raw, "atom predicate")


def _encode_atom(atom: TypedAtom) -> dict:
    return {
        "predicate": _encode_predicate(atom.predicate),
        "args": [_encode_term(term) for term in atom.args],
    }


def _decode_atom(raw: object) -> TypedAtom:
    if not isinstance(raw, dict):
        raise ReasoningError("atom must be a mapping")
    try:
        predicate = _decode_predicate(raw["predicate"])
        args = [_decode_term(term) for term in raw["args"]]
    except KeyError as exc:
        raise ReasoningError(f"atom missing field: {exc}") from exc
    return TypedAtom(predicate, tuple(args))


def _encode_comparison(item: Comparison) -> dict:
    return {"op": item.op, "left": _encode_term(item.left), "right": _encode_term(item.right)}


def _decode_comparison(raw: object) -> Comparison:
    if not isinstance(raw, dict):
        raise ReasoningError("comparison must be a mapping")
    try:
        left = _decode_term(raw["left"])
        right = _decode_term(raw["right"])
        op = str(raw["op"])
    except KeyError as exc:
        raise ReasoningError(f"comparison missing field: {exc}") from exc
    if not isinstance(left, (Variable, Literal)) or not isinstance(right, (Variable, Literal)):
        raise ReasoningError("comparison sides must be variables or literals")
    return Comparison(op, left, right)


def _encode_fact(fact: TypedFact) -> dict:
    return {"predicate": fact.predicate, "values": [_encode_term(v) for v in fact.values]}


def _decode_fact(raw: object) -> TypedFact:
    if not isinstance(raw, dict):
        raise ReasoningError("fact must be a mapping")
    try:
        predicate = _text(raw["predicate"], "fact predicate")
        values = [_decode_term(term) for term in raw["values"]]
    except KeyError as exc:
        raise ReasoningError(f"fact missing field: {exc}") from exc
    return TypedFact(predicate, tuple(values))


def _encode_rule(rule: GenericRule) -> dict:
    return {
        "rule_id": rule.rule_id,
        "body": [_encode_atom(atom) for atom in rule.body],
        "filters": [_encode_comparison(item) for item in rule.filters],
        "head": _encode_atom(rule.head),
        "negated": [_encode_atom(atom) for atom in rule.negated],
    }


def _decode_rule(raw: object) -> GenericRule:
    if not isinstance(raw, dict):
        raise ReasoningError("rule must be a mapping")
    try:
        return GenericRule(
            _text(raw["rule_id"], "rule_id"),
            tuple(_decode_atom(atom) for atom in raw["body"]),
            tuple(_decode_comparison(item) for item in raw.get("filters", [])),
            _decode_atom(raw["head"]),
            tuple(_decode_atom(atom) for atom in raw.get("negated", [])),
        )
    except KeyError as exc:
        raise ReasoningError(f"rule missing field: {exc}") from exc


def _encode_aggregation(item: Aggregation) -> dict:
    return {"op": item.op, "source": item.source.name, "target": item.target.name}


def _decode_aggregation(raw: object) -> Aggregation:
    if not isinstance(raw, dict):
        raise ReasoningError("aggregation must be a mapping")
    try:
        return Aggregation(str(raw["op"]), Variable(str(raw["source"])), Variable(str(raw["target"])))
    except KeyError as exc:
        raise ReasoningError(f"aggregation missing field: {exc}") from exc


def _encode_aggregate_rule(rule: AggregateRule) -> dict:
    return {
        "rule_id": rule.rule_id,
        "body": [_encode_atom(atom) for atom in rule.body],
        "group": [variable.name for variable in rule.group],
        "aggregations": [_encode_aggregation(item) for item in rule.aggregations],
        "head": _encode_atom(rule.head),
    }


def _decode_aggregate_rule(raw: object) -> AggregateRule:
    if not isinstance(raw, dict):
        raise ReasoningError("aggregate rule must be a mapping")
    try:
        return AggregateRule(
            _text(raw["rule_id"], "rule_id"),
            tuple(_decode_atom(atom) for atom in raw["body"]),
            tuple(Variable(str(name)) for name in raw.get("group", [])),
            tuple(_decode_aggregation(item) for item in raw["aggregations"]),
            _decode_atom(raw["head"]),
        )
    except KeyError as exc:
        raise ReasoningError(f"aggregate rule missing field: {exc}") from exc


def _encode_program(program: GenericProgram) -> dict:
    return {
        "rules": [_encode_rule(rule) for rule in program.rules],
        "aggregates": [_encode_aggregate_rule(rule) for rule in program.aggregates],
        "closed": list(program.closed),
    }


def _decode_program(raw: object) -> GenericProgram:
    if not isinstance(raw, dict):
        raise ReasoningError("program must be a mapping")
    try:
        rules = tuple(_decode_rule(item) for item in raw.get("rules", []))
        aggregates = tuple(_decode_aggregate_rule(item) for item in raw.get("aggregates", []))
        closed = tuple(_text(name, "closed predicate") for name in raw.get("closed", []))
    except KeyError as exc:
        raise ReasoningError(f"program missing field: {exc}") from exc
    return GenericProgram(rules, aggregates, closed)


def _encode_support(item: Support) -> dict:
    return {
        "fact": _encode_fact(item.fact),
        "rule_id": item.rule_id,
        "premises": [_encode_fact(premise) for premise in item.premises],
    }


def _decode_support(raw: object) -> Support:
    if not isinstance(raw, dict):
        raise ReasoningError("support must be a mapping")
    try:
        return Support(
            _decode_fact(raw["fact"]),
            _text(raw["rule_id"], "rule_id"),
            tuple(_decode_fact(premise) for premise in raw["premises"]),
        )
    except KeyError as exc:
        raise ReasoningError(f"support missing field: {exc}") from exc


def session_to_dict(session: ReasoningSession) -> dict:
    """Encode a session deterministically; digest is computed on load/save."""
    if not isinstance(session, ReasoningSession):
        raise ReasoningError("session must be a ReasoningSession")
    return {
        "format": SESSION_FORMAT,
        "format_version": SESSION_FORMAT_VERSION,
        "database_id": session.database_id,
        "commit_horizon": session.commit_horizon,
        "program_digest": session.program.digest,
        "catalog_digest": session.catalog.digest,
        "scope": {
            "world": session.scope.world,
            "valid_time_us": session.scope.valid_time_us,
            "selection": {
                "agents": None if session.scope.selection.agents is None else list(session.scope.selection.agents),
                "sources": None if session.scope.selection.sources is None else list(session.scope.selection.sources),
                "include_derived": session.scope.selection.include_derived,
            },
            "mapping": {"predicate_aliases": [list(pair) for pair in session.scope.mapping.predicate_aliases]},
        },
        "program": _encode_program(session.program),
        "catalog": session.catalog.to_dict(),
        "ledger": {
            "scope_id": session.ledger.scope_id,
            "versions": [
                {"version_id": item.version_id, "fact": _encode_fact(item.fact)}
                for item in session.ledger.versions
            ],
            "retired": list(session.ledger.retired),
        },
        "version_logical": [list(pair) for pair in session.version_logical],
        "facts": [_encode_fact(fact) for fact in session.facts],
        "index": {
            "program_digest": session.index.program_digest,
            "horizon": session.index.horizon,
            "supports": [_encode_support(item) for item in session.index.supports],
        },
    }


def session_from_dict(data: object, *, max_versions: int = 100_000, max_supports: int = 100_000) -> ReasoningSession:
    """Rebuild a session; every identity and consistency check must pass."""
    for name, value in (("max_versions", max_versions), ("max_supports", max_supports)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ReasoningError(f"{name} must be a positive integer")
    if not isinstance(data, dict):
        raise ReasoningError("session document must be a mapping")
    if data.get("format") != SESSION_FORMAT or data.get("format_version") != SESSION_FORMAT_VERSION:
        raise ReasoningError(f"unsupported session format: {data.get('format')!r} v{data.get('format_version')!r}")
    try:
        stored_digest = _text(data["session_digest"], "session_digest")
        payload = {key: value for key, value in data.items() if key != "session_digest"}
        if _digest(payload) != stored_digest:
            raise ReasoningError("session digest mismatch; file is corrupt or tampered")
        database_id = _text(data["database_id"], "database_id")
        horizon = data["commit_horizon"]
        if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 1:
            raise ReasoningError("commit_horizon must be a positive integer")
        raw_scope = data["scope"]
        selection_raw = raw_scope["selection"]
        selection = ClaimSelection(
            agents=selection_raw["agents"],
            sources=selection_raw["sources"],
            include_derived=bool(selection_raw["include_derived"]),
        )
        mapping = ClaimMapping(tuple(tuple(pair) for pair in raw_scope["mapping"]["predicate_aliases"]))
        scope = SessionScope(
            world=_text(raw_scope["world"], "world"),
            valid_time_us=int(raw_scope["valid_time_us"]),
            selection=selection,
            mapping=mapping,
        )
        program = _decode_program(data["program"])
        catalog = RuleCatalog.from_dict(data["catalog"])
        raw_ledger = data["ledger"]
        ledger = EvidenceLedger(
            _text(raw_ledger["scope_id"], "scope_id"),
            tuple(
                AssertionVersion(_text(item["version_id"], "version_id"), _decode_fact(item["fact"]))
                for item in raw_ledger["versions"]
            ),
            tuple(_text(key, "retired version ID") for key in raw_ledger["retired"]),
        )
        version_logical = tuple(
            (_text(pair[0], "version_id"), _text(pair[1], "logical_id")) for pair in data["version_logical"]
        )
        facts = tuple(_decode_fact(item) for item in data["facts"])
        raw_index = data["index"]
        index = DependencyIndex(
            _text(raw_index["program_digest"], "program_digest"),
            _text(raw_index["horizon"], "horizon"),
            tuple(_decode_support(item) for item in raw_index["supports"]),
        )
    except KeyError as exc:
        raise ReasoningError(f"session document missing field: {exc}") from exc
    if _text(data["program_digest"], "program_digest") != program.digest:
        raise ReasoningError("program digest drifted since this session was persisted")
    if _text(data["catalog_digest"], "catalog_digest") != catalog.digest:
        raise ReasoningError("catalog digest drifted since this session was persisted")
    if len(ledger.versions) > max_versions or len(version_logical) > max_versions:
        raise ReasoningLimitError("persisted session exceeds max_versions")
    if len(index.supports) > max_supports:
        raise ReasoningLimitError("persisted session exceeds max_supports")
    session = ReasoningSession(program, catalog, scope, database_id, horizon, ledger, version_logical, facts, index)
    if index.program_digest != program.digest:
        raise ReasoningError("dependency index was built for a different program; rebuild explicitly")
    from ..typed import _digest_facts

    if index.horizon != _digest_facts(sorted(facts, key=_sort_key)):
        raise ReasoningError("dependency index horizon does not match persisted facts; rebuild explicitly")
    active = {item.version_id for item in ledger.versions} - set(ledger.retired)
    if set(dict(version_logical)) != active or len(set(dict(version_logical))) != len(version_logical):
        raise ReasoningError("version map diverged from the ledger; rebuild explicitly")
    if not set(ledger.facts) <= set(facts):
        raise ReasoningError("ledger facts are not a subset of persisted closure facts")
    known = set(facts)
    for item in index.supports:
        if item.fact not in known or any(premise not in known for premise in item.premises):
            raise ReasoningError("persisted supports reference unknown facts; rebuild explicitly")
    return session


def _encoded_bytes(session: ReasoningSession) -> bytes:
    payload = session_to_dict(session)
    payload["session_digest"] = _digest(payload)
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return encoded


def _session_file_guard(path):
    """Cooperating writers share the existing cross-process lock primitive.

    Resolve the lock identity, not the data path's replacement semantics.
    Direct filesystem writers and hardlink aliases are outside this contract.
    """
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_name(f".{target.name}.session.lock")
    with _SESSION_FILE_LOCKS_GUARD:
        return _SESSION_FILE_LOCKS.setdefault(str(lock_path), _TemporalWriterLock(lock_path))


def _read_state(path: Path, max_bytes: int):
    try:
        with open(path, "rb") as handle:
            raw = handle.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ReasoningLimitError("persisted session exceeds max_bytes")
        data = json.loads(raw.decode("utf-8"))
    except FileNotFoundError:
        return None
    except ReasoningLimitError:
        raise
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise ReasoningError(f"cannot read existing session file: {exc}") from exc
    if not isinstance(data, dict) or data.get("format") != SESSION_FORMAT:
        raise ReasoningError("existing session file has an unsupported format")
    horizon = data.get("commit_horizon")
    if isinstance(horizon, bool) or not isinstance(horizon, int):
        raise ReasoningError("existing session file has an invalid horizon")
    stored = data.pop("session_digest", None)
    if not isinstance(stored, str) or stored != _digest(data):
        raise ReasoningError("existing session digest mismatch; recover explicitly")
    return horizon, stored


def save_session(
    session: ReasoningSession,
    path: str | os.PathLike,
    *,
    expected_horizon: int | None = None,
    expected_session_digest: str | None = None,
    max_bytes: int = 256_000_000,
    cancel: Callable[[], bool] | None = None,
) -> str:
    """Persist one session atomically; return its session digest.

    Cooperating writers serialize through a sidecar file lock. Optional
    ``expected_session_digest`` compares exact state, including same-horizon
    changes and deletion; horizon-only pins retain their legacy behavior.
    Checksums/locks do not authenticate the database or prove closure soundness.
    """
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ReasoningError("max_bytes must be a positive integer")
    if expected_horizon is not None:
        if isinstance(expected_horizon, bool) or not isinstance(expected_horizon, int):
            raise ReasoningError("expected_horizon must be an integer or None")
    if expected_session_digest is not None:
        _text(expected_session_digest, "expected_session_digest")
    check_cancelled(cancel)
    target = Path(path)
    encoded = _encoded_bytes(session)
    if len(encoded) > max_bytes:
        raise ReasoningLimitError("persisted session exceeds max_bytes")
    def compare():
        state = _read_state(target, max_bytes)
        if expected_session_digest is not None and (state is None or state[1] != expected_session_digest):
            raise ReasoningError("session file state changed underneath this writer; reload and retry")
        if expected_horizon is not None and state is not None and state[0] != expected_horizon:
            raise ReasoningError("session file changed underneath this writer; reload and retry")
    with _session_file_guard(target):
        check_cancelled(cancel)
        compare()
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", dir=target.parent, prefix=f".{target.name}.tmp.", delete=False) as handle:
                tmp = Path(handle.name)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            check_cancelled(cancel)
            compare()
            os.replace(tmp, target)
            tmp = None
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
        try:
            dir_fd = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        except OSError:
            pass  # Some platforms cannot open/fsync directories.
        else:
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    return json.loads(encoded.decode("utf-8"))["session_digest"]


def load_session(
    path: str | os.PathLike, *, max_versions: int = 100_000, max_supports: int = 100_000,
    max_bytes: int = 256_000_000,
) -> ReasoningSession:
    """Load and fully verify a persisted session file."""
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ReasoningError("max_bytes must be a positive integer")
    try:
        with open(path, "rb") as handle:
            raw = handle.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ReasoningLimitError("persisted session exceeds max_bytes")
        data = json.loads(raw.decode("utf-8"))
    except FileNotFoundError as exc:
        raise ReasoningError(f"session file not found: {path}") from exc
    except ReasoningLimitError:
        raise
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise ReasoningError(f"cannot decode session file: {exc}") from exc
    return session_from_dict(data, max_versions=max_versions, max_supports=max_supports)


def advance_persisted_session(
    graph: GraphDB,
    path: str | os.PathLike,
    *,
    limits: ReasoningLimits,
    max_versions: int = 100_000,
    max_advance_commits: int = 10_000,
    max_supports: int = 100_000,
    max_bytes: int = 256_000_000,
    expected_horizon: int | None = None,
    cancel: Callable[[], bool] | None = None,
) -> SessionAdvance:
    """Resume a persisted session in a new process, advance, and re-persist.

    The loaded session is verified against the live database identity; a
    foreign database, backwards horizon, unknown program/catalog drift or
    corrupt file fails closed. The advanced (or explicitly rebuilt) session is
    written back atomically with a horizon compare against the loaded value.
    """
    if not isinstance(graph, GraphDB):
        raise ReasoningError("GraphDB is required")
    if not isinstance(limits, ReasoningLimits):
        raise ReasoningError("limits must be ReasoningLimits")
    session = load_session(path, max_versions=max_versions, max_supports=max_supports)
    if expected_horizon is not None and session.commit_horizon != expected_horizon:
        raise ReasoningError("persisted session horizon does not match expected_horizon; reload and retry")
    outcome = advance_session(
        graph,
        session,
        limits=limits,
        max_versions=max_versions,
        max_advance_commits=max_advance_commits,
        cancel=cancel,
    )
    save_session(outcome.session, path, expected_horizon=session.commit_horizon,
                 expected_session_digest=_digest(session_to_dict(session)), max_bytes=max_bytes, cancel=cancel)
    return outcome

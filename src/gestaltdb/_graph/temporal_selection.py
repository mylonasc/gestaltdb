"""Point-in-time and claim selection over committed history (Phase 5 extraction).

These queries read through an explicit ``TemporalReader`` bound to one
store scope: validity-interval locators, entity-as-of resolution, claim
selection with grouped commit validation, and version-history iteration.
The grouped-read tripwire and hydration hooks stay explicit parameters so
facade-level instrumentation keeps working.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..epistemic import Claim, ClaimObjectKind, ClaimPolarity, ClaimStatus, claim_statement_id
from ..versioning import (
    ClaimVersion,
    EdgeVersion,
    NodeVersion,
    TemporalCorruptionError,
    VersionOperation,
    canonical_json_bytes,
    normalize_logical_id,
    normalize_temporal_instant,
)
from . import temporal_indexes as _temporal_indexes
from .temporal_keys import (
    decode_temporal_locator,
    temporal_instant_index_value,
    temporal_locator,
    temporal_visible_key,
)
from .temporal_reads import TemporalReader


#: Sentinel for "argument not provided", distinct from ``None``.
_UNSET = object()


def interval_locators(reader, index_name, parts, instant):
    """Return index locators started but not yet ended at an instant."""
    generation = _temporal_indexes.index_state_generation(reader.store, reader.state_key)
    encoded = temporal_instant_index_value(instant)
    started = set(reader.store.iter_range_index(
        _temporal_indexes.index_name(index_name, generation),
        parts,
        None,
        encoded,
        True,
        True,
    ))
    if not started:
        return ()
    unexpired = set(reader.store.iter_range_index(
        _temporal_indexes.index_name(_temporal_indexes.VALID_END_INDEX, generation),
        [index_name.encode("ascii"), *parts],
        encoded,
        None,
        False,
        True,
    ))
    return tuple(sorted(started.intersection(unexpired)))

def entity_as_of(reader, kind, logical_id, *, valid_time, system_time=None,
                 through_commit=None, hydrate=None):
    """Resolve one logical node/edge/claim version at a valid time.

    ``hydrate`` decodes one locator; callers pass the facade hook so
    instrumentation of version hydration keeps working.
    """
    hydrate = hydrate or reader.get_version_at
    logical_id = normalize_logical_id(logical_id)
    instant = normalize_temporal_instant(valid_time)
    horizon = reader.system_horizon(system_time=system_time, through_commit=through_commit)
    _temporal_indexes.ensure_temporal_indexes(
        reader.store, reader.state_key, reader.stale_key, horizon)
    kind_bytes = {"node": b"n", "edge": b"e", "claim": b"c"}[kind]
    locators = interval_locators(reader, 
        _temporal_indexes.LOGICAL_VALID_INDEX,
        [kind_bytes, logical_id.encode("utf-8")],
        instant,
    )
    winner = None
    for locator in locators:
        version = hydrate(locator)
        if version is not None and version.commit_id <= horizon and version.valid.contains(instant):
            if winner is None or (version.commit_id, version.commit_ordinal) > (winner.commit_id, winner.commit_ordinal):
                winner = version
    if winner is None or winner.operation is VersionOperation.RETRACT:
        return None
    return winner

def iter_nodes_as_of(reader, *, valid_time, system_time=None, through_commit=None,
                       hydrate=None):
    """Iterate node versions visible at one valid and system-time point."""
    hydrate = hydrate or reader.get_version_at
    instant = normalize_temporal_instant(valid_time)
    horizon = reader.system_horizon(system_time=system_time, through_commit=through_commit)
    _temporal_indexes.ensure_temporal_indexes(
        reader.store, reader.state_key, reader.stale_key, horizon)
    logical_ids = set()
    for locator in interval_locators(reader, 
        _temporal_indexes.NODE_CATALOG_INDEX, [b"nodes"], instant
    ):
        candidate = hydrate(locator)
        if isinstance(candidate, NodeVersion) and candidate.commit_id <= horizon:
            logical_ids.add(candidate.logical_id)
    for logical_id in sorted(logical_ids):
        version = entity_as_of(reader, 
            "node", logical_id, valid_time=instant, through_commit=horizon,
            hydrate=hydrate,
        )
        if version is not None:
            yield version

def iter_edges_as_of(reader, *, edge_type=None, direction="out", source=None,
                     target=None, valid_time, system_time=None,
                     through_commit=None, hydrate=None):
    """Iterate endpoint edges visible at valid time and a system horizon."""
    hydrate = hydrate or reader.get_version_at
    if direction not in {"out", "in"}:
        raise ValueError("direction must be 'out' or 'in'")
    if direction == "out" and (source is None or target is not None):
        raise ValueError("outgoing traversal requires source and rejects target")
    if direction == "in" and (target is None or source is not None):
        raise ValueError("incoming traversal requires target and rejects source")
    if edge_type is not None and (not isinstance(edge_type, str) or not edge_type):
        raise ValueError("edge_type must be a non-empty string or None")
    horizon = reader.system_horizon(system_time=system_time, through_commit=through_commit)
    _temporal_indexes.ensure_temporal_indexes(
        reader.store, reader.state_key, reader.stale_key, horizon)
    node_id = source if direction == "out" else target
    if edge_type is None:
        index_name = (
            _temporal_indexes.EDGE_OUT_CATALOG_INDEX
            if direction == "out"
            else _temporal_indexes.EDGE_IN_CATALOG_INDEX
        )
        parts = [str(node_id).encode("utf-8")]
    else:
        index_name = _temporal_indexes.EDGE_OUT_INDEX if direction == "out" else _temporal_indexes.EDGE_IN_INDEX
        parts = [str(node_id).encode("utf-8"), edge_type.encode("utf-8")]
    instant = normalize_temporal_instant(valid_time)
    locators = interval_locators(reader, index_name, parts, instant)
    logical_ids = set()
    for locator in locators:
        candidate = hydrate(locator)
        if candidate is not None and candidate.commit_id <= horizon:
            logical_ids.add(candidate.logical_id)
    for logical_id in sorted(logical_ids):
        version = entity_as_of(reader, 
            "edge", logical_id, valid_time=valid_time, through_commit=horizon,
            hydrate=hydrate,
        )
        if version is None or (
            edge_type is not None and version.edge.properties.get("type") != edge_type
        ):
            continue
        if direction == "out" and str(version.edge.source) != str(source):
            continue
        if direction == "in" and str(version.edge.target) != str(target):
            continue
        yield version

def iter_claims_as_of(
    reader,
    *,
    valid_time,
    statement_id=None,
    subject=None,
    predicate=None,
    object=_UNSET,
    object_kind=None,
    agent=None,
    source=_UNSET,
    world=None,
    polarity=None,
    system_time=None,
    through_commit=None,
    live_store=None,
):
    """Iterate visible claims matching indexed epistemic dimensions.

    Locator reads are grouped by commit, with full commit validation per
    group rather than per row. Winners are buffered at one logical horizon.
    Live-store commit integrity is checked again on successful exhaustion;
    partially consumed iterators are not completed authenticated reads.

    ``live_store`` reports the handle's current store for the rebinding
    tripwire; reads always use the store captured when iteration began.
    """
    read_store = reader.store
    live = live_store or (lambda: reader.store)
    instant = normalize_temporal_instant(valid_time)
    horizon = reader.system_horizon(
        system_time=system_time, through_commit=through_commit
    )
    _temporal_indexes.ensure_temporal_indexes(
        reader.store, reader.state_key, reader.stale_key, horizon)
    if statement_id is not None:
        digest = statement_id.removeprefix("statement:") if isinstance(statement_id, str) else ""
        if (
            not isinstance(statement_id, str)
            or not statement_id.startswith("statement:")
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError("statement_id must be a deterministic statement ID")
    effective_object_kind = (
        ClaimObjectKind.ENTITY if object_kind is None and object is not _UNSET else object_kind
    )
    if object is not _UNSET and statement_id is None and subject is not None and predicate is not None:
        statement_id = claim_statement_id(
            subject, predicate, object, object_kind=effective_object_kind
        )
    dimension_filters = {
        "subject": subject,
        "predicate": predicate,
        "agent": agent,
        "world": world,
        "polarity": None if polarity is None else ClaimPolarity(polarity).value,
        "object_kind": (
            None
            if effective_object_kind is None
            else ClaimObjectKind(effective_object_kind).value
        ),
    }
    if source is not _UNSET:
        dimension_filters["source"] = source
    indexed_filter = next(
        ((name, value) for name, value in dimension_filters.items() if value is not None),
        None,
    )
    if statement_id is not None:
        index_name = _temporal_indexes.CLAIM_STATEMENT_INDEX
        parts = [statement_id.encode("ascii")]
    elif indexed_filter is not None:
        name, value = indexed_filter
        index_name = _temporal_indexes.CLAIM_DIMENSION_INDEX
        parts = [name.encode("ascii"), canonical_json_bytes(value)]
    else:
        index_name = _temporal_indexes.CLAIM_CATALOG_INDEX
        parts = [b"claims"]
    # This is an operation-local bulk-read protocol, not admission of live
    # cache entries as immutable. Every group still takes the existing full
    # descriptor/marker/envelope validation path. Retain only marker identity
    # and selected winners, not an unbounded second cache of whole commits.
    admitted_markers = {}

    def admit_commit(commit_id):
        if live() is not read_store:
            raise TemporalCorruptionError("claim read store changed during iteration")
        marker = read_store.get_metadata(temporal_visible_key(commit_id))
        if commit_id in admitted_markers and admitted_markers[commit_id] != marker:
            raise TemporalCorruptionError("claim read commit changed during iteration")
        commit = reader.get_commit_cached(commit_id, _validate_supersession=False)
        if marker != read_store.get_metadata(temporal_visible_key(commit_id)):
            raise TemporalCorruptionError("claim read commit changed during validation")
        admitted_markers[commit_id] = marker
        return commit

    def read_locators(locators):
        groups = {}
        for locator in locators:
            commit_id, ordinal = decode_temporal_locator(locator)
            if commit_id <= horizon:
                groups.setdefault(commit_id, set()).add(ordinal)
        for commit_id, ordinals in sorted(groups.items()):
            commit = admit_commit(commit_id)
            if commit is None:
                continue
            for ordinal in sorted(ordinals):
                if ordinal >= len(commit.versions):
                    raise TemporalCorruptionError("temporal index locator ordinal is out of range")
                yield commit.versions[ordinal]

    if horizon:
        admit_commit(horizon)
    logical_ids = set()
    for candidate in read_locators(interval_locators(reader, index_name, parts, instant)):
        if isinstance(candidate, ClaimVersion) and candidate.commit_id <= horizon:
            logical_ids.add(candidate.logical_id)

    # Resolve every candidate identity against its complete logical history,
    # not just the originally selected dimension: a later correction or
    # retraction must still win. Do not repeatedly recompute/validate the
    # latest system horizon for each logical ID.
    def logical_locators():
        for claim_id in sorted(logical_ids):
            yield from interval_locators(reader, 
                _temporal_indexes.LOGICAL_VALID_INDEX, [b"c", claim_id.encode("utf-8")], instant
            )

    winners = {}
    for version in read_locators(logical_locators()):
        if (isinstance(version, ClaimVersion) and version.logical_id in logical_ids
                and version.commit_id <= horizon and version.valid.contains(instant)):
            previous = winners.get(version.logical_id)
            if previous is None or (version.commit_id, version.commit_ordinal) > (previous.commit_id, previous.commit_ordinal):
                winners[version.logical_id] = version
    for claim_id in sorted(logical_ids):
        version = winners.get(claim_id)
        if version is None or version.operation is VersionOperation.RETRACT or version.claim is None:
            continue
        claim = version.claim
        if statement_id is not None and claim.statement_id != statement_id:
            continue
        if any(
            value is not None and getattr(claim, name) != value
            for name, value in dimension_filters.items()
        ):
            continue
        if source is not _UNSET and claim.source != source:
            continue
        if object is not _UNSET and claim.object != Claim(
            subject=claim.subject,
            predicate=claim.predicate,
            object=object,
            object_kind=effective_object_kind,
            polarity=claim.polarity,
            agent=claim.agent,
            source=claim.source,
            world=claim.world,
        ).object:
            continue
        yield version

    # No successful complete pass may hide damage/rewrite/removal of an
    # already touched commit, including an unrelated row in that commit.
    # Appends beyond the captured horizon do not change these markers.
    for commit_id in admitted_markers:
        admit_commit(commit_id)

def claim_status(
    reader,
    subject,
    predicate,
    object,
    *,
    valid_time,
    object_kind=ClaimObjectKind.ENTITY,
    agent=None,
    source=_UNSET,
    world=None,
    system_time=None,
    through_commit=None,
) -> ClaimStatus:
    """Return supported/refuted/both/unknown under open-world semantics."""
    polarities = {
        version.claim.polarity
        for version in iter_claims_as_of(reader, 
            valid_time=valid_time,
            statement_id=claim_statement_id(
                subject, predicate, object, object_kind=object_kind
            ),
            agent=agent,
            source=source,
            world=world,
            system_time=system_time,
            through_commit=through_commit,
        )
    }
    if polarities == {ClaimPolarity.POSITIVE, ClaimPolarity.NEGATIVE}:
        return ClaimStatus.BOTH
    if ClaimPolarity.POSITIVE in polarities:
        return ClaimStatus.SUPPORTED
    if ClaimPolarity.NEGATIVE in polarities:
        return ClaimStatus.REFUTED
    return ClaimStatus.UNKNOWN



# -- version history iteration ------------------------------------------

def _iter_versions_by_logical_id(reader, kind, kind_byte, logical_id, *,
                                 system_time=None, through_commit=None):
    logical_id = normalize_logical_id(logical_id)
    horizon = reader.system_horizon(system_time=system_time, through_commit=through_commit)
    _temporal_indexes.ensure_temporal_indexes(
        reader.store, reader.state_key, reader.stale_key, horizon)
    end = temporal_locator(horizon, (1 << 32) - 1)
    for locator in reader.store.iter_range_index(
        _temporal_indexes.index_name(
            _temporal_indexes.LOGICAL_COMMIT_INDEX,
            _temporal_indexes.index_state_generation(reader.store, reader.state_key),
        ),
        [kind_byte, logical_id.encode("utf-8")],
        None,
        end,
        True,
        True,
    ):
        version = reader.get_version_at(locator)
        if isinstance(version, kind):
            yield version

def iter_node_versions(reader, logical_id=None, *, system_time=None,
                       through_commit=None):
    """Iterate node versions, using the logical-history index when filtered."""
    if logical_id is None:
        horizon = reader.system_horizon(system_time=system_time, through_commit=through_commit)
        for commit in reader.iter_commits(through_commit=horizon):
            for version in commit.versions:
                if isinstance(version, NodeVersion):
                    yield version
        return
    yield from _iter_versions_by_logical_id(reader, 
        NodeVersion, b"n", logical_id, system_time=system_time,
        through_commit=through_commit)

def iter_edge_versions(reader, logical_id=None, *, system_time=None,
                       through_commit=None):
    """Iterate edge versions, using the logical-history index when filtered."""
    if logical_id is None:
        horizon = reader.system_horizon(system_time=system_time, through_commit=through_commit)
        for commit in reader.iter_commits(through_commit=horizon):
            for version in commit.versions:
                if isinstance(version, EdgeVersion):
                    yield version
        return
    yield from _iter_versions_by_logical_id(reader, 
        EdgeVersion, b"e", logical_id, system_time=system_time,
        through_commit=through_commit)

def iter_claim_versions(reader, claim_id=None, *, system_time=None,
                        through_commit=None):
    """Iterate claim versions, optionally restricted to one deterministic claim ID."""
    if claim_id is None:
        horizon = reader.system_horizon(system_time=system_time, through_commit=through_commit)
        for commit in reader.iter_commits(through_commit=horizon):
            for version in commit.versions:
                if isinstance(version, ClaimVersion):
                    yield version
        return
    yield from _iter_versions_by_logical_id(reader, 
        ClaimVersion, b"c", claim_id, system_time=system_time,
        through_commit=through_commit)

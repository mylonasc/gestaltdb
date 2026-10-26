"""Explicit native index locators with complete historical coverage admission.

Prefix pushdown reduces native row transfer, not complete catalog authentication.
The default full-record and ordinary sources remain unchanged.
"""

from dataclasses import dataclass

from ..._graph.temporal_indexes import (
    CLAIM_DIMENSION_INDEX as _TEMPORAL_CLAIM_DIMENSION_INDEX,
    LOGICAL_VALID_INDEX as _TEMPORAL_LOGICAL_VALID_INDEX,
)
from ..._graph.temporal_keys import temporal_instant_index_value
from ...kvstores import _range_index_key, _range_index_prefix
from ...readview import ProvenanceMismatchError
from ...versioning import ClaimVersion, VersionOperation, canonical_json_bytes
from ..model import ReasoningError, ReasoningLimitError, check_cancelled
from .native import NativeKeyScan
from .native_claims import NativeClaimLimits, PinnedNativeClaimSource


@dataclass(frozen=True)
class NativeLocatorLimits(NativeClaimLimits):
    """Cumulative native index rows plus an independent full-catalog record cap."""

    max_authenticated_records: int = 1_000_000


@dataclass(frozen=True)
class NativeLocatorProfile:
    authenticated_records: int
    raw_index_rows: int
    candidate_claims: int
    materialized_records: int
    resolved_claims: int
    scans: int
    scan_mode: str = "native-dimension-intersection+logical-history; full-catalog-authentication"


class PinnedNativeLocatorClaimSource(PinnedNativeClaimSource):
    """Borrow one point view; admit native locators before materializing evidence.

    Predicate alternatives and subject bindings use separate native prefixes;
    their candidate identities intersect with the world prefix. Complete logical
    histories resolve corrections/retractions before any output policy filter.
    Full catalog authentication and grouped canonical conformance are retained:
    this is not a fast cache, zero-decode source or measured acceleration claim.
    """

    _physical_pushdown = "native-world/predicate/subject-prefixes; object/policy-post-selection"

    def __init__(self, view, *, native_limits=NativeLocatorLimits(), **kwargs):
        if not isinstance(native_limits, NativeLocatorLimits):
            raise ReasoningError("native_limits must be NativeLocatorLimits")
        super().__init__(view, native_limits=native_limits, **kwargs)

    def _read_winners(self, cancel):
        temporal, limits = self._graph.temporal, self._native_limits
        horizon = self.provenance.commit_horizon
        if horizon > limits.max_commits:
            raise ReasoningLimitError("native locator extraction exceeded max_commits")
        temporal.ensure_indexes(horizon)
        generation = temporal.index_generation()
        catalog, histories, all_winners = {}, {}, {}
        authenticated_records = 0
        for commit_id in range(1, horizon + 1):
            check_cancelled(cancel)
            self._check_binding()
            commit = temporal.get_commit_cached(commit_id)
            if commit is None:
                continue
            # Commit decoding/verification is atomic with respect to this cap;
            # the cap does not interrupt a backend operation or bound its RSS.
            authenticated_records += len(commit.versions)
            if authenticated_records > limits.max_authenticated_records:
                raise ReasoningLimitError("native locator extraction exceeded max_authenticated_records")
            for version in commit.versions:
                check_cancelled(cancel)
                if isinstance(version, ClaimVersion):
                    locator = temporal.locator(commit_id, version.commit_ordinal)
                    catalog[locator] = version
                    histories.setdefault(version.logical_id, []).append((locator, version))
                    if version.valid.contains(self._time):
                        all_winners[version.logical_id] = version
                        if len(all_winners) > limits.max_logical_claims:
                            raise ReasoningLimitError("native locator extraction exceeded max_logical_claims")

        raw_rows = scans = materialized = 0
        point = temporal_instant_index_value(self._time)
        snapshot = self._native_handle.create_snapshot()
        try:
            def scan_index(base_name, parts, expected):
                nonlocal raw_rows, scans
                check_cancelled(cancel)
                self._check_binding()
                name = temporal.index_name(base_name, generation)
                prefix = _range_index_prefix(name, parts)
                # Fixed-width sortable start, followed by 0x1f and locator.
                # Include every start equal to the point, but no later start.
                stop = prefix + point + b"\x20"
                remaining = dict(expected)
                found = []
                previous = None
                scans += 1
                with NativeKeyScan(self._native_store, prefix=prefix, stop=stop, snapshot=snapshot,
                        batch_rows=limits.batch_rows, max_batch_bytes=limits.max_batch_bytes,
                        max_rows=limits.max_raw_rows) as scan:
                    for batch in scan:
                        for key, value in batch.rows:
                            check_cancelled(cancel)
                            self._check_binding()
                            raw_rows += 1
                            if raw_rows > limits.max_raw_rows:
                                raise ReasoningLimitError("native locator extraction exceeded max_raw_rows")
                            if not prefix <= key < stop or not key.startswith(prefix) or (previous is not None and key <= previous):
                                raise ProvenanceMismatchError("native locator keys escaped bounds or are unordered/duplicated")
                            previous = key
                            try:
                                commit_id, ordinal = temporal.decode_locator(value)
                            except (TypeError, ValueError) as exc:
                                raise ProvenanceMismatchError("native locator payload is malformed") from exc
                            if commit_id < 1:
                                raise ProvenanceMismatchError("native locator commit is invalid")
                            if commit_id > horizon:
                                continue  # Later appends never enter the old pinned source.
                            version = catalog.get(value)
                            if version is None:
                                # No visible claim can authenticate this locator;
                                # unlike raw record gaps, index rows must belong.
                                raise ProvenanceMismatchError("native locator is not an authenticated claim member")
                            if remaining.pop(key, None) != value:
                                raise ProvenanceMismatchError("native locator differs from authenticated index membership")
                            found.append(version)
                if remaining:
                    raise ProvenanceMismatchError("native locator scan omitted authenticated index membership")
                return found

            def expected_dimension(dimension, value):
                name = temporal.index_name(_TEMPORAL_CLAIM_DIMENSION_INDEX, generation)
                parts = [dimension.encode("ascii"), canonical_json_bytes(value)]
                expected = {}
                for locator, version in catalog.items():
                    check_cancelled(cancel)
                    if (version.claim is not None and version.valid.start <= self._time
                            and getattr(version.claim, dimension) == value):
                        key = _range_index_key(name, parts,
                            temporal_instant_index_value(version.valid.start), locator)
                        expected[key] = locator
                return parts, expected

            def dimension_ids(dimension, value):
                parts, expected = expected_dimension(dimension, value)
                return {v.logical_id for v in scan_index(_TEMPORAL_CLAIM_DIMENSION_INDEX, parts, expected)
                        if v.valid.contains(self._time)}

            ids = dimension_ids("world", self._request.world) if horizon else set()
            if self._request.predicates is not None:
                predicates = set()
                for predicate in self._request.predicates:
                    predicates.update(dimension_ids("predicate", predicate))
                ids.intersection_update(predicates)
            if self._request.subject is not None:
                ids.intersection_update(dimension_ids("subject", self._request.subject))
            selected = {}
            logical_name = temporal.index_name(_TEMPORAL_LOGICAL_VALID_INDEX, generation)
            for logical_id in sorted(ids):
                check_cancelled(cancel)
                parts = [b"c", logical_id.encode("utf-8")]
                expected = {
                    _range_index_key(logical_name, parts,
                        temporal_instant_index_value(v.valid.start), locator): locator
                    for locator, v in histories[logical_id]
                    if v.valid.start <= self._time
                }
                winner = None
                for version in scan_index(_TEMPORAL_LOGICAL_VALID_INDEX, parts, expected):
                    check_cancelled(cancel)
                    self._check_binding()
                    key = temporal.record_key(version.commit_id, version.commit_ordinal)
                    payload = self._native_store.get_metadata(key)
                    if payload is None or temporal.decode_version(payload) != version:
                        raise ProvenanceMismatchError("point-materialized locator differs from authenticated version")
                    materialized += 1
                    if version.valid.contains(self._time) and (winner is None or
                            (version.commit_id, version.commit_ordinal) > (winner.commit_id, winner.commit_ordinal)):
                        winner = version
                if winner != all_winners.get(logical_id):
                    raise ProvenanceMismatchError("native logical history differs from authenticated winner")
                if winner is not None and winner.operation is not VersionOperation.RETRACT:
                    selected[logical_id] = winner
        finally:
            snapshot.release()

        # Retain canonical conformance over the full point input, not only the
        # pushed subset. Missing canonical indexes must never silently pass.
        iterator = self._pinned.iter_claims_as_of(valid_time=self._time)
        try:
            while True:
                check_cancelled(cancel)
                self._check_binding()
                try:
                    version = next(iterator)
                except StopIteration:
                    break
                check_cancelled(cancel)
                self._check_binding()
                if not isinstance(version, ClaimVersion):
                    raise ProvenanceMismatchError("canonical locator conformance yielded invalid version")
                expected = all_winners.pop(version.logical_id, None)
                if expected != version or version.operation is VersionOperation.RETRACT:
                    raise ProvenanceMismatchError("canonical selection differs from authenticated locator history")
        finally:
            close = getattr(iterator, "close", None)
            if close is not None:
                close()
        if any(v.operation is not VersionOperation.RETRACT for v in all_winners.values()):
            raise ProvenanceMismatchError("canonical selection omitted authenticated locator history")
        self.verify_source()
        check_cancelled(cancel)
        return tuple(selected[key] for key in sorted(selected)), NativeLocatorProfile(
            authenticated_records, raw_rows, len(ids), materialized, len(selected), scans)

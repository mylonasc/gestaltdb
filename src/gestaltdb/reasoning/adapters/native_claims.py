"""Explicit authenticated native candidate scan with canonical point selection.

Correctness-first full temporal-record scan, not index-locator acceleration.
Native rows never become evidence until commit membership, complete coverage,
canonical historical winner selection and the shared claim policy all pass.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ...epistemic import ClaimObjectKind, ClaimPolarity
from ..._graph.temporal_keys import _TEMPORAL_RECORD_PREFIX
from ...kvstores import PyRexStore
from ...readview import ProvenanceMismatchError
from ...versioning import ClaimVersion, VersionOperation
from ..model import ReasoningError, ReasoningLimitError, check_cancelled
from .batched import PinnedBatchedClaimSource
from .native import NativeKeyScan, _require_pyarrow, require_native_scan


@dataclass(frozen=True)
class NativeClaimLimits:
    """Physical scan bounds, separate from logical request/output limits.

    Authentication and canonical point-index internals are not bounded by these
    counters. Native calls, lock acquisition and backend reads are cooperative
    cancellation boundaries, not interruptible deadlines or hard RSS limits.
    """

    max_raw_rows: int = 1_000_000
    max_logical_claims: int = 100_000
    max_commits: int = 100_000
    batch_rows: int = 1024
    max_batch_bytes: int = 8 * 1024 * 1024

    def __post_init__(self):
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ReasoningError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class NativeClaimProfile:
    raw_rows: int
    visible_records: int
    logical_claims: int
    resolved_claims: int
    scan_mode: str = "full-records; grouped-canonical-point-resolution; post-selection-filters"


class PinnedNativeClaimSource(PinnedBatchedClaimSource):
    """Borrow a temporal view, scan plain PyRex, reuse canonical point semantics.

    Same request/policy/mapping/context and FactBatch contract as the ordinary
    batched source. Scans all entity kinds/operations in the pinned record
    prefix; filters run only after winner selection. Cooperating temporal writers
    wait during extraction. Old prefixes remain historical after later appends.
    No implicit source creation, index repair, publication or native adoption.
    A completed pass is authenticated; partially consumed batches are not a
    successful complete read. Close abandoned iterators to release the guard.

    Requires plain PyRex and Arrow, an existing temporal read view, an extraction
    request, and explicit selection/mapping policies. See the runnable example
    in :doc:`reasoning-native-claims` for their construction, bounded batches,
    exact version evidence, and resource lifetimes. For first use without native
    scanning, start with :doc:`ontology` or :doc:`reasoning-extraction`.
    """

    _physical_pushdown = "none; native-full-records; post-selection-filters"

    def __init__(self, view, *, native_limits=NativeClaimLimits(), **kwargs):
        if not isinstance(native_limits, NativeClaimLimits):
            raise ReasoningError("native_limits must be NativeClaimLimits")
        # Base initialization validates the exact view and policy. Capture handle
        # objects after it; verify_source's first call uses only base checks.
        self._native_store = None
        super().__init__(view, **kwargs)
        if not isinstance(self._graph.store, PyRexStore) or self._graph.store.transactional:
            raise ReasoningError("native claim extraction requires plain PyRex")
        self._native_store, self._native_serializer = self._graph.store, self._graph.serializer
        self._native_handle = require_native_scan(self._native_store)
        _require_pyarrow()
        self._native_limits = native_limits
        self._active = False
        self._native_profile = None
        self._check_binding()

    def _check_binding(self):
        super()._check_binding()
        if self._native_store is not None and (
            self._graph.store is not self._native_store
            or self._graph.serializer is not self._native_serializer
            or self._native_store.db is not self._native_handle
        ):
            raise ProvenanceMismatchError("native source store/decoder/handle was rebound")

    def _read_winners(self, cancel):
        graph, limits = self._graph, self._native_limits
        horizon = self.provenance.commit_horizon
        if horizon > limits.max_commits:
            raise ReasoningLimitError("native claim extraction exceeded max_commits")
        expected = {}
        for commit_id in range(1, horizon + 1):
            check_cancelled(cancel)
            self._check_binding()
            commit = graph.temporal.get_commit_cached(commit_id)
            if commit is not None:
                expected[commit_id] = len(commit.versions)
        coverage = dict.fromkeys(expected, 0)
        candidates = {}
        raw_rows, visible_records = 0, 0
        prefix = self._native_store._key(b"M", _TEMPORAL_RECORD_PREFIX)
        previous_key = None
        current_id, current_commit = None, None
        if horizon:
            stop = self._native_store._key(b"M", graph.temporal.record_key(horizon + 1, 0))
            with NativeKeyScan(self._native_store, prefix=prefix, stop=stop,
                    batch_rows=limits.batch_rows, max_batch_bytes=limits.max_batch_bytes,
                    max_rows=limits.max_raw_rows) as scan:
                for batch in scan:
                    check_cancelled(cancel)
                    self._check_binding()
                    for key, value in batch.rows:
                        check_cancelled(cancel)
                        raw_rows += 1
                        if not key.startswith(prefix) or len(key) != len(prefix) + 12:
                            raise ProvenanceMismatchError("native temporal record key is invalid")
                        if previous_key is not None and key <= previous_key:
                            raise ProvenanceMismatchError("native temporal records are duplicated or unordered")
                        previous_key = key
                        commit_id = int.from_bytes(key[len(prefix):len(prefix) + 8], "big")
                        ordinal = int.from_bytes(key[-4:], "big")
                        if not 0 < commit_id <= horizon:
                            raise ProvenanceMismatchError("native temporal record escaped the pinned prefix")
                        if commit_id not in expected:
                            # Unmarked interrupted writes/reserved gaps are not
                            # visible evidence. Never repair or publish them.
                            continue
                        if commit_id != current_id:
                            current_id = commit_id
                            current_commit = graph.temporal.get_commit_cached(commit_id)
                        if current_commit is None or ordinal >= len(current_commit.versions):
                            raise ProvenanceMismatchError("native record is not an authenticated commit member")
                        # Compare exact serialized bytes, not Node/Edge payload
                        # object identity. Canonical admission already decoded
                        # and authenticated every member of this commit.
                        if value != self._native_store.get_metadata(graph.temporal.record_key(commit_id, ordinal)):
                            raise ProvenanceMismatchError("native record differs from authenticated commit")
                        version = current_commit.versions[ordinal]
                        if version.commit_id != commit_id or version.commit_ordinal != ordinal:
                            raise ProvenanceMismatchError("native record key/ordinal disagrees with its envelope")
                        coverage[commit_id] += 1
                        visible_records += 1
                        if isinstance(version, ClaimVersion) and version.valid.contains(self._time):
                            # Raw keys are strictly ordered by commit/ordinal;
                            # retain the final eligible record as a conformance
                            # check against canonical logical-index resolution.
                            candidates[version.logical_id] = version
                            if len(candidates) > limits.max_logical_claims:
                                raise ReasoningLimitError("native claim extraction exceeded max_logical_claims")
        if coverage != expected:
            raise ProvenanceMismatchError("native scan omitted authenticated temporal records")
        candidate_count = len(candidates)
        winners = []
        # One existing grouped canonical point read, not N independently
        # authenticated get_claim_as_of operations. The canonical iterator still
        # validates complete commit groups on entry and successful exhaustion;
        # native coverage independently checks omissions from its indexes.
        iterator = iter(self._pinned.iter_claims_as_of(valid_time=self._time))
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
                raise ProvenanceMismatchError("canonical point selection yielded an invalid claim version")
            expected_winner = candidates.pop(version.logical_id, None)
            if (expected_winner is None or expected_winner.operation is VersionOperation.RETRACT
                    or version != expected_winner):
                raise ProvenanceMismatchError("canonical point selection differs from complete native history")
            winners.append(version)
        if any(version.operation is not VersionOperation.RETRACT for version in candidates.values()):
            raise ProvenanceMismatchError("canonical point selection omitted complete native history")
        self.verify_source()
        check_cancelled(cancel)
        return tuple(winners), NativeClaimProfile(raw_rows, visible_records, candidate_count, len(winners))

    def _iter_versions(self, query, cancel=None):
        for version in self._native_versions:
            check_cancelled(cancel)
            claim = version.claim
            if (claim.world == self._request.world and claim.polarity is ClaimPolarity.POSITIVE
                    and claim.object_kind is ClaimObjectKind.ENTITY
                    and all(getattr(claim, name) == value for name, value in query.items())):
                yield version

    def _filtered(self, cancel=None):
        check_cancelled(cancel)
        self.verify_source()
        self._native_versions, profile = self._read_winners(cancel)
        yield from super()._filtered(cancel)
        self.verify_source()
        check_cancelled(cancel)
        self._completed_native_profile = profile

    def iter_batches(self, *, cancel=None):
        check_cancelled(cancel)
        self._check_binding()
        if self._active:
            raise ReasoningError("native source already has an active extraction pass")
        self._active = True
        self._native_profile = None
        self.__dict__.pop("_profile", None)
        try:
            with self._graph.temporal.write_guard():
                yield from super().iter_batches(cancel=cancel)
                self._profile = replace(self._profile, pushdown=self._physical_pushdown)
                self._native_profile = self._completed_native_profile
        finally:
            self.__dict__.pop("_native_versions", None)
            self._active = False

    @property
    def native_profile(self):
        if self._native_profile is None:
            raise ReasoningError("no completed native extraction pass yet")
        return self._native_profile

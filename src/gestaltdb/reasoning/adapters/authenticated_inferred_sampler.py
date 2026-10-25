"""Source-authenticated admission for the existing inferred sampler consumer.

Hydration independently reconstructs the complete artifact, including CSR and
policy arrays. Self-consistent checksums alone cannot admit altered topology.
This is deliberately expensive admission, not a fast cache hit or closure proof.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from ...sampling.snapshot import SamplerSnapshot
from ..model import ReasoningError, ReasoningLimitError, check_cancelled, _text
from .authenticated_sessions import _validate
from .inferred_sampler import _project_inferred
from .session_generation import SessionAdmissionLimits, bind_session_generation
from .session_store import _digest, _session_file_guard, session_to_dict


@dataclass(frozen=True)
class InferredSnapshotSelection:
    """Explicit artifact projection; separate from the source claim policy."""

    node_predicate: str = "person"
    edge_predicates: tuple[str, ...] = ("memberOf",)

    def __post_init__(self):
        _text(self.node_predicate, "node_predicate")
        if not isinstance(self.edge_predicates, (tuple, list, set, frozenset)) or not self.edge_predicates:
            raise ReasoningError("edge_predicates must be a nonempty explicit collection")
        object.__setattr__(self, "edge_predicates", tuple(sorted({_text(p, "edge predicate") for p in self.edge_predicates})))


@dataclass(frozen=True)
class InferredSnapshotLimits:
    """Projection cardinality and serialized artifact-byte bounds, not hard RSS."""

    max_nodes: int = 100_000
    max_edges: int = 100_000
    max_bytes: int = 256_000_000

    def __post_init__(self):
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ReasoningError(f"{name} must be a positive integer")


def _admit(graph, session, expected_generation, expected_session_digest, freshness, admission_limits, cancel):
    _validate(graph, expected_generation, expected_session_digest, admission_limits, freshness, cancel)
    binding = bind_session_generation(graph, session, freshness=freshness, limits=admission_limits, cancel=cancel)
    expected_generation.require_compatible(binding.identity)
    if binding.session_digest != expected_session_digest:
        raise ReasoningError("session state does not match expected_session_digest")
    return binding


def _reconstruct(session, binding, selection, limits, path, cancel):
    arrays, counts = _project_inferred(session, selection.node_predicate, selection.edge_predicates,
        cancel=cancel, max_nodes=limits.max_nodes, max_edges=limits.max_edges)
    # JSON normalize tuple/list distinctions without adopting artifact metadata.
    contract = json.loads(json.dumps({
        "format_version": 1,
        "generation": asdict(binding.identity),
        "provenance": asdict(binding.provenance),
        "scope": session_to_dict(session)["scope"],
        "session_digest": binding.session_digest,
        "selection": asdict(selection),
        "projection": "inferred-source-endpoints-unit-weight-v1",
        **counts,
    }))
    check_cancelled(cancel)
    snapshot = SamplerSnapshot.from_edge_arrays(path, **arrays, metadata={
        "authenticated_reasoning_generation": contract,
        "snapshot_id": _digest(contract),
    })
    _files(path, limits, cancel)
    check_cancelled(cancel)
    return snapshot


def _files(path, limits, cancel):
    """Only bounded, plain files; no unlisted sidecars or symlink traversal."""
    if path.is_symlink() or not path.is_dir():
        raise ReasoningError("inferred snapshot must be a plain directory")
    result = {}
    total = 0
    for entry in path.iterdir():
        check_cancelled(cancel)
        if entry.is_symlink() or not entry.is_file():
            raise ReasoningError("inferred snapshot contains a non-regular artifact")
        total += entry.stat().st_size
        if total > limits.max_bytes:
            raise ReasoningLimitError("inferred snapshot exceeded max_bytes")
        result[entry.name] = entry
        if len(result) > 128:
            raise ReasoningLimitError("inferred snapshot exceeded artifact count limit")
    return result


def _compare_files(actual, expected, limits, cancel):
    actual_files, expected_files = _files(actual, limits, cancel), _files(expected, limits, cancel)
    if actual_files.keys() != expected_files.keys():
        raise ReasoningError("inferred snapshot artifact set mismatch")
    total = 0
    for name in sorted(expected_files):
        with actual_files[name].open("rb") as left, expected_files[name].open("rb") as right:
            while True:
                check_cancelled(cancel)
                a, b = left.read(65_536), right.read(65_536)
                total += len(a)
                if total > limits.max_bytes:
                    raise ReasoningLimitError("inferred snapshot exceeded max_bytes")
                if a != b:
                    raise ReasoningError(f"inferred snapshot metadata/array mismatch: {name}")
                if not a:
                    break


def _options(selection, limits):
    if not isinstance(selection, InferredSnapshotSelection) or not isinstance(limits, InferredSnapshotLimits):
        raise ReasoningError("InferredSnapshotSelection and InferredSnapshotLimits required")


def build_authenticated_inferred_snapshot(graph, session, output_path, *, expected_generation,
        expected_session_digest, selection=InferredSnapshotSelection(), freshness="latest",
        admission_limits=SessionAdmissionLimits(), limits=InferredSnapshotLimits(), cancel=None):
    """Authenticate exact input, stage a complete v2 snapshot, install at a NEW path.

    Cooperating calls serialize by canonical path; no existing directory (even
    empty) is replaced. Cancellation/failure before rename removes only own staging.
    The database writer guard spans source admission and final freshness check.

    This consumes an existing reasoning session, caller-held generation/state
    pins, and an explicit projection selection. The complete runnable workflow
    in :doc:`reasoning-authenticated-inferred` creates those inputs, builds the
    artifact, verifies a reload, and samples its derived relations. Use a fresh
    output directory for each build.
    """
    _options(selection, limits)
    _validate(graph, expected_generation, expected_session_digest, admission_limits, freshness, cancel)
    target = Path(output_path)
    store, serializer = graph.store, graph.serializer
    with graph._temporal_writer_guard(), _session_file_guard(target):
        if target.exists() or target.is_symlink():
            raise ReasoningError("output_path already exists; use a new path per generation")
        binding = _admit(graph, session, expected_generation, expected_session_digest, freshness, admission_limits, cancel)
        with TemporaryDirectory(prefix=f".{target.name}.inferred.", dir=target.parent) as root:
            staged = Path(root) / "snapshot"
            snapshot = _reconstruct(session, binding, selection, limits, staged, cancel)
            check_cancelled(cancel)
            binding.source_binding.verify_source(graph, freshness=freshness)
            if graph.store is not store or graph.serializer is not serializer:
                raise ReasoningError("inferred source store/decoder was rebound")
            if target.exists() or target.is_symlink():
                raise ReasoningError("output_path appeared during build; use a new path")
            os.rename(staged, target)
        return replace(snapshot, path=target)


def load_authenticated_inferred_snapshot(graph, session, path, *, expected_generation,
        expected_session_digest, selection=InferredSnapshotSelection(), freshness="latest",
        admission_limits=SessionAdmissionLimits(), limits=InferredSnapshotLimits(), cancel=None):
    """Re-admit source/session, reconstruct and byte-compare EVERY artifact.

    Stored identity/checksums are never adopted as caller authority. Eager load
    only: a returned in-memory snapshot does not depend on future file changes.
    Requires immutable cooperating artifacts; hostile filesystem mutation is out
    of contract. No files in the supplied artifact are written or repaired.
    """
    _options(selection, limits)
    _validate(graph, expected_generation, expected_session_digest, admission_limits, freshness, cancel)
    target = Path(path)
    store, serializer = graph.store, graph.serializer
    with graph._temporal_writer_guard():
        binding = _admit(graph, session, expected_generation, expected_session_digest, freshness, admission_limits, cancel)
        _files(target, limits, cancel)
        with TemporaryDirectory(prefix="gestaltdb-inferred-admission-") as root:
            expected = Path(root) / "snapshot"
            snapshot = _reconstruct(session, binding, selection, limits, expected, cancel)
            _compare_files(target, expected, limits, cancel)
        check_cancelled(cancel)
        binding.source_binding.verify_source(graph, freshness=freshness)
        if graph.store is not store or graph.serializer is not serializer:
            raise ReasoningError("inferred source store/decoder was rebound")
        # Return trusted eager reconstruction, not a second read of caller files
        # after comparison. The admitted artifact location remains the public path.
        return replace(snapshot, path=target)
